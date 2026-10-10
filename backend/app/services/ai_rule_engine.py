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


# ─────────────────────────────────────────────────────────────────────
# 闲聊/寒暄闸门 (工单 #150) - 纯寒暄问句毫秒短路, 不进 LLM
#
# 为什么需要(根因 #150): 「吃饭了没」这类闲聊问句没有任何经营意图, 原链路
# 却完整走 L2 语义匹配(7b) + 摘取层(7b) 两次 LLM —— 7b 对无关问句与日报候选
# 的语义相关性给出 ≥0.6 模糊置信度 → 误命中当日日报 → 返回一屏"接待分析"
# 数据 + "未提供关于'吃饭了没'的相关信息"。既有 `try_rule_answer` 只覆盖
# 退款取数, 与闲聊无关, 故新增独立闸门, 与取数规则层级分离。
#
# 判据(宁可漏放、不可误杀):
# 1. 去空白与标点后(数字/字母保留, 便于 "666"/"hi"):
#    问句去除寒暄短语后剩余为空(或只剩语气词) → 纯闲聊 → 短路;
# 2. 问句含任一业务词(退款/接待/投诉/经营/今日... 含"今天天气"里"今天")
#    → 一律放行, 走正常链路(问候+业务问句"你好,今日退款如何"正常应答)。
#
# 短语按长度降序替换, 保证长短语(吃饭了没)优先于短词(吃)被整体剥离。
# 寒暄回复优雅降级: 任何一次异常都返回 None(放行), 不阻塞主链路。
# ─────────────────────────────────────────────────────────────────────
_CHITCHAT_PHRASES: tuple[str, ...] = (
    "吃饭了没", "吃饭了么", "吃了吗", "吃了没", "吃饭没",
    "在不在啊", "在不在", "在嘛", "在吗", "在忙吗",
    "干嘛呢", "在干嘛", "干什么呢", "在么", "在呢",
    "早上好", "下午好", "晚上好", "中午好", "晚安", "早安", "午安",
    "你好呀", "你好啊", "你们好", "大家好", "您好", "你好", "你们",
    "哈喽", "嗨喽", "嗨呀", "hello", "hello呀", "hi", "hey", "哟",
    "谢谢啦", "谢谢了", "谢谢呀", "辛苦了", "谢谢", "多谢", "感谢", "感恩",
    "再见", "拜拜", "回聊", "再见呀",
    "哈哈哈", "哈哈", "嘿嘿", "呵呵", "嘻嘻", "吼吼",
    "666", "6666", "好呀", "好滴", "好的", "好吧", "好哒", "嗯嗯",
    "哦哦", "哦了", "知道啦", "知道了",
    "什么", "咋了", "没事", "随便", "没有没有", "没了", "好的好的",
    "你呢", "怎么啦", "怎么了", "如何评价",
    "今天天气", "天气", "干嘛", "想你了", "在吗在吗",
    "叫我", "叫我啥", "谁", "你叫什么", "你叫啥", "你是谁", "你是啥",
    "你会什么", "你会啥", "你能干嘛", "能干嘛", "你会干嘛",
    "忙吗", "忙不", "不忙", "忙呢", "在忙",
    "饿了吗", "饿了么", "饿了没",
)
# 纯语气词(替换后可剩余, 视为闲聊尘埃); 不覆盖数字/字母(保留 "hi" 判定)
_CHITCHAT_PARTICLES: tuple[str, ...] = ("呀", "呐", "啦", "了", "呢", "啊", "哦", "嘛", "呗", "呜", "哈", "嘛呢", "呀啊")
# 去标点正则(字符类内 `-` 放末尾即为字面量, 免转义; 三引号避免引号隐式拼接)
_CHITCHAT_STRIP_RE = re.compile(r'''[\s,，。.!！?？~～、；;:'"“”‘’〈〉《》【】()（）_/-]+''')

# 业务词表: 问句含任一 → 放行(即便带问候前缀)。「今天天气」含"今天"→放行,不误杀。
_CHITCHAT_BUSINESS_WORDS: tuple[str, ...] = (
    "退款", "提现", "退费", "涉诉", "投诉", "积压", "登记", "处理", "商户号",
    "接待", "会话", "咨询", "专项", "效率", "客流", "营收", "收入", "成交",
    "订单", "业绩", "金额", "笔数", "数量", "指标", "KPI", "达成", "目标",
    "加班", "下班", "值班", "挂", "运营",
    "经营", "数据", "分析", "对比", "环比", "同比", "趋势", "增长", "下降",
    "汇算", "汇总", "报表", "可视化", "图表", "导出",
    "今日", "今天", "昨天", "明天", "本周", "上周", "本月", "上月", "这周",
    "这个月", "周报", "月报", "日报", "年度", "季度", "当前", "现在", "最近",
    "多少", "几个", "几条", "几笔", "情况", "进展", "状态", "问题",
    "方案", "建议", "策略", "怎么", "如何", "为什么", "怎么办", "预测",
    "报告", "总数", "合计", "共用", "平均", "占比", "比率", "差值",
)
# 闲聊/寒暄回复文案(带快捷问引导; 不生成任何经营数据, 与命中链路解耦)
_CHITCHAT_REPLY = (
    "我是经营数据 AI 助手，专注于解答经营数据相关问题（接待 / 退款 / 投诉等），"
    "闲聊寒暄交给真人工客服就好啦。\n\n"
    "您可以试着问我：\n"
    "- 今日退款多少笔？\n"
    "- 本周投诉情况如何？\n"
    "- 本月经营总览"
)


def _strip_noise(q: str) -> str:
    """去空白/标点(保留中文字符、数字、字母): 使纯寒暄句归一化后可比。"""
    return _CHITCHAT_STRIP_RE.sub("", (q or "").lower())


def _is_pure_chitchat(q: str) -> bool:
    """剥离寒暄短语与语气词后, 剩余为空 → 纯闲聊。"""
    rest = q
    for p in sorted(_CHITCHAT_PHRASES, key=len, reverse=True):
        rest = rest.replace(p, "")
    for p in _CHITCHAT_PARTICLES:
        rest = rest.replace(p, "")
    return not rest.strip()


def try_chitchat_answer(question: str) -> str | None:
    """闲聊闸门入口: 纯寒暄/闲聊问句 → 返回固定文案; 否则 None 放行。

    #150 设计取舍(替业务问句安全着想):
      - 问句含任一业务词 → 放行(如「你好, 今日退款如何」正常走命中链路);
      - 问句过长(>12 字符) → 放行(可能有长句意图, 不冒险短路);
      - 判定失败/异常 → None(放行), 保证主链路零影响。
    """
    try:
        q = question or ""
        if len(q) > 12:  # 长问句不判定为纯闲聊(宁可放行, 不可误杀)
            return None
        norm = _strip_noise(q)
        if not norm:
            return None
        if any(w.lower() in norm for w in _CHITCHAT_BUSINESS_WORDS):
            return None  # 含业务词 → 放行
        if _is_pure_chitchat(norm):
            return _CHITCHAT_REPLY
        return None
    except Exception:  # noqa: BLE001  闸门异常放行, 零侵入主链路
        logger.warning("chitchat gate 异常(放行): %s", question, exc_info=True)
        return None


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
