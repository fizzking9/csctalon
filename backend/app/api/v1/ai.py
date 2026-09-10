"""AI 分析助手路由 (A-20260904-003 LOCKED v1.0)

一期=自由问答:
- 数据: 内部 service 直连聚合(ai_feed._build_feed = external._build_brief 同底层同缓存)
- 口径: ai_glossary 口径卡随 Prompt 注入
- 模型: llm_client(OpenAI 兼容, 一期 deepseek-v4-flash, 可切本地)
- 任务: AiTaskManager 内存任务 + 轮询
- 权限: ManagerUser; LLM_ENABLED=false → 503
"""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, DBSession, ManagerUser
from app.core.config import settings
from app.core.database import SessionLocal
from app.schemas.ai_focus import AiFocusCreate, AiFocusUpdate
from app.schemas.common import R
from app.services import ai_feed, ai_focus_service, llm_client
from app.services.ai_task_manager import ai_task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI 分析助手"])


def _require_enabled() -> None:
    """LLM_ENABLED=false → 503(仿 EXTERNAL_API_ENABLED 模式)。"""
    if not settings.LLM_ENABLED:
        raise HTTPException(503, "AI 分析助手未启用(LLM_ENABLED=false); 请在 .env 配置 LLM_* 后开启")


class AiAnalyzeRequest(BaseModel):
    """自由问答入参。"""
    question: str = Field(..., min_length=1, max_length=500, description="经营数据问题(如: 今日退款和涉诉如何?)")
    period: Literal["daily", "weekly", "monthly"] = Field("daily", description="数据窗口: daily/weekly/monthly")
    date: str | None = Field(None, description="period=daily 可指定某整天 YYYY-MM-DD(缺省=今天实时)")
    # v1.1(2026-09-07): 任意区间(与 external brief 同源同护栏; 同时给出则优先于 period/date)
    start: str | None = Field(None, description="任意区间起点, 仅纯日期 YYYY-MM-DD(与 end 成对; 历史标准周回看)")
    end: str | None = Field(None, description="任意区间终点, 仅纯日期 YYYY-MM-DD(与 start 成对; end=今天则实时)")


class _AiWindow:
    """解析并校验 AI 查询窗口: period/date 或 start/end(与 external._window 同规则)。"""

    def __init__(self, payload: AiAnalyzeRequest):
        self.period = payload.period
        self.date = payload.date
        self.start = payload.start
        self.end = payload.end
        self._validate()

    def _validate(self) -> None:
        import datetime as _dt
        if self.date:
            parts = self.date.split("-")
            if len(parts) != 3 or not all(len(p) == (4 if i == 0 else 2) and p.isdigit() for i, p in enumerate(parts)):
                raise HTTPException(400, f"date 参数非法: {self.date!r}, 应为 YYYY-MM-DD")
        # start/end 成对校验(纯日期)
        if self.start is not None or self.end is not None:
            if self.start is None or self.end is None:
                raise HTTPException(400, "start/end 必须成对提供(YYYY-MM-DD)")
            for name, val in (("start", self.start), ("end", self.end)):
                try:
                    _dt.date.fromisoformat(val.strip())
                except (ValueError, TypeError):
                    raise HTTPException(400, f"{name} 参数非法: {val!r}, 应为 YYYY-MM-DD")

    def effective(self) -> dict:
        """返回供 ai_feed 使用的窗口参数(仅当显式给了 start/end 才透传)。"""
        if self.start is not None or self.end is not None:
            return {"period": self.period, "date": None, "start": self.start, "end": self.end}
        return {"period": self.period, "date": self.date, "start": None, "end": None}


@router.get("/config", response_model=R, summary="AI 助手启用状态与快捷问(内部)")
def ai_config(_: ManagerUser):
    """返回助手是否启用 + 可用 period + 快捷触发句(前端初始化用)。"""
    return R(data={
        "enabled": settings.LLM_ENABLED,
        "base_configured": bool(settings.LLM_BASE_URL and settings.LLM_MODEL),
        "periods": ai_feed.available_scope_options(),
        "notice": "数据基于内部聚合(与对外简报同口径); 输出由 AI 生成, 仅供参考",
    })


@router.post("/analyze", response_model=R, summary="提交 AI 经营数据分析任务(自由问答)")
async def ai_analyze(payload: AiAnalyzeRequest, _: ManagerUser):
    """提交分析任务, 立即返回 task_id(生成可能数十秒, 前端轮询 GET /ai/tasks/{id})。"""
    _require_enabled()
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(400, "question 不能为空")
    # 窗口解析(period/date 或 start/end 成对校验)
    win = _AiWindow(payload)
    eff = win.effective()

    async def _runner(task):
        """后台执行: 建独立 Session 组装 feed → 调 LLM → 写回 task。"""
        try:
            db = SessionLocal()
            try:
                messages = ai_feed.build_messages(
                    db, eff["period"], eff["date"], q,
                    start=eff["start"], end=eff["end"],
                )
            finally:
                db.close()
            # 记录注入的 data 规模日志(不含明细)
            logger.info("ai task %s: period=%s date=%s start=%s end=%s question_len=%d",
                        task.task_id, eff["period"], eff["date"], eff["start"], eff["end"], len(q))
            result = await llm_client.chat_completion(
                messages,
                max_tokens=int(getattr(settings, "AI_MAX_TOKENS", 1500) or 1500),
            )
            task.result_md = result
        except llm_client.LLMError as e:
            task.error = str(e)
            raise
        except Exception as e:  # noqa: BLE001
            task.error = f"分析失败: {e}"
            raise

    task_id = ai_task_manager.submit(q, eff["period"], eff["date"], _runner,
                                     start=eff["start"], end=eff["end"])
    # 首查同步等待(2026-09-04 提速): AI 任务通常 3~6s 完成, 前端若走 2s 轮询需等
    # 8~10s 才有输出。这里最多等 AI_SYNC_WAIT_S 秒直接拿结果; 超时才返回 task_id 走轮询兜底。
    wait_s = float(getattr(settings, "AI_SYNC_WAIT_S", 6.0) or 6.0)
    deadline = asyncio.get_event_loop().time() + wait_s
    while True:
        task = ai_task_manager.get(task_id)
        if task is not None and task.status in ("success", "failed"):
            break
        if asyncio.get_event_loop().time() >= deadline:
            break
        await asyncio.sleep(0.15)
    task = ai_task_manager.get(task_id)
    if task is not None and task.status == "success":
        # 一次请求直接返回结果, 前端无需轮询
        return R(data={"task_id": task_id, "status": "success", "result_md": task.result_md})
    if task is not None and task.status == "failed":
        return R(data={"task_id": task_id, "status": "failed", "error": task.error})
    # 超时未完成 → 回 task_id 走轮询
    return R(data={"task_id": task_id, "status": "running", "message": "已提交, 请轮询 GET /api/v1/ai/tasks/{task_id}"})


@router.get("/tasks/{task_id}", response_model=R, summary="查询 AI 分析任务结果(轮询)")
def ai_task_status(task_id: str, _: ManagerUser):
    """轮询: 终态返回 result_md; queued/running 返回进度; 不存在 404。"""
    task = ai_task_manager.get(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在或已过期")
    return R(data=task.to_public_dict())


@router.get("/tasks", response_model=R, summary="最近 AI 分析任务(调试/展示)")
def ai_task_latest(_: ManagerUser):
    latest = ai_task_manager.get_latest()
    return R(data={"latest": latest.to_public_dict() if latest else None})


# ─────────────────────────────────────────────────────────────────────
# AI 助手关注点/分析视角配置管理(#26, A-20260904-003 v1.2)
#   AdminUser(改提示词=高风险); 不依赖 LLM_ENABLED(可先配置后启用)。
# ─────────────────────────────────────────────────────────────────────
@router.get("/focus-configs", response_model=R, summary="AI 关注点/分析视角配置列表(内部)")
def ai_focus_list(db: DBSession, _: AdminUser):
    return R(data=ai_focus_service.list_configs(db))


@router.post("/focus-configs", response_model=R, summary="新建 AI 关注点(内部)")
def ai_focus_create(payload: AiFocusCreate, db: DBSession, me: AdminUser):
    return R(data=ai_focus_service.create_config(db, payload, me.username))


@router.patch("/focus-configs/{config_id}", response_model=R, summary="更新 AI 关注点(内部)")
def ai_focus_update(config_id: int, payload: AiFocusUpdate, db: DBSession, me: AdminUser):
    return R(data=ai_focus_service.update_config(db, config_id, payload, me.username))


@router.delete("/focus-configs/{config_id}", response_model=R, summary="删除 AI 关注点(内部)")
def ai_focus_delete(config_id: int, db: DBSession, _: AdminUser):
    ok = ai_focus_service.delete_config(db, config_id)
    if not ok:
        raise HTTPException(404, "关注点不存在")
    return R(data={"ok": True})


# ─────────────────────────────────────────────────────────────────────
# AI 提示词分层预览 + 模型配置(只读脱敏)  (#44, A-20260910-002 LOCKED)
#   AdminUser; 不依赖 LLM_ENABLED(可先查看配置后启用)。
#   🔒 安全红线: LLM_API_KEY 明文绝不出现在任何响应中; 本组端点**仅 GET**(不可写)。
# ─────────────────────────────────────────────────────────────────────
@router.get("/prompt-layers", response_model=R, summary="AI 提示词分层总览(内部, 只读)")
def ai_prompt_layers(_: AdminUser):
    """返回 AI 提示词四层结构: 模型接入 + 铁律/口径卡/关注点。

    设计取舍(A-20260910-002 §2.1): 前三层是**防幻觉底线**, 通过 UI 不可修改;
    本端点只回**结构与字符数**, 正文走 /prompt-preview 只读端点, 避免前端误以为可编辑。
    """
    from app.services.ai_glossary import glossary_all

    system_len = len(ai_feed._SYSTEM_PROMPT or "")
    try:
        glossary_len = len(glossary_all() or "")
    except Exception:  # noqa: BLE001  口径卡加载异常不阻塞总览
        logger.warning("ai prompt-layers: glossary 加载失败")
        glossary_len = 0

    return R(data={
        "layers": [
            {
                "key": "model",
                "name": "模型接入",
                "editable": False,
                "source": "env",
                "reason": "服务端 .env 注入; API Key 严禁入库/前端明文展示",
                "manage_path": None,
            },
            {
                "key": "system",
                "name": "System 铁律",
                "editable": False,
                "source": "code",
                "reason": "防幻觉底线, 代码常量",
                "char_count": system_len,
            },
            {
                "key": "glossary",
                "name": "业务口径卡",
                "editable": False,
                "source": "code",
                "reason": "客观统计口径, 代码常量",
                "char_count": glossary_len,
            },
            {
                "key": "focus",
                "name": "关注点 / 分析视角",
                "editable": True,
                "source": "db",
                "reason": "运营主观偏好, 可增改/排序/启停",
                "table": "ai_focus_configs",
                "manage_path": "/system/ai-focus-configs",
            },
        ],
        "notice": "口径卡与铁律是防幻觉底线, 不可通过界面修改; 仅「关注点」可配置。",
    })


@router.get("/prompt-preview", response_model=R, summary="AI 提示词分层正文预览(内部, 只读)")
def ai_prompt_preview(layer: str, db: DBSession, _: AdminUser):
    """只读预览某一层提示词正文(A-20260910-002: 看得见但改不动)。

    本端点**只有 GET**, 不提供任何写入方法 —— 铁律/口径卡是代码常量。
    """
    from app.services.ai_glossary import glossary_all

    if layer == "system":
        return R(data={"layer": "system", "name": "System 铁律", "content": ai_feed._SYSTEM_PROMPT, "readonly": True})
    if layer == "glossary":
        return R(data={"layer": "glossary", "name": "业务口径卡", "content": glossary_all(), "readonly": True})
    if layer == "focus":
        # 关注点在 DB 侧(可编辑), 此处拼接为文本便于与只读两层对照查看
        rows = ai_focus_service.list_configs(db)
        enabled = [r for r in rows if r.get("enabled")]
        content = "\n\n".join(
            f"[{r.get('scope_key')}] {r.get('title')}\n{r.get('content')}" for r in enabled
        )
        return R(data={
            "layer": "focus",
            "name": "关注点 / 分析视角",
            "content": content,
            "readonly": False,
            "count": len(enabled),
        })
    raise HTTPException(400, f"未知 layer: {layer!r}; 允许: system/glossary/focus")


@router.get("/model-config", response_model=R, summary="AI 模型配置(内部, 脱敏只读)")
def ai_model_config(_: AdminUser):
    """返回模型接入配置(**脱敏**)。

    🔒 安全红线(A-20260904-003 LOCKED): `LLM_API_KEY` 明文**绝不出现在响应中**。
    本端点只回「是否已配置 + 长度」——长度为帮运维核对是否填了正确长度的 key, 不含内容。
    有测试对该约束做断言(test_ai_model_config.py::test_no_api_key_leak)。
    """
    key = getattr(settings, "LLM_API_KEY", "") or ""
    return R(data={
        "enabled": bool(settings.LLM_ENABLED),
        "base_url": settings.LLM_BASE_URL or "",
        "model": settings.LLM_MODEL or "",
        "thinking": getattr(settings, "LLM_THINKING", "disabled"),
        "timeout_s": getattr(settings, "LLM_TIMEOUT_S", 120.0),
        "max_tokens": getattr(settings, "AI_MAX_TOKENS", 1500),
        "api_key_configured": bool(key),
        "api_key_length": len(key),  # 只回长度, 不回内容
        "source": "env",
        "notice": (
            "模型接入配置由服务端 .env 提供(LLM_ENABLED/LLM_BASE_URL/LLM_MODEL/LLM_API_KEY)。"
            "API Key 严禁入库、严禁在界面展示明文; 修改需运维在服务器更新 .env 并重启后端。"
        ),
    })

