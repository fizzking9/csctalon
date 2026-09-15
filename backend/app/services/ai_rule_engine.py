"""AI 规则层 - 纯取数问句秒回 (A-20260904-003, 工单 #98 层3)

为什么需要规则层:
  生产实测(#98)显示 llm_ms 占总耗时 75~95%, 即使用户问"今日退款多少笔"这种
  一个数字的问题也要走完整 LLM 链路(全量 feed + 口径卡 + 模型生成 800 token)。
  但用户实际诉求是"按既定逻辑 + 系统数据做简单判断" —— 规则化、确定性。
  规则层让纯取数问句直接从 feed 取数 + 模板拼 Markdown 毫秒返回, 不入队 LLM。

边界(只覆盖"纯取数", 推理类走 LLM):
  - 命中: "今日退款多少笔" / "昨天提现金额" / "本周退款数量"
  - 未命中(回退 LLM): "分析下今日经营" / "退款策略建议" / "为什么退款多"
  - 判据: 含 scope 关键词 + 取数词, 且不含推理词(分析/建议/策略/对比/为什么/...)

复用:
  - ai_feed._build_feed: 同一数据底座(命中 today:kpi/refund:do 缓存, 含坏缓存自愈)
  - 不复用 ai_focus_service.keyword_to_scope: 规则层需要更严格"取数词"匹配,
    scope 路由太宽("退款"就命中 scope, 但"分析下退款策略"不该走规则层)

铁律(沿用 ai_feed._SYSTEM_PROMPT 精神):
  - 模板数字必须来自 feed, 严禁编造
  - 取数为 None → 写"暂无", 不写 0
  - 输出附数据窗口(让用户核对)
"""

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.services import ai_feed

logger = logging.getLogger(__name__)

# 推理类词(出现即不命中规则层, 走 LLM 做分析)
_ANALYSIS_WORDS = ("分析", "建议", "策略", "对比", "为什么", "怎么办",
                   "怎么改善", "推荐", "评价", "评估", "预测", "趋势", "如何")

# scope 关键词 + 取数词(同时命中才走规则层)
_RULE_PATTERNS = {
    "refund": (
        re.compile(r"(退款|提现)"),
        re.compile(r"(多少笔|笔数|多少|金额|数量)"),
    ),
    # complaint/reception 规则待后续补(结构需进一步确认 + 模板设计),
    # 先留接口返回 None 回退 LLM, 不阻塞 #98 层3 上线。
}


def _looks_like_analysis(question: str) -> bool:
    """问句含推理词 → 走 LLM, 规则层不接。"""
    q = question or ""
    return any(w in q for w in _ANALYSIS_WORDS)


def _fmt_num(v: Any, suffix: str = "") -> str:
    """格式化数字; None/空 → '暂无'(不写 0, 避免误导)。"""
    if v is None:
        return "暂无"
    if isinstance(v, (int, float)):
        # 金额类保留 2 位小数; 笔数/数量取整
        if suffix == "元" and isinstance(v, float):
            return f"{v:,.2f}{suffix}"
        return f"{int(v) if isinstance(v, float) else v}{suffix}"
    return f"{v}{suffix}" if v else "暂无"


def _try_refund(feed: dict, question: str, window_label: str) -> str | None:
    """退款取数模板。feed.refund 结构见 external._build_brief L438-453。"""
    rf = feed.get("refund")
    if not isinstance(rf, dict):
        return None
    total_count = (rf.get("total_count") or {}).get("value")
    total_amount = (rf.get("total_amount") or {}).get("value")
    a_route = rf.get("a_route") or {}
    ratio = (a_route.get("litigation_ratio") or {}).get("value")
    lit_amount = a_route.get("litigation_amount")
    generated_at = feed.get("generated_at", "未知时点")
    lines = [
        f"## {window_label} 退款（截至 {generated_at}）",
        "",
        f"- 退款笔数：{_fmt_num(total_count)}",
        f"- 退款金额：{_fmt_num(total_amount, '元')}",
        f"- 涉诉率：{_fmt_num(ratio, '%') if ratio is not None else '暂无'}",
        f"- 涉诉金额：{_fmt_num(lit_amount, '元')}",
    ]
    return "\n".join(lines)


_SCOPE_RENDERERS = {
    "refund": _try_refund,
}


def try_rule_answer(db: Session, question: str, eff: dict) -> str | None:
    """规则层入口: 命中 → 返回 Markdown; 未命中/取数空 → None 回退 LLM。

    eff: ai.py _AiWindow.effective() 的窗口参数(period/date/start/end)。
    """
    q = (question or "").strip()
    if not q or _looks_like_analysis(q):
        return None

    matched_scope = None
    for scope, (kw_re, num_re) in _RULE_PATTERNS.items():
        if kw_re.search(q) and num_re.search(q):
            matched_scope = scope
            break
    if matched_scope is None:
        return None

    renderer = _SCOPE_RENDERERS.get(matched_scope)
    if renderer is None:
        return None

    # 复用 ai_feed._build_feed(命中缓存秒取, 含坏缓存自愈), 不绕过铁律
    try:
        feed = ai_feed._build_feed(
            db, eff["period"], eff.get("date"),
            start=eff.get("start"), end=eff.get("end"),
        )
    except Exception as e:  # noqa: BLE001  规则层失败不影响主链路, 回退 LLM
        logger.warning("rule_engine _build_feed 失败(回退 LLM): %s", e)
        return None

    window_label = eff.get("window_label") or eff.get("start") or eff.get("date") or "本期"
    try:
        md = renderer(feed, q, window_label)
    except Exception as e:  # noqa: BLE001  模板取数异常也回退 LLM
        logger.warning("rule_engine %s 渲染失败(回退 LLM): %s", matched_scope, e)
        return None
    if not md:
        return None
    logger.info("ai rule hit: scope=%s question=%r out_chars=%d",
                matched_scope, q[:30], len(md))
    return md
