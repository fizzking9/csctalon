"""AI 助手 - 自然语言时间窗口解析 (工单 #66, 2026-09-11)

背景
----
生产诊断(2026-09-10)证据 5: 前端只发 `{question, period}`, feed 永远取「当前窗口」。
用户问「昨天的运营数据」时, 模型手里只有今日值 + yoy 百分比 → 只能答「昨日暂无数据」,
或拿同比硬凑成对比。本模块把问句里的中文时间表达解析为 (start, end) 纯日期窗口。

设计原则
--------
1. **只产出纯日期**: 与 external `/brief` 的 `start`/`end` 同粒度(`YYYY-MM-DD`),
   不引入时分, 避免与 `analytics._resolve_windows` 的「整天」语义分叉。
2. **保守优先**: 没把握的表达一律返回 `none`, 让上层退回原有 `period` 行为;
   绝不猜一个「看起来对」的窗口 —— 防幻觉优先于「显得智能」。
3. **必须回显**: 调用方必须把解析结果(含 `label`)回显给用户, 让推断可见、可纠错。
4. **不产生未来窗口**: 晚于今天(北京)的日期直接丢弃, 防止空窗口静默返回 0 被当成
   「业务归零」误判。
5. **必须回显**: 调用方必须把 `label` 回显给用户(见 ai.py `window_label`)。

支持的表达
----------
| 类别 | 示例 | 解析结果 |
|---|---|---|
| 相对单日 | 今天/今日/昨天/昨日/前天/大前天 | 该日单日 |
| N 天前 | `3天前` | 该日单日 |
| 近 N 天 | 近3天/最近7天/过去5天/这10天 | 含今天往前 N 天 |
| 口语短区间 | 这两天/两三天/这几天/近两天 | 含今天往前 2 天起 |
| 周 | 本周/上周/上上周/前一周 | 本周=周一~今天; 历史周=完整周 |
| 月 | 本月/上月 | 本月=1日~今天; 上月=完整月 |
| 绝对日期 | `2026-09-10` / `2026年9月10日` / `9月10日` | 该日单日 |
| 绝对区间 | 同时出现两个日期(如「9月1日和9月5日」) | 覆盖两者的闭区间 |

优先级: **明确日期点 > 区间表达**。即「本月1日到本月5日」取 1~5 日,
而不是被「本月」扩成 1 日~今天。
"""

import json
import re
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timedelta, timezone

from app.core.config import settings

TZ_CN = timezone(timedelta(hours=8))

# ── 相对单日 token(值 = 相对今天的天数偏移, 0 = 今天)──
# 正则按 token 长度降序拼接, 保证「大前天」先于「前天」命中(否则会被截成「前天」)
_REL_DAY_OFFSET: dict[str, int] = {
    "大前天": 3,
    "前天": 2,
    "昨天": 1,
    "昨日": 1,
    "今天": 0,
    "今日": 0,
    "本日": 0,
}
_RE_REL_DAY = re.compile("|".join(sorted(_REL_DAY_OFFSET, key=len, reverse=True)))

_RE_N_DAY_AGO = re.compile(r"(\d{1,3})\s*天前")
_RE_RECENT_N = re.compile(r"(?:最近|近|过去|这)\s*(\d{1,3})\s*(?:天|日)")
_RE_COUPLE = re.compile(r"这两天|两三天|这几天|近两天|这两天来")

# ── 中文数字近 N 天/周 (2026-09-15 #98): 用户口语常说"最近一周"而非"最近7天",
#    原正则只认阿拉伯数字, "一" 不匹配 → 退回 daily 默认窗口, AI 提示窗口差异。
#    支持: 一(1)/二(2)/两(2)/三(3)/四(4)/五(5)/六(6)/七(7)/八(8)/九(9)/十(10)
#    + "一周/一星期"=7 天
_CN_NUM_MAP = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CN_NUM_RE = "|".join(_CN_NUM_MAP.keys())
_RE_RECENT_CN_DAY = re.compile(rf"(?:最近|近|过去|这)\s*({_CN_NUM_RE})\s*(?:天|日)")
# "近一周/最近一周/过去一周/这一周" → 7 天; "近两周一星期" 也兼容
_RE_RECENT_WEEK = re.compile(r"(?:最近|近|过去|这)\s*(?:一|二|两|三|四|五)?\s*(?:周|星期)")

_RE_THIS_WEEK = re.compile(r"本周|这周|本星期|这星期|当周")
# 两周前要排在「上周」之前匹配, 否则「上上周」会被「上周」吃掉留下一个「上」
_RE_TWO_WEEKS_AGO = re.compile(r"上上个?(?:周|星期)")
_RE_LAST_WEEK = re.compile(r"上周|上星期|前一周|上一周|上个星期")

_RE_THIS_MONTH = re.compile(r"本月|这个月|当月")
_RE_LAST_MONTH = re.compile(r"上个月|上月|上一个月")

# 绝对日期: 先 ISO(`2026-09-10`), 再中文全称(`2026年9月10日`), 最后月日(`9月10日`)
_RE_ABS_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_RE_ABS_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_RE_ABS_MD = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
# 「本月5日 / 上月15日」—— 月份是相对的, 日为绝对。必须在 _RE_ABS_MD 之前处理,
# 否则「本月1日到本月5日」里的「本月」会先被区间规则吃成「本月=1日~今天」而丢失 5 日。
_RE_THIS_MONTH_DAY = re.compile(r"(?:本月|这个月)\s*(\d{1,2})\s*[日号]")
_RE_LAST_MONTH_DAY = re.compile(r"(?:上个月|上月|上一个月)\s*(\d{1,2})\s*[日号]")
# 「本月1日到5日」/「9月1日到5日」: 区间末尾省略月份, 继承前面的月。
# 必须在单点规则前处理, 否则只剩起点、终点被丢, 退化成单日。
_RE_MONTH_DAY_SPAN_REL = re.compile(
    r"(本月|这个月|上个月|上月|上一个月)\s*(\d{1,2})\s*[日号]\s*[到至\-~—－]\s*(\d{1,2})\s*[日号]"
)
_RE_MONTH_DAY_SPAN_ABS = re.compile(
    r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]\s*[到至\-~—－]\s*(\d{1,2})\s*[日号]"
)

# 掩码填充字符(全角空格): 与任何 token/正则都不冲突, 且不改变字符串长度
_MASK_CHAR = "\u3000"


@dataclass(frozen=True)
class TimeIntent:
    """问句时间意图。`kind` 为 `none` 表示「未识别」→ 上层退回 period 默认窗口。"""

    kind: str                      # none / day / range
    start: str | None = None       # YYYY-MM-DD
    end: str | None = None         # YYYY-MM-DD
    matched: str | None = None     # 命中的原始文本(便于排查误判)
    label: str = ""                # 人类可读窗口说明(必须回显给用户)

    @property
    def is_effective(self) -> bool:
        return self.kind in ("day", "range")


_NONE = TimeIntent(kind="none")


def today_cn() -> _date:
    """当前北京日期。"""
    return datetime.now(TZ_CN).date()


def _mask(patterns: tuple[re.Pattern, ...], text: str, today: _date) -> tuple[list[tuple[_date, str]], str]:
    """按顺序提取绝对日期, 命中后原地掩码。

    必须先长后短(`2026-09-10` > `2026年9月10日` > `9月10日`): 否则
    `2026年9月10日` 会被 `9月10日` 规则二次命中, 用「今天所在年」拼出错误年份。
    非法日期(如 2 月 30 日)直接丢弃 —— 不猜、不外推。
    """
    hits: list[tuple[_date, str]] = []
    masked = text
    for pattern, mode in patterns:
        parts: list[str] = []
        last = 0
        for m in pattern.finditer(masked):
            parts.append(masked[last:m.start()])
            g = m.groups()
            try:
                d: _date | None = (
                    _date(int(g[0]), int(g[1]), int(g[2])) if mode == "ymd"
                    else _date(today.year, int(g[0]), int(g[1]))
                )
            except ValueError:
                d = None
            if d is not None:
                hits.append((d, m.group(0)))
            parts.append(_MASK_CHAR * len(m.group(0)))
            last = m.end()
        parts.append(masked[last:])
        masked = "".join(parts)
    return hits, masked


def _month_base(today: _date, token: str) -> _date:
    """「本月/这个月」→ 该月 1 日;「上月/上个月/上一个月」→ 上个月 1 日。"""
    if token in ("本月", "这个月"):
        return today.replace(day=1)
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def _extract_month_day_forms(
    text: str, today: _date
) -> tuple[list[tuple[_date, str]], list[tuple[_date, _date, str]], str]:
    """提取「相对月 + 绝对日」两种形态, 命中处掩码。

    - 区间(先做): `本月1日到5日` / `上月1日到上月15日` / `9月1日到5日`
      —— 终点省略月份时继承起点所在月
    - 单点(后做): `本月15日` / `上月20日`
    返回 `(单点列表, 区间列表, 掩码后文本)`。该月无此日(如 2 月 30 日)一律丢弃, **不猜**。
    """
    points: list[tuple[_date, str]] = []
    spans: list[tuple[_date, _date, str]] = []
    masked = text

    # 区间: rel=True 表示月份是相对的(本月/上月), False 表示绝对月(9月)
    for pattern, rel in ((_RE_MONTH_DAY_SPAN_REL, True), (_RE_MONTH_DAY_SPAN_ABS, False)):
        parts: list[str] = []
        last = 0
        for m in pattern.finditer(masked):
            parts.append(masked[last:m.start()])
            g = m.groups()
            try:
                if rel:
                    base = _month_base(today, g[0])
                    s_d, e_d = base.replace(day=int(g[1])), base.replace(day=int(g[2]))
                else:
                    s_d = _date(today.year, int(g[0]), int(g[1]))
                    e_d = _date(today.year, int(g[0]), int(g[2]))
                if s_d <= e_d:
                    spans.append((s_d, e_d, m.group(0)))
            except ValueError:
                pass
            parts.append(_MASK_CHAR * len(m.group(0)))
            last = m.end()
        parts.append(masked[last:])
        masked = "".join(parts)

    # 单点: 以命中文本前缀判定「本月」还是「上月」, 避免依赖单独传参
    for pattern in (_RE_THIS_MONTH_DAY, _RE_LAST_MONTH_DAY):
        parts = []
        last = 0
        for m in pattern.finditer(masked):
            parts.append(masked[last:m.start()])
            token = "本月" if m.group(0).startswith(("本月", "这个月")) else "上月"
            try:
                points.append((_month_base(today, token).replace(day=int(m.group(1))), m.group(0)))
            except ValueError:
                pass
            parts.append(_MASK_CHAR * len(m.group(0)))
            last = m.end()
        parts.append(masked[last:])
        masked = "".join(parts)

    return points, spans, masked


def parse_time_intent(question: str, *, today: _date | None = None) -> TimeIntent:
    """从问句解析时间窗口; 无法识别 → `TimeIntent(kind="none")`。

    today 参数仅供测试注入(固定「今天」)。
    """
    q = (question or "").strip()
    if not q:
        return _NONE
    today = today or today_cn()

    # ── 0. 相对月 + 绝对日(「本月5日」「上月20日」「本月1日到5日」) ──
    #     必须最先处理并掩码: 否则区间规则会把「本月」先吃成「1日~今天」, 丢掉终点。
    month_day_points, month_day_spans, masked_head = _extract_month_day_forms(q, today)

    # ── 1. 绝对日期(先掩码, 防二次匹配) ──
    abs_hits, masked = _mask(
        ((_RE_ABS_ISO, "ymd"), (_RE_ABS_CN, "ymd"), (_RE_ABS_MD, "md")), masked_head, today
    )
    day_points: list[tuple[_date, str]] = list(abs_hits) + month_day_points

    # ── 2. 相对单日 / N 天前 ──
    for m in _RE_REL_DAY.finditer(masked):
        day_points.append((today - timedelta(days=_REL_DAY_OFFSET[m.group(0)]), m.group(0)))
    for m in _RE_N_DAY_AGO.finditer(masked):
        day_points.append((today - timedelta(days=int(m.group(1))), m.group(0)))

    # ── 3. 区间表达 ──
    spans: list[tuple[_date, _date, str]] = list(month_day_spans)
    for m in _RE_RECENT_N.finditer(masked):
        n = max(1, int(m.group(1)))
        spans.append((today - timedelta(days=n - 1), today, m.group(0)))
    # 中文数字近 N 天(2026-09-15 #98): 近三天/最近五天/过去两日 → 7 天
    for m in _RE_RECENT_CN_DAY.finditer(masked):
        n = _CN_NUM_MAP[m.group(1)]
        spans.append((today - timedelta(days=n - 1), today, m.group(0)))
    # 近 N 周(2026-09-15 #98): 最近一周/近两周/过去三星期 → N*7 天
    for m in _RE_RECENT_WEEK.finditer(masked):
        raw = m.group(0)
        cn_digit_match = re.search(r"([一二两三四五])", raw)
        n = _CN_NUM_MAP[cn_digit_match.group(1)] if cn_digit_match else 1
        spans.append((today - timedelta(days=n * 7 - 1), today, m.group(0)))
    if (m := _RE_COUPLE.search(masked)):
        spans.append((today - timedelta(days=1), today, m.group(0)))
    if _RE_THIS_WEEK.search(masked):
        monday = today - timedelta(days=today.weekday())
        spans.append((monday, today, "本周"))
    if (m := _RE_TWO_WEEKS_AGO.search(masked)):
        monday = today - timedelta(days=today.weekday()) - timedelta(days=14)
        spans.append((monday, monday + timedelta(days=6), m.group(0)))
    elif (m := _RE_LAST_WEEK.search(masked)):
        monday = today - timedelta(days=today.weekday()) - timedelta(days=7)
        spans.append((monday, monday + timedelta(days=6), m.group(0)))
    if _RE_THIS_MONTH.search(masked):
        spans.append((today.replace(day=1), today, "本月"))
    if (m := _RE_LAST_MONTH.search(masked)):
        last_day = today.replace(day=1) - timedelta(days=1)
        spans.append((last_day.replace(day=1), last_day, m.group(0)))

    # ── 4. 丢弃未来日期(防止空窗口被误读为「业务归零」) ──
    points = [(d, tok) for d, tok in day_points if d <= today]
    clipped = [(s, min(e, today), tok) for s, e, tok in spans if s <= today]

    # ── 5. 优先级: 明确日期点 > 区间表达 ──
    if points:
        return _to_intent(min(d for d, _ in points), max(d for d, _ in points),
                          _join_tokens(t for _, t in sorted(points)))
    if clipped:
        return _to_intent(min(s for s, _, _ in clipped), max(e for _, e, _ in clipped),
                          _join_tokens(t for _, _, t in clipped))
    return _NONE


def _join_tokens(tokens) -> str:
    """去重保序拼接命中文本(用户可能同时说「昨天和今天」)。"""
    return "、".join(dict.fromkeys(tokens))


def _to_intent(lo: _date, hi: _date, matched: str) -> TimeIntent:
    """(lo, hi) → TimeIntent; 同日退化为 day。"""
    if lo == hi:
        return TimeIntent(kind="day", start=lo.isoformat(), end=lo.isoformat(),
                          matched=matched, label=f"{lo.isoformat()}({matched})")
    return TimeIntent(kind="range", start=lo.isoformat(), end=hi.isoformat(),
                      matched=matched,
                      label=f"{lo.isoformat()} ~ {hi.isoformat()}({matched}, 共{(hi - lo).days + 1}天)")


# ─────────────────────────────────────────────────────────────────────
# LLM 兜底语义解析 (2026-09-15 #98, 混合方案: 正则优先 + LLM 兜底)
# ─────────────────────────────────────────────────────────────────────
# 触发条件: parse_time_intent 正则未命中任何时间表达(kind="none")。
# 设计要点:
#   · 仅在正则完全没识别到时才调 LLM, 95% 常规表达走正则(<1ms, 免费, 确定)
#   · LLM 输出严格 JSON, 后端校验日期合法性 + 不晚于今天(防幻觉)
#   · confidence < 0.7 → 退回 period 默认(保守优先, 防错误窗口)
#   · LLM 调用失败/超时 → 静默退回 period(不影响主链路)
_LLM_TIME_PROMPT = """你是一个中文时间窗口解析器。用户问句中可能包含时间表达, 请解析为日期区间。

【今天日期】{today} (北京时间)

【任务】从用户问句中识别时间窗口, 输出严格 JSON, 不要有任何解释文字:
{{
  "start": "YYYY-MM-DD",      // 窗口开始日期(含), 没有则 null
  "end": "YYYY-MM-DD",        // 窗口结束日期(含), 没有则 null
  "confidence": 0.0-1.0,      // 对解析结果的置信度
  "matched": "命中的时间表达原文"  // 如 "上周三" / "近半个月", 没有则 null
}}

【规则】
1. 相对今天计算, 所有日期必须 ≤ 今天日期
2. "上周"=上一个完整自然周(周一~周日); "本周"=本周一~今天
3. "近N天/最近N天"=今天往前 N-1 天到今天(含今天)
4. "上个月"=上个月完整月(1日~月末)
5. 若问句中完全没有时间表达, 返回 confidence=0, start/end/matched=null
6. 只输出 JSON, 不要输出 markdown 代码块标记或解释

【用户问句】{question}"""


async def llm_parse_time_intent(question: str, *, today: _date | None = None) -> TimeIntent:
    """LLM 兜底语义解析: 仅在正则未命中时调用。

    返回 TimeIntent; 任一环节失败(LLM 未配置/超时/JSON 解析错/日期非法/
    confidence<0.7)一律返回 _NONE, 让上层退回 period 默认窗口。

    ⚠️ 本函数是**兜底**而非主路径, 调用方应先跑 parse_time_intent, 仅当其
       is_effective=False 时才调用本函数。

    工单 #108 (2026-09-15): 切到 DB 多 Provider 模式; 库为空时仍按 .env 兜底。
    """
    import logging
    _log = logging.getLogger(__name__)
    today = today or today_cn()
    # DB 模式: 优先查 Provider; 库为空回退 .env 模式(由 chat_completion 内部判断)
    try:
        from app.core.database import SessionLocal
        from app.services import llm_provider_service
        from app.services.llm_client import chat_completion
        _db = SessionLocal()
        try:
            _providers = llm_provider_service.get_active_providers(_db)
            _db.close()
        except Exception:
            _db.close()
            _providers = []
    except Exception as e:  # noqa: BLE001  兜底失败不影响主链路
        _log.warning("llm_parse_time_intent: get_active_providers 失败: %s", e)
        _providers = []
    # 库为空 + .env 也未配置 → 跳过(原 v1.0 行为)
    if not _providers and not (settings.LLM_BASE_URL and settings.LLM_MODEL):
        _log.debug("llm_parse_time_intent: 无可用 Provider + .env 未配置, 跳过")
        return _NONE
    try:
        from app.services.llm_client import chat_completion

        # #202 收口: 时间解析兜底属实时问答链路 → 经 llm_scheduler L2 队列。
        # chat_completion 未指定 provider(内部走全链遍历) → 门闸用 default,
        # 与 ai.py 主问答门闸语义一致(未指定 provider 的调用互斥)。
        from app.services.llm_scheduler import acquire, release
        # 复用一个独立 Session 给本次调用记录审计日志
        _log_db = SessionLocal()
        try:
            handle = await acquire(2, name="llm_time_parse")
            try:
                content = await chat_completion(
                    [{"role": "user", "content": _LLM_TIME_PROMPT.format(today=today.isoformat(), question=question)}],
                    temperature=0.0, max_tokens=300, timeout_s=15,
                    # R-P1-3: timeout_s=15 是「单次 attempt 下限」, 本地慢端点会被
                    # _ensure_timeout_fits_tokens 按 max_tokens 自洽上调到约 50s; 但时间
                    # 解析只是实时问答的**前置兜底**(失败安全 return _NONE, 不阻断主链路),
                    # 不应让用户为此等 50s。deadline_s=20 是端到端 wall-clock 硬墙,
                    # 跨重试/降级在约 20s 内必返回(优先于单次自洽上调)。
                    deadline_s=20,
                    db=_log_db, source="llm_time_parse",
                )
            finally:
                await release(handle)
        finally:
            _log_db.close()
    except Exception as e:  # noqa: BLE001  兜底失败不影响主链路
        _log.warning("llm_parse_time_intent 调用失败(忽略): %s", e.__class__.__name__)
        return _NONE

    # 解析 JSON(容错: 去掉可能的 markdown 代码块标记)
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        _log.warning("llm_parse_time_intent JSON 解析失败: %s", text[:100])
        return _NONE
    if not isinstance(obj, dict):
        return _NONE

    start_s = obj.get("start")
    end_s = obj.get("end")
    confidence = float(obj.get("confidence") or 0)
    matched = obj.get("matched")
    # 置信度不足 → 退回默认
    if confidence < 0.7 or not start_s or not end_s:
        _log.info("llm_parse_time_intent confidence=%.2f 不足, 退回默认", confidence)
        return _NONE
    # 日期合法性校验 + 不晚于今天
    try:
        s_d = _date.fromisoformat(start_s)
        e_d = _date.fromisoformat(end_s)
    except (ValueError, TypeError):
        _log.warning("llm_parse_time_intent 日期非法: start=%s end=%s", start_s, end_s)
        return _NONE
    if s_d > today or e_d > today or s_d > e_d:
        _log.warning("llm_parse_time_intent 日期越界: start=%s end=%s today=%s", start_s, end_s, today)
        return _NONE
    return _to_intent(s_d, e_d, matched or "LLM语义解析")
