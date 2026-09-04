"""AI 分析助手路由 (A-20260904-003 LOCKED v1.0)

一期=自由问答:
- 数据: 内部 service 直连聚合(ai_feed._build_feed = external._build_brief 同底层同缓存)
- 口径: ai_glossary 口径卡随 Prompt 注入
- 模型: llm_client(OpenAI 兼容, 一期 deepseek-v4-flash, 可切本地)
- 任务: AiTaskManager 内存任务 + 轮询
- 权限: ManagerUser; LLM_ENABLED=false → 503
"""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import ManagerUser
from app.core.config import settings
from app.core.database import SessionLocal
from app.schemas.common import R
from app.services import ai_feed, llm_client
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
    if payload.date:
        # date 基础格式校验
        parts = payload.date.split("-")
        if len(parts) != 3 or not all(len(p) == (4 if i == 0 else 2) and p.isdigit() for i, p in enumerate(parts)):
            raise HTTPException(400, f"date 参数非法: {payload.date!r}, 应为 YYYY-MM-DD")

    async def _runner(task):
        """后台执行: 建独立 Session 组装 feed → 调 LLM → 写回 task。"""
        try:
            db = SessionLocal()
            try:
                messages = ai_feed.build_messages(db, payload.period, payload.date, q)
            finally:
                db.close()
            # 记录注入的 data 规模日志(不含明细)
            logger.info("ai task %s: period=%s question_len=%d", task.task_id, payload.period, len(q))
            result = await llm_client.chat_completion(messages)
            task.result_md = result
        except llm_client.LLMError as e:
            task.error = str(e)
            raise
        except Exception as e:  # noqa: BLE001
            task.error = f"分析失败: {e}"
            raise

    task_id = ai_task_manager.submit(q, payload.period, payload.date, _runner)
    return R(data={"task_id": task_id, "message": "已提交, 请轮询 GET /api/v1/ai/tasks/{task_id}"})


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
