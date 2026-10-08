"""AI 分析助手路由 (A-20260904-003 LOCKED v1.0)

一期=自由问答:
- 数据: 内部 service 直连聚合(ai_feed._build_feed = external._build_brief 同底层同缓存)
- 口径: ai_glossary 口径卡随 Prompt 注入
- 模型: llm_client(OpenAI 兼容, 一期 deepseek-v4-flash, 可切本地)
- 任务: AiTaskManager 内存任务 + 轮询
- 权限: ManagerUser; LLM_ENABLED=false → 503
"""

import asyncio
import datetime as _dt
import logging
import re
import time
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
from app.services.ai_time_parser import parse_time_intent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI 分析助手"])

# 严格纯日期: 字面 `YYYY-MM-DD`(4-2-2 数字)。不用 `date.fromisoformat` —— 后者
# 在 Python ≥3.11 会接受 `20260910` / `2026-W01-1`, 比手写分段校验更松。
_PLAIN_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ─────────────────────────────────────────────────────────────────────
# #152 兜底层 Deep-Req: 用户明确要求深度分析(对答案不满意)的意图词
# ─────────────────────────────────────────────────────────────────────
_DEEP_REQ_WORDS = (
    "太简单", "再详细", "详细点", "详细一点", "详细些", "更详细",
    "这不对", "说错了", "重新分析", "深度分析", "深挖", "更细",
)

# 兜底层动作提示(结果前缀, 让用户知道"正在取底层数据按需求重跑", 层层兜底)
_DEEP_REQ_NOTE = (
    "【深度分析】我现在去深度分析：取底层数据，按您的需求重新跑一次分析。\n\n"
)


def _has_deep_req_intent(question: str) -> bool:
    """判定问句是否命中「用户明确要求深度分析」意图(#152 兜底层)。

    命中 → 跳过命中链, 直接实时 27b 按窗口+维度重算, 结果带深度分析提示前缀。
    仅做字符串包含判定(毫秒级), 不做 LLM 语义识别, 避免误判/延迟。
    """
    q = (question or "").strip()
    if not q:
        return False
    ql = q.lower()
    return any(w in ql for w in _DEEP_REQ_WORDS)


def _question_dims(question: str) -> list[str]:
    """无模板可依时, 按问句 scope 给提示性维度清单(#152 仅提示用)。

    补算/深度实时生成的 required_dimensions 注入: 模板配置优先, 模板缺失时用
    本函数兜底(退款/投诉/接待三域, 维度词与模板 required_dimensions 同风格)。
    """
    dims: list[str] = []
    if "退款" in question:
        dims += ["总金额", "笔数", "四路", "涉诉", "挽回"]
    if "投诉" in question:
        dims += ["登记", "处理", "积压"]
    if "接待" in question:
        dims += ["接待量", "会话数", "专项"]
    return dims


def _template_for(db, report) -> "object | None":
    """按命中报告查其模板(L2 命中路径取 required_dimensions / #144 短路用)。"""
    if report is None:
        return None
    tid = getattr(report, "template_id", None)
    if not tid:
        return None
    from app.models.ai_report_template import AiReportTemplate
    try:
        return db.query(AiReportTemplate).get(tid)
    except Exception:  # noqa: BLE001  查模板失败仅返回 None, 不阻塞主流程
        return None


def _get_last_llm_model(db) -> str | None:
    """#116 取最近一条 source=ai_analyze 的成功 LLM 调用命中的 provider_code+model。

    用途: AI 报告落库时记下"用的是哪个模型", 便于按模型分组复盘报告质量差异。
    失败/未找到 → None(落库 NULL, 不影响主流程)。
    """
    from sqlalchemy import text
    try:
        row = db.execute(
            text("""SELECT provider_code, model
                    FROM llm_call_logs
                    WHERE source = 'ai_analyze' AND status = 'success'
                    ORDER BY created_at DESC
                    LIMIT 1"""),
        ).mappings().first()
        if not row:
            return None
        code = row.get("provider_code") or ".env"
        model = row.get("model") or ""
        return f"{code}/{model}" if model else code
    except Exception as e:  # noqa: BLE001  失败仅返回 None, 不阻塞主流程
        logger.warning("ai get_last_llm_model failed: %s", e)
        return None


def _require_plain_date(value: str | None, name: str) -> str:
    """校验 `value` 为字面 `YYYY-MM-DD` 且是真实日历日; 否则 400。返回规范化串。"""
    raw = (value or "").strip()
    if not _PLAIN_DATE_RE.match(raw):
        raise HTTPException(400, f"{name} 参数非法: {value!r}, 应为 YYYY-MM-DD")
    try:
        _dt.date.fromisoformat(raw)
    except (ValueError, TypeError):
        # 格式对但日历非法(如 2026-13-01 / 2026-02-30)
        raise HTTPException(400, f"{name} 参数非法: {value!r}, 不是有效日期")
    return raw


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
    # #135 clarify 兜底用: 跳过三级命中链路, 强制走实时 LLM 路径(架构文档 §6.2 "直接生成"按钮)
    force_llm: bool = Field(False, description="跳过命中链路, 强制走实时 LLM(架构文档 §6.2 clarify 兜底按钮用)")


class _AiWindow:
    """解析并校验 AI 查询窗口: 显式 date/start-end > 问句自然语言识别 > period 兜底。

    优先级(工单 #66, 2026-09-11):
      1. 显式 `start`+`end`(调用方最明确)
      2. 显式 `date`(单日)
      3. **问句自然语言**(如「昨天的运营数据」→ 自动取昨日窗口)
      4. `period` 默认窗口(原有行为, 向后兼容)

    自然语言识别结果必须通过 `window_label` 回显给用户, 让推断可见、可纠错。
    """

    def __init__(self, payload: AiAnalyzeRequest):
        self.period = payload.period
        self.date = payload.date
        self.start = payload.start
        self.end = payload.end
        self.window_source = "period"
        self.window_label = ""
        self._validate()

    def _validate(self) -> None:
        # ⚠️ 必须用**精确正则**校验, 不能用 `date.fromisoformat`:
        #   - 原手写分段校验(长度 4/2/2 且数字)会放过 `2026-13-01`(月份非法), 太松;
        #   - 但 `date.fromisoformat` 又会**接受** `20260910`、`2026-W01-1` 这类紧凑/周格式
        #     (实测 Python 3.14), 比原实现更松 —— 属于把校验改退化。
        # 故锁定字面格式 `YYYY-MM-DD`, 与 `external._valid_date_or_400` 逐字符一致。
        if self.date:
            _require_plain_date(self.date, "date")
        # start/end 成对校验(纯日期)
        if self.start is not None or self.end is not None:
            if self.start is None or self.end is None:
                raise HTTPException(400, "start/end 必须成对提供(YYYY-MM-DD)")
            for name, val in (("start", self.start), ("end", self.end)):
                _require_plain_date(val, name)

    def resolve(self, question: str) -> None:
        """按优先级定窗口; 自然语言命中时改写 start/end 并记录来源与说明。

        ⚠️ 只在「调用方未显式给窗口」时介入 —— 显式参数永远优先, 避免"智能"覆盖用户意图。
        识别失败(无时间表达 / 非法日期)一律退回 period, 保持既有行为不变。
        """
        if self.start is not None or self.end is not None:
            self.window_source = "explicit_range"
            self.window_label = f"{self.start} ~ {self.end}(指定区间)"
            return
        if self.date:
            self.window_source = "explicit_date"
            self.window_label = f"{self.date}(指定日期)"
            return
        intent = parse_time_intent(question)
        if not intent.is_effective:
            # 未识别 → 保持 period 默认窗口(与原行为完全一致)
            #
            # ⚠️ #93 (2026-09-14): window_label 是**给用户看的文案**（前端回显
            #    「按哪个窗口统计」）。原实现写 `f"period={self.period}"` ——
            #    把**变量名与英文枚举**当文案输出，生产日志实测形如：
            #        `ai analyze 窗口: source=period label=period=daily`
            #    用户看到 "period=daily" 无法判断是「今天/本周/本月」。
            #    现改为中文人读文案（与 nl / explicit 分支风格一致）。
            _PERIOD_LABELS = {
                "daily": "今日",
                "weekly": "本周",
                "monthly": "本月",
            }
            _zh = _PERIOD_LABELS.get(self.period, self.period)
            # date 存在时 effective() 会把 date 作为窗口 → 文案须带上具体日期，
            # 否则「今日」与实际统计日（可能是历史某天）不符，属误导。
            if self.date:
                self.window_label = f"{self.date}(指定日期)"
            else:
                self.window_label = f"{_zh}(默认窗口)"
            return
        # 识别成功: 统一转成 start/end 纯日期区间(单日也走区间, 语义更明确)
        self.start, self.end = intent.start, intent.end
        # date 必须清空, 否则 effective() 会优先透传 date 覆盖推断结果
        self.date = None
        self.window_source = "nl"
        self.window_label = intent.label

    def effective(self) -> dict:
        """返回供 ai_feed 使用的窗口参数(仅当显式给了 start/end 才透传)。

        ⚠️ period 需随窗口形态调整: 落在区间(跨天)时用 `weekly`, 让
        `refund_decision_overview` 的 grain 走 `week`(与 external brief range 同映射);
        否则单日窗口会因 grain=day 而统计口径不一致。
        """
        if self.start is not None or self.end is not None:
            period = self.period
            if self.start != self.end:
                period = "weekly"
            return {"period": period, "date": None, "start": self.start, "end": self.end}
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
async def ai_analyze(payload: AiAnalyzeRequest, current_user: ManagerUser):
    """提交分析任务, 立即返回 task_id(生成可能数十秒, 前端轮询 GET /ai/tasks/{id})。

    窗口优先级(工单 #66): 显式 start/end > 显式 date > **问句自然语言识别** > period 兜底。
    识别结果通过 `window` / `window_label` 回显, 让推断可见。

    #116 持久化: 任务 success 后落 ai_reports 表(归属 current_user), 响应增 report_id/usage/total_ms/llm_ms。
    """
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(400, "question 不能为空")
    user_id = current_user.id
    # 窗口解析(显式参数校验 + 自然语言识别)
    win = _AiWindow(payload)
    win.resolve(q)

    # #150 闲聊闸门(毫秒短路, 不依赖 LLM_ENABLED): 纯寒暄/闲聊问句(吃饭了没/
    #   你好/谢谢...), 不进任何 LLM 链路(含 LLM 时间兜底/语义匹配/摘取), 直接
    #   返回固定引导文案。位置在窗口解析后、_require_enabled 前:
    #   ① 闲聊没有时间意图, 不该触发 LLM 时间解析(慢的来源之一);
    #   ② 闲聊不需要 LLM: LLM_ENABLED=false(助手禁用)时也能应答引导文案而非 503;
    #   ③ 含业务词的长问句("你好, 今日退款如何")一律放行, 零误杀。
    #   #150 决策: 闲聊不落库(无经营信息, 落库只污染「我的待办」历史)。
    from app.services.ai_rule_engine import try_chitchat_answer
    _chitchat_md = try_chitchat_answer(q)
    if _chitchat_md is not None:
        _eff = win.effective()
        logger.info("ai chitchat 短路: question=%r out_chars=%d", q[:30], len(_chitchat_md))
        return R(data={"task_id": None, "status": "success",
                       "result_md": _chitchat_md,
                       "report_id": None, "hit_mode": "chitchat",
                       "usage": None, "total_ms": 0, "llm_ms": 0,
                       "llm_model": "chitchat",
                       "window_source": win.window_source,
                       "window_label": win.window_label,
                       "period": _eff["period"],
                       "start": _eff["start"],
                       "end": _eff["end"]})

    # 从这里开始才需要 LLM(闲聊闸门以上均无需 LLM 即可应答)
    _require_enabled()

    # #98 (2026-09-15) LLM 兜底: 正则未命中(window_source=="period")时, 调 LLM 语义解析
    #   仅兜底路径, 95% 常规表达走正则(<1ms); LLM 失败/低置信度静默退回 period。
    if win.window_source == "period":
        from app.services.ai_time_parser import llm_parse_time_intent
        _llm_intent = await llm_parse_time_intent(q)
        if _llm_intent.is_effective:
            win.start, win.end = _llm_intent.start, _llm_intent.end
            win.date = None
            win.window_source = "nl_llm"
            win.window_label = _llm_intent.label
            logger.info("ai analyze LLM 时间兜底命中: %s", _llm_intent.label)
    eff = win.effective()
    logger.info("ai analyze 窗口: source=%s label=%s period=%s start=%s end=%s",
                win.window_source, win.window_label, eff["period"], eff["start"], eff["end"])

    # #98 层3: 规则层秒回(纯取数问句不入队 LLM, 毫秒级)。
    # 命中 → 直接返回 success(返回结构与 LLM 路径一致, 前端零改);
    # 未命中/取数空 → 返回 None 继续 LLM 路径(零侵入)。
    # 窗口回显与 LLM 路径一致(让用户核对窗口, 识别错误可立刻发现)。
    window_info = {
        "window_source": win.window_source,
        "window_label": win.window_label,
        "period": eff["period"],
        "start": eff["start"],
        "end": eff["end"],
    }

    from app.services.ai_rule_engine import try_rule_answer
    _rule_db = SessionLocal()
    try:
        _rule_md = try_rule_answer(_rule_db, q, {**eff, "window_label": win.window_label})
    finally:
        _rule_db.close()
    if _rule_md is not None:
        logger.info("ai rule short-circuit: question=%r out_chars=%d window_source=%s",
                    q[:30], len(_rule_md), win.window_source)
        # #116 规则层秒回也落库(llm_model=rule-engine, tokens=None, 耗时=0)
        from app.services.ai_report_service import save_report
        _rule_db2 = SessionLocal()
        try:
            _saved = save_report(
                _rule_db2,
                task_id=None, user_id=user_id, question=q,
                period=eff["period"], date=eff["date"], start=eff["start"], end=eff["end"],
                window_source=win.window_source, window_label=win.window_label,
                answer_md=_rule_md, llm_model="rule-engine",
                llm_ms=0, total_ms=0, tokens=None,
                hit_mode="rule",
            )
        finally:
            _rule_db2.close()
        return R(data={"task_id": None, "status": "success",
                       "result_md": _rule_md,
                       "report_id": _saved.id if _saved else None,
                       "usage": None, "total_ms": 0, "llm_ms": 0,
                       "llm_model": "rule-engine",
                       **window_info})

    # #135 三级命中链路: L1 关键词(match_template_report 不变) → 命中走摘取层(不返回全文);
    #   L1 未命中 → L2 27b 语义匹配 → 命中走摘取层;
    #   L2 未命中且有候选 → L3 clarify 兜底(返回 H2 标题列表, 让用户选段重发);
    #   L2 未命中且无候选 → 走原 #116 实时 LLM 路径(保留兼容, 架构文档 §八.3 取舍③)
    from app.services.ai_extract_service import extract_section, split_sections
    from app.services.ai_report_matcher import (
        bump_hit_accounting,
        list_candidates,
        match_template_report,
    )
    from app.services.ai_semantic_matcher import semantic_match

    def _persist_hit(_db, *, _result_md: str, _hit_mode: str,
                     _src_report, _tmpl) -> int | None:
        """#147 命中(L1/L2)路径也落库一条本人私有报告, 供「我的待办·AI 报告历史」复查。

        命中路径原本直接 return 不落库 → 历史无从区分粒度。现统一落库:
        - is_public=False(本人私有), hit_mode 区分 full/section
        - 复用源报告 template_id/snapshot_date/snapshot_period(便于归因到模板)
        - 不 bump matched_count(那是源公共报告的复用统计, 与本私有行无关)
        返回新行 id; 失败返回 None(不影响主响应)。
        """
        if not _result_md:
            return None
        from app.services.ai_report_service import save_report
        tmpl_code = getattr(_tmpl, "code", None)
        saved = save_report(
            _db,
            task_id=None, user_id=user_id, question=q,
            period=eff["period"], date=eff["date"], start=eff["start"], end=eff["end"],
            window_source=win.window_source, window_label=win.window_label,
            answer_md=_result_md,
            llm_model=f"pregen:{tmpl_code}" if tmpl_code else "pregen",
            llm_ms=0, total_ms=0, tokens=None,
            template_id=(
                (getattr(_src_report, "template_id", None))
                or (getattr(_tmpl, "id", None))
            ),
            snapshot_date=(
                str(getattr(_src_report, "snapshot_date", "") or "") or None
            ),
            snapshot_period=getattr(_src_report, "snapshot_period", None),
            is_public=False,
            hit_mode=_hit_mode,
        )
        return saved.id if saved else None

    # #135 force_llm=True 时跳过三级命中链路, 直接走 _runner 实时 LLM 兜底
    #   (架构文档 §6.2 "直接生成"按钮: clarify 状态下用户主动选择触发)
    # #151 单日窗口回退为 date 语义: effective() 对区间形态(start/end, 单日也是
    #   区间)返回 date=None → 匹配层 SQL 按"今天"查快照 → 问「昨天」命中今日
    #   预生成日报(窗口标签 09-18 但内容统计 09-19 实时), 窗口与内容错位。
    #   单日区间(start==end)即等价于指定日期, 回退为 win_date 传入匹配层。
    _win_date = eff["date"] or (
        eff["start"] if eff["start"] and eff["start"] == eff["end"] else None
    )

    # #152 回答模式分级: 默认 live(实时 LLM, 兼容现行兜底); 命中快照后按问句
    #   意图重定为 brief/standard/deep; 兜底层 deep_req 跳过命中链直接实时。
    #   _live_dims: 实时生成时注入的 required_dimensions(模板配置或问句兜底清单)
    #   _source: 数据来源(snapshot/snapshot_reuse/live_llm), 随响应回给前端标记
    _mode = "live"
    _source = "live_llm"
    _live_dims: list | None = None
    _deep_req = False

    # #182 统一意图识别: 一次算出「内容域 / 粒度 / 新鲜度 / 是否提时间」四维。
    #   此前这些散在 5 处各自字面匹配且互不通信（见 ai_intent_service 模块说明）。
    #   v1 接入方式: **只做观测 + 个人绩效域的定向补强** ——
    #     · 全链路打日志, 使「识别与实际走的路是否一致」可核对（先拿数据再改行为）
    #     · 个人绩效无预生成模板, 直接走实时（避免白试一次模板匹配）
    #   其余分支暂不改 —— 识别层的词表需要真实流量检验, 一次改太多
    #   会让出问题时无法定位是"识别错"还是"分流逻辑错"。
    from app.services import ai_intent_service
    _intent = ai_intent_service.classify(q)
    logger.info(
        "ai #182 intent: question=%r domains=%s granularity=%s freshness=%s(%s)",
        q[:40], sorted(_intent.domains), _intent.granularity,
        _intent.freshness, _intent.freshness_reason,
    )
    if _intent.is_personal and not payload.force_llm:
        # 个人绩效: 看板有数据（today_staff_efficiency / operator_rank）但**无预生成模板**,
        #   → 不可能命中快照。直接走实时, 省掉一次必然失败的模板匹配 + 语义匹配。
        #   ⚠️ 这是"事前判定"取代"事后补救"的第一处落地:
        #      此前它要等 L1 未命中 + L2 未命中 + 无候选 才落到实时。
        _mode = "deep_req"
        _deep_req = True
        _live_dims = _question_dims(q) or None
        logger.info("ai #182 个人绩效域: 跳过命中链直接实时 (无预生成模板)")

    if not payload.force_llm and not _deep_req:
        if _has_deep_req_intent(q):
            # #152 兜底层 Deep-Req: 用户明确要求深度分析(太简单/再详细/重新分析/深挖...)
            #   跳过命中链, 直接实时 27b 按窗口+维度重算, 结果带深度分析提示前缀,
            #   让用户知道"正在取底层数据按需求重跑"(层层兜底最后一层)。
            _mode = "deep_req"
            _deep_req = True
            _live_dims = _question_dims(q) or None
            logger.info("ai #152 deep_req 兜底: question=%r 跳过命中链直接实时重算", q[:30])
        else:
            from app.services.ai_completeness_service import check_dimensions
            from app.services.ai_mode_service import classify_question
            from app.services.ai_recompute_service import recompute
            from app.services.ai_report_service import record_latest_missing

            _match_db = SessionLocal()
            try:
                _matched, _tmpl = match_template_report(
                    _match_db, q, eff["period"], _win_date,
                    win_start=eff["start"], win_end=eff["end"],
                )

                if _matched is not None:
                    # L1 命中 → 按问句意图分级
                    _mode = classify_question(q)["mode"]
                    if _mode == "brief":
                        # 简报: 快照摘取秒回(快照即最终, 时点口径)
                        _ext = await extract_section(_match_db, q, _matched, _tmpl)
                        if _ext["result_md"]:
                            _hit_mode = "full" if _ext["confidence"] >= 0.9 else "section"
                            _hit_id = _persist_hit(
                                _match_db, _result_md=_ext["result_md"],
                                _hit_mode=_hit_mode, _src_report=_matched, _tmpl=_tmpl,
                            )
                            logger.info("ai #152 L1 brief: question=%r report_id=%s template=%s "
                                        "extract_conf=%.2f out_chars=%d hit_mode=%s hit_row=%s",
                                        q[:30], _matched.id,
                                        _tmpl.code if _tmpl else None,
                                        _ext["confidence"], len(_ext["result_md"]),
                                        _hit_mode, _hit_id)
                            return R(data={"task_id": None, "status": "success",
                                           "result_md": _ext["result_md"],
                                           "report_id": _hit_id or _matched.id,
                                           "hit_mode": _hit_mode,
                                           "matched_template": _tmpl.code if _tmpl else None,
                                           "extract_confidence": _ext["confidence"],
                                           "mode": "brief", "source": "snapshot",
                                           **window_info})
                        # 摘取失败/need_clarify → L3 clarify
                        logger.info("ai #152 L1 brief 摘取失败 → clarify: report_id=%s", _matched.id)
                        return R(data={"task_id": None, "status": "clarify",
                                       "clarify_options": _ext.get("clarify_options", []),
                                       "message": "未命中明确段落, 请选择您关心的部分或换种表述",
                                       "mode": "brief", "source": "snapshot",
                                       **window_info})

                    if _mode == "deep":
                        # 深度: 不走快照, 直接实时 27b 全量 feed(带模板维度注入)
                        _live_dims = (getattr(_tmpl, "required_dimensions", None) or []) or _question_dims(q)
                        _live_dims = _live_dims or None
                        logger.info("ai #152 L1 deep: question=%r report_id=%s template=%s 转实时重算",
                                    q[:30], _matched.id, _tmpl.code if _tmpl else None)
                    else:
                        # 明细: 完整性校验 → 缺维度补算(同窗口快照优先)
                        # #153 收集层: 校验结果(missing)写回被校验快照的
                        #   latest_missing, 供周期「提示词健康报告」聚合缺失维度
                        _ck = check_dimensions(_match_db, _matched, _tmpl)
                        record_latest_missing(_match_db, _matched.id, _ck["missing"])
                        if _ck["complete"]:
                            _ext = await extract_section(_match_db, q, _matched, _tmpl)
                            if _ext["result_md"]:
                                _hit_mode = "full" if _ext["confidence"] >= 0.9 else "section"
                                _hit_id = _persist_hit(
                                    _match_db, _result_md=_ext["result_md"],
                                    _hit_mode=_hit_mode, _src_report=_matched, _tmpl=_tmpl,
                                )
                                logger.info("ai #152 L1 standard(维度齐全): question=%r report_id=%s "
                                            "out_chars=%d hit_mode=%s",
                                            q[:30], _matched.id, len(_ext["result_md"]), _hit_mode)
                                return R(data={"task_id": None, "status": "success",
                                               "result_md": _ext["result_md"],
                                               "report_id": _hit_id or _matched.id,
                                               "hit_mode": _hit_mode,
                                               "matched_template": _tmpl.code if _tmpl else None,
                                               "extract_confidence": _ext["confidence"],
                                               "mode": "standard", "source": "snapshot",
                                               **window_info})
                            logger.info("ai #152 L1 standard 摘取失败 → clarify: report_id=%s", _matched.id)
                            return R(data={"task_id": None, "status": "clarify",
                                           "clarify_options": _ext.get("clarify_options", []),
                                           "message": "未命中明确段落, 请选择您关心的部分或换种表述",
                                           "mode": "standard", "source": "snapshot",
                                           **window_info})
                        # 缺维度 → 补算
                        _win = {**eff, "window_label": win.window_label,
                                "window_source": win.window_source}
                        _re = await recompute(_match_db, q, _win, _tmpl)
                        if _re["source"] == "snapshot_reuse" and _re.get("result_md"):
                            _hit_mode = "full" if _re.get("confidence", 0) >= 0.9 else "section"
                            _hit_id = _persist_hit(
                                _match_db, _result_md=_re["result_md"],
                                _hit_mode=_hit_mode, _src_report=_matched, _tmpl=_tmpl,
                            )
                            logger.info("ai #152 L1 standard 补算(同窗口快照复用): question=%r "
                                        "report_id=%s reuse_id=%s out_chars=%d",
                                        q[:30], _matched.id, _re["report_id"], len(_re["result_md"]))
                            return R(data={"task_id": None, "status": "success",
                                           "result_md": _re["result_md"],
                                           "report_id": _hit_id or _matched.id,
                                           "hit_mode": _hit_mode,
                                           "matched_template": _tmpl.code if _tmpl else None,
                                           "extract_confidence": _re.get("confidence", 0),
                                           "recomputed_from": _re["report_id"],
                                           "mode": "standard", "source": "snapshot_reuse",
                                           **window_info})
                        # 无同窗口完整快照 → 实时 27b(带 required_dimensions)
                        _live_dims = (getattr(_tmpl, "required_dimensions", None) or []) or _question_dims(q)
                        _live_dims = _live_dims or None
                        logger.info("ai #152 L1 standard 缺维度且无完整快照 → 实时补算: "
                                    "question=%r report_id=%s missing=%s",
                                    q[:30], _matched.id, _ck["missing"])
                else:
                    # L1 未命中 → L2 27b 语义匹配
                    _cands = list_candidates(_match_db, eff["period"], _win_date,
                                             eff["start"], eff["end"], question=q)
                    _sem = await semantic_match(_match_db, q, _cands)
                    if _sem["hit_report"] is not None:
                        _hit2 = _sem["hit_report"]
                        # #193 命中记账补齐: L1 由 _wrap_row → bump_hit_accounting 记账,
                        #   原先 L2 语义命中不记账 → matched_count 系统性偏低(实测 7 天差 32%),
                        #   会让"刷新频率 vs 命中量"的决策建立在偏小的命中率上。此处补记一次。
                        bump_hit_accounting(_match_db, _hit2.id)
                        _tmpl2 = _template_for(_match_db, _hit2)
                        _mode = classify_question(q)["mode"]
                        if _mode == "brief":
                            _ext2 = await extract_section(_match_db, q, _hit2, _tmpl2)
                            if _ext2["result_md"]:
                                _hit_id = _persist_hit(
                                    _match_db, _result_md=_ext2["result_md"],
                                    _hit_mode="section", _src_report=_hit2, _tmpl=_tmpl2,
                                )
                                logger.info("ai #152 L2 brief: question=%r report_id=%s "
                                            "semantic_conf=%.2f out_chars=%d hit_row=%s",
                                            q[:30], _hit2.id,
                                            _sem["confidence"], len(_ext2["result_md"]), _hit_id)
                                return R(data={"task_id": None, "status": "success",
                                               "result_md": _ext2["result_md"],
                                               "report_id": _hit_id or _hit2.id,
                                               "hit_mode": "section",
                                               "extract_confidence": _ext2["confidence"],
                                               "semantic_confidence": _sem["confidence"],
                                               "mode": "brief", "source": "snapshot",
                                               **window_info})
                            logger.info("ai #152 L2 brief 摘取失败 → clarify: report_id=%s", _hit2.id)
                            return R(data={"task_id": None, "status": "clarify",
                                           "clarify_options": _ext2.get("clarify_options", []),
                                           "message": "未命中明确段落, 请选择您关心的部分或换种表述",
                                           "mode": "brief", "source": "snapshot",
                                           **window_info})
                        if _mode == "deep":
                            _live_dims = (getattr(_tmpl2, "required_dimensions", None) or []) or _question_dims(q)
                            _live_dims = _live_dims or None
                            logger.info("ai #152 L2 deep: question=%r report_id=%s 转实时重算",
                                        q[:30], _hit2.id)
                        else:
                            _ck2 = check_dimensions(_match_db, _hit2, _tmpl2)
                            # #153 收集层: 校验结果(missing)写回被校验快照(与 L1 同规)
                            record_latest_missing(_match_db, _hit2.id, _ck2["missing"])
                            if _ck2["complete"]:
                                _ext2 = await extract_section(_match_db, q, _hit2, _tmpl2)
                                if _ext2["result_md"]:
                                    _hit_id = _persist_hit(
                                        _match_db, _result_md=_ext2["result_md"],
                                        _hit_mode="section", _src_report=_hit2, _tmpl=_tmpl2,
                                    )
                                    logger.info("ai #152 L2 standard(维度齐全): question=%r "
                                                "report_id=%s out_chars=%d hit_row=%s",
                                                q[:30], _hit2.id, len(_ext2["result_md"]), _hit_id)
                                    return R(data={"task_id": None, "status": "success",
                                                   "result_md": _ext2["result_md"],
                                                   "report_id": _hit_id or _hit2.id,
                                                   "hit_mode": "section",
                                                   "extract_confidence": _ext2["confidence"],
                                                   "semantic_confidence": _sem["confidence"],
                                                   "mode": "standard", "source": "snapshot",
                                                   **window_info})
                                logger.info("ai #152 L2 standard 摘取失败 → clarify: report_id=%s", _hit2.id)
                                return R(data={"task_id": None, "status": "clarify",
                                               "clarify_options": _ext2.get("clarify_options", []),
                                               "message": "未命中明确段落, 请选择您关心的部分或换种表述",
                                               "mode": "standard", "source": "snapshot",
                                               **window_info})
                            _win = {**eff, "window_label": win.window_label,
                                    "window_source": win.window_source}
                            _re2 = await recompute(_match_db, q, _win, _tmpl2)
                            if _re2["source"] == "snapshot_reuse" and _re2.get("result_md"):
                                _hit_id = _persist_hit(
                                    _match_db, _result_md=_re2["result_md"],
                                    _hit_mode="section", _src_report=_hit2, _tmpl=_tmpl2,
                                )
                                logger.info("ai #152 L2 standard 补算(同窗口快照复用): question=%r "
                                            "report_id=%s reuse_id=%s out_chars=%d",
                                            q[:30], _hit2.id, _re2["report_id"], len(_re2["result_md"]))
                                return R(data={"task_id": None, "status": "success",
                                               "result_md": _re2["result_md"],
                                               "report_id": _hit_id or _hit2.id,
                                               "hit_mode": "section",
                                               "extract_confidence": _re2.get("confidence", 0),
                                               "semantic_confidence": _sem["confidence"],
                                               "recomputed_from": _re2["report_id"],
                                               "mode": "standard", "source": "snapshot_reuse",
                                               **window_info})
                            _live_dims = (getattr(_tmpl2, "required_dimensions", None) or []) or _question_dims(q)
                            _live_dims = _live_dims or None
                            logger.info("ai #152 L2 standard 缺维度且无完整快照 → 实时补算: "
                                        "question=%r report_id=%s missing=%s",
                                        q[:30], _hit2.id, _ck2["missing"])
                    elif _cands:
                        # L2 未命中: 有候选报告 → L3 clarify(用第一份报告的 H2 标题列表作为 options)
                        _clarify = list(split_sections(_cands[0].answer_md or "").keys())
                        logger.info("ai extract L3 clarify(无命中): question=%r options=%d",
                                    q[:30], len(_clarify))
                        return R(data={"task_id": None, "status": "clarify",
                                       "clarify_options": _clarify,
                                       "message": "未命中明确段落, 请选择您关心的部分或换种表述",
                                       "mode": "live", "source": "live_llm",
                                       **window_info})
                    else:
                        # L2 未命中且无候选 → 走实时 LLM 兜底(现行行为)
                        _mode = "live"
                        logger.info("ai extract L0/L1/L2 全部未命中且无候选 → 走实时 LLM 兜底")
            finally:
                _match_db.close()

    async def _runner(task):
        """后台执行: 建独立 Session 组装 feed → 调 LLM → 写回 task + 落 ai_reports。"""
        t0 = time.perf_counter()
        try:
            db = SessionLocal()
            try:
                # #152 实时生成注入 required_dimensions: 模板配置(命中链 deep/standard
                #   缺快照补算)或问句兜底清单(deep_req), 让 LLM 按维度全覆盖输出。
                messages = ai_feed.build_messages(
                    db, eff["period"], eff["date"], q,
                    start=eff["start"], end=eff["end"],
                    window_label=win.window_label, window_source=win.window_source,
                    required_dimensions=_live_dims,
                )
            finally:
                db.close()
            build_ms = int((time.perf_counter() - t0) * 1000)
            # 记录注入的 data 规模日志(不含明细)
            logger.info("ai task %s: period=%s date=%s start=%s end=%s question_len=%d build_ms=%d",
                        task.task_id, eff["period"], eff["date"], eff["start"], eff["end"],
                        len(q), build_ms)
            # #116 llm_result 是 LLMResult(继承 str), .usage 拿 token 统计
            # #120 前台实时路径显式传 timeout_s=60(用户等); 后台预生成走 #121 传 180
            # R-P1-3: 60s 是云端/单次 attempt 下限; 本地慢端点按 1500 token 自洽上调到
            #   约 130s(物理上产得完)。是否加端到端 deadline 硬墙属实时 SLO, 与 B2-5
            #   max_tokens 一并评估, 本次仅统一口径不擅缩用户等待。
            # #127 接入真优先级队列: 用户实时问答挂 L2(最低, 可被 L0/L1 抢占)。
            #   被抢占时上层(ai_task_manager)捕获 CancelledError 置"可重试"。
            from app.services.llm_scheduler import acquire, release
            handle = await acquire(2, name=f"ai_analyze:{task.task_id}")
            try:
                llm_result = await llm_client.chat_completion(
                    messages,
                    max_tokens=int(getattr(settings, "AI_MAX_TOKENS", 1500) or 1500),
                    timeout_s=60.0,
                    db=db, source="ai_analyze",
                )
            finally:
                await release(handle)
            task.result_md = str(llm_result)
            # #152 兜底层 Deep-Req: 结果前缀提示"正在取底层数据按需求重跑"(层层兜底最后一层),
            #   让用户知道这是深度重算而非又一遍快照摘取。
            if _deep_req:
                task.result_md = _DEEP_REQ_NOTE + (task.result_md or "")
            total_ms_now = int((time.perf_counter() - t0) * 1000)
            llm_ms_now = total_ms_now - build_ms
            task.usage = llm_result.usage
            task.total_ms = total_ms_now
            task.llm_ms = llm_ms_now
            task.build_ms = build_ms
            # 命中的 provider+model 字符串(DB 模式从 db 拿; .env 模式用 settings.LLM_MODEL)
            task.llm_model = _get_last_llm_model(db)
            # 埋点(工单 #66 C3): 让生产可统计耗时与窗口来源, 定位"同步等待越线"。
            logger.info("ai task %s done: build_ms=%d llm_ms=%d total_ms=%d out_chars=%d window_source=%s",
                        task.task_id, build_ms, llm_ms_now, total_ms_now,
                        len(llm_result or ""), win.window_source)
            # #116 持久化(同步落库, 失败仅 log; report_id 写回 task 供主入口/轮询取)
            from app.services.ai_report_service import save_report
            _persist_db = SessionLocal()
            try:
                _saved = save_report(
                    _persist_db,
                    task_id=task.task_id, user_id=user_id, question=q,
                    period=eff["period"], date=eff["date"], start=eff["start"], end=eff["end"],
                    window_source=win.window_source, window_label=win.window_label,
                    answer_md=task.result_md or "",
                    llm_model=task.llm_model,
                    llm_ms=llm_ms_now, total_ms=total_ms_now,
                    tokens=task.usage,
                    hit_mode="live",
                )
                if _saved:
                    task.report_id = _saved.id
            finally:
                _persist_db.close()
        except llm_client.LLMError as e:
            task.error = str(e)
            logger.error("ai task %s LLM 失败: %s (elapsed_ms=%d window_source=%s)",
                         task.task_id, e, int((time.perf_counter() - t0) * 1000), win.window_source)
            raise
        except Exception as e:  # noqa: BLE001
            task.error = f"分析失败: {e}"
            logger.exception("ai task %s 异常 (elapsed_ms=%d)", task.task_id,
                             int((time.perf_counter() - t0) * 1000))
            raise

    task_id = ai_task_manager.submit(q, eff["period"], eff["date"], _runner,
                                     start=eff["start"], end=eff["end"],
                                     window_source=win.window_source,
                                     window_label=win.window_label)
    # window_info 已在 #98 层3 短路入口前构造(规则层与 LLM 路径共用, 避免重复)
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
        return R(data={"task_id": task_id, "status": "success",
                       "result_md": task.result_md,
                       "report_id": task.report_id,
                       "usage": task.usage,
                       "total_ms": task.total_ms,
                       "llm_ms": task.llm_ms,
                       "build_ms": task.build_ms,
                       "llm_model": task.llm_model,
                       # #152 回答模式分级: mode/source 随结果回前端标记(实时路径)
                       "mode": _mode, "source": _source,
                       **window_info})
    if task is not None and task.status == "failed":
        return R(data={"task_id": task_id, "status": "failed",
                       "error": task.error or "分析失败(未捕获到错误详情, 请查服务端日志)",
                       "mode": _mode, "source": _source,
                       **window_info})
    # 超时未完成 → 回 task_id 走轮询。
    # ⚠️ 2026-09-11 (#66): 生产实测 36% 请求走这条路径, 而前端在此之前只有一次轮询就断链
    #    → 用户看到"毫无提示"。这里显式带上 remaining_s, 让前端能给出"仍在生成中"的反馈。
    return R(data={
        "task_id": task_id, "status": "running",
        "message": "分析仍在生成中, 请继续查询或稍后在助手面板查看",
        "waited_s": round(wait_s, 1),
        "mode": _mode, "source": _source,
        **window_info,
    })


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
# AI 报告历史(工单 #116) - 仅本人可见; admin 可查全部
# ─────────────────────────────────────────────────────────────────────
@router.get("/reports", response_model=R, summary="AI 报告历史列表(本人)")
def ai_reports_list(
    db: DBSession, current_user: ManagerUser,
    page: int = 1, page_size: int = 20,
    start: str | None = None, end: str | None = None,
    period: str | None = None,
):
    """本人 AI 报告历史列表(admin 可查全部, 普通用户仅本人)。

    支持时间范围(start/end 纯日期 YYYY-MM-DD)与 period(daily/weekly/monthly/range) 筛选;
    默认按 created_at DESC, 分页。响应不返回 answer_md(详情另调 /reports/{id})。
    """
    if start:
        _require_plain_date(start, "start")
    if end:
        _require_plain_date(end, "end")
    is_admin = current_user.role == "admin"
    from app.services.ai_report_service import list_reports
    data = list_reports(
        db, current_user.id, is_admin,
        start=start, end=end, period=period,
        page=page, page_size=page_size,
    )
    return R(data=data)


@router.get("/reports/summary", response_model=R, summary="AI 报告 KPI + 7 日柱状(本人)")
def ai_reports_summary(db: DBSession, current_user: ManagerUser):
    """本人 AI 报告 KPI(总条数/30天条数/累计 token) + 7 日柱状。

    admin 范围看全部, 普通用户仅本人。供"我的待办·AI 报告历史" tab KPI 头部展示。
    """
    is_admin = current_user.role == "admin"
    from app.services.ai_report_service import summary as _summary
    return R(data=_summary(db, current_user.id, is_admin))


@router.get("/queue-status", response_model=R, summary="LLM 调度队列状态")
def ai_queue_status(_: ManagerUser):
    """返回当前 LLM 调度队列状态(供前端忙时提示)。

    #123: 用户请求前可调此接口, 若 is_busy=true 前端显示"⏳ 当前 LLM 队列 N 个"。
    """
    from app.services.llm_scheduler import queue_status
    return R(data=queue_status())


@router.get("/reports/{report_id}", response_model=R, summary="AI 报告详情(本人或 admin)")
def ai_report_detail(report_id: int, db: DBSession, current_user: ManagerUser):
    """单条 AI 报告详情(含 answer_md 全文)。

    权限: 本人或 admin; 非本人且非 admin → 403(不暴露他人报告存在性)。
    """
    is_admin = current_user.role == "admin"
    from app.services.ai_report_service import get_report
    data = get_report(db, report_id, current_user.id, is_admin)
    if data is None:
        # 用 404 而非 403: 不暴露"存在但无权限"(隐私), 与无 id 一视同仁
        raise HTTPException(404, "AI 报告不存在或无权查看")
    return R(data=data)


@router.post("/reports/{report_id}/feedback", response_model=R, summary="对 AI 报告评分/留意见(采集)")
def ai_report_feedback(report_id: int, db: DBSession, current_user: ManagerUser, payload: dict):
    """对报告/对话结果打分 1~5 + 可选意见（**只采集，不实时逐条分析**）。

    权限同报告详情(本人或 admin)。评价落 ai_report_feedbacks，评估中心按周期
    汇总归因低分报告/高频意见，驱动提示词迭代（#174 演进）。
    payload: {rating: 1~5 必填, opinion: 可选文本}
    """
    is_admin = current_user.role == "admin"
    from app.services.ai_report_service import get_report
    data = get_report(db, report_id, current_user.id, is_admin)
    if data is None:
        raise HTTPException(404, "AI 报告不存在或无权查看")
    payload = payload or {}
    try:
        from app.services.ai_report_feedback_service import create_feedback
        row = create_feedback(
            db, report_id, current_user.id,
            rating=int(payload.get("rating", 0)),
            opinion=payload.get("opinion"),
        )
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return R(data=row, message="评价已记录，将纳入下次周期评估汇总")


@router.get("/reports/{report_id}/feedbacks", response_model=R, summary="某报告的评价列表(本人或 admin)")
def ai_report_feedbacks_list(report_id: int, db: DBSession, current_user: ManagerUser):
    """返回某报告全部评价（时间正序）。权限同详情。"""
    is_admin = current_user.role == "admin"
    from app.services.ai_report_service import get_report
    data = get_report(db, report_id, current_user.id, is_admin)
    if data is None:
        raise HTTPException(404, "AI 报告不存在或无权查看")
    from app.services.ai_report_feedback_service import (
        has_feedback_for,
        list_feedbacks,
    )
    return R(data={"items": list_feedbacks(db, report_id),
                   "mine": bool(has_feedback_for(db, report_id, current_user.id))})


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

