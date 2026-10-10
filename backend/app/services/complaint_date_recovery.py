"""投诉日期自由文本补救服务（#195）

定位
----
315/卡商投诉入库时「投诉日期」列为空，但自由文本（投诉问题/处理明细等）中
可能记录了投诉/诉求发起日期。本服务用**确定性规则**恢复该日期。

铁律
----
1. 只输出「投诉/诉求发起」日期；购买/充值/发货/到期日一律不输出；
2. 证据不足 → None，禁止猜测（宁留 pending 不造假）；
3. 纯函数无 I/O，规则全部显式可审。

判定管线
--------
抽取候选日期（值+位置+是否自带年份） → 触发词邻近/语义上下文打分 →
取最高分（低于阈值返回 None） → 年份补全（文本年份优先，不足返回 None）。

年份无法从单条文本确定的行，由调用方做**批次内年份传播**（同 sheet
邻近行已确定的年份），本服务不内置该跨条逻辑。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# ──────────────────────────────────────────────────────────────────────────
# 1. 候选日期形态
# ──────────────────────────────────────────────────────────────────────────

# 完整日期：2026年8月4日 / 2026.8.4 / 2026-8-4 / 2026/8/4（可带「日」）
_FULL_YMD_RE = re.compile(
    r"(20\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?"
)
# 两位年份中文：24年8月25日（否定后顾排除 2024 中的 24）
_FULL_2Y_RE = re.compile(r"(?<!20)(\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
# 带前缀的紧凑 8 位日期：TS20250407… / 预处理-20260622…
_COMPACT_RE = re.compile(
    r"(?:TS|预处理[-_])(20\d{2})(\d{2})(\d{2})(?=\d{4}|$|[^\d])"
)
# 「年月」仅用于年份线索收集（无日，不能定投诉日期）
_YEAR_MONTH_RE = re.compile(r"(20\d{2})\s*[年./\-]\s*(\d{1,2})\s*月")
_YEAR_MONTH_2Y_RE = re.compile(r"(?<!20)(\d{2})\s*年\s*(\d{1,2})\s*月")
# 月日：8月4日 / 8月4号（年份缺失）
_MD_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
# 裸数字 M.D：9.27 / 6.22（高歧义，必须紧邻强信号才采纳）
_BARE_MD_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})(?!\d)")
# 相对：上个月8号 / 上月8号（需要 anchor 日期）
_REL_RE = re.compile(r"(上)\s*个?月\s*(\d{1,2})\s*[日号]?")

# ──────────────────────────────────────────────────────────────────────────
# 2. 语义信号
# ──────────────────────────────────────────────────────────────────────────

# 强动作（投诉/诉求发起）：候选与之邻近 → 是投诉日
_STRONG_ACTION_RE = re.compile(
    r"投诉|举报|申诉|维权|要求退款|要求退还|要求退费|要求退回|要求注销|"
    r"申请退款|申请.{0,2}退款|发起|截止|承诺.{0,12}退|退还|退款|退费"
)
# 弱状态（追述/拖延状态，只说明“还没解决”，不标志发起日）
_WEAK_STATUS_RE = re.compile(
    r"未退款|未退回|还没退|仍未|至今|还未退|余额还未|没有退|拖延"
)
# 叙述当天词
_TODAY_RE = re.compile(r"今天|今日|今晚|当晚|当天|现在都|目前")
# 候选后紧跟「，已经X天/已过X天」= 投诉叙述写于当天
_ELAPSED_RE = re.compile(r"^[，,、]?\s*(已经|已过).{0,5}天")
# 问题复发（再次无法使用/不能使用…）
_RECURE_RE = re.compile(r"再次.{0,10}(无法|不能|问题|断网)|又.{0,6}(无法|不能|断网)")
# 非投诉日语义（购买/充值/收到…）
_OTHER_EVT_RE = re.compile(r"购买|买到|买了|充值|收到|发货|到货|下单|开通|到期")
# 裸数字紧跟单位 → 金额/流量，不是日期
_UNIT_AFTER_RE = re.compile(r"^\s*(元|块|G|g|%|％|折|GB|个月|天)")
_MONEY_BEFORE_RE = re.compile(r"(￥|价值|金额)$")

# 处理结果字段开头的裸 M.D（如「6.23，已销户」）
_LEADING_HANDLE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})[，,、]")

# 打分窗口
_NEAR = 6
_WIDE = 14
_SCORE_MIN = 2


@dataclass
class _Cand:
    """日期候选：位置 + 年月日（年份可空）+ 裸形态/紧凑形态标记。"""

    start: int
    end: int
    month: int
    day: int
    year: int | None
    bare: bool = False

    def iso(self, year: int) -> str:
        return date(year, self.month, self.day).isoformat()


def _valid_md(m: int, d: int) -> bool:
    return 1 <= m <= 12 and 1 <= d <= 31


def _extract_candidates(text: str) -> list[_Cand]:
    """从文本抽取全部日期候选（同一起止位置的多形态去重）。"""
    cands: list[_Cand] = []
    seen: set[tuple[int, int]] = set()

    def add(m: re.Match, month: int, day: int, year: int | None,
            bare: bool = False) -> None:
        if not _valid_md(month, day):
            return
        key = (m.start(), m.end())
        if key in seen:
            return
        seen.add(key)
        cands.append(_Cand(m.start(), m.end(), month, day, year, bare))

    for m in _FULL_YMD_RE.finditer(text):
        add(m, int(m.group(2)), int(m.group(3)), int(m.group(1)))
    for m in _FULL_2Y_RE.finditer(text):
        add(m, int(m.group(2)), int(m.group(3)), 2000 + int(m.group(1)))
    for m in _COMPACT_RE.finditer(text):
        add(m, int(m.group(2)), int(m.group(3)), int(m.group(1)))
    for m in _MD_RE.finditer(text):
        add(m, int(m.group(1)), int(m.group(2)), None)
    # 裸 M.D：先按金额/流量语境排除，其余留待打分判定
    for m in _BARE_MD_RE.finditer(text):
        if _UNIT_AFTER_RE.match(text[m.end(): m.end() + 3]):
            continue
        before = text[max(0, m.start() - _WIDE): m.start()]
        if _MONEY_BEFORE_RE.search(before):
            continue
        add(m, int(m.group(1)), int(m.group(2)), None, bare=True)
    return cands


def _score(c: _Cand, text: str, all_cands: list[_Cand]) -> int:
    """依据候选前后语义信号打分：

    - 强动作宽窗 +2，紧邻再 +1；弱状态 +1；
    - 当天词紧邻 +3 / 宽窗 +2；「，已经X天」+3；问题复发 +2；
    - 带前缀的紧凑串（TS/预处理）保底 +2（单据时间戳语义）；
    - 裸数字无强信号 -4；非投诉事件仅在零分时紧邻 -2。

    ``all_cands``：动作词与候选之间若隔着另一个日期候选，该动作词归属
    另一个候选，不重复计分。
    """
    bn = text[max(0, c.start - _NEAR): c.start]
    an = text[c.end: c.end + _NEAR]
    bw = text[max(0, c.start - _WIDE): c.start]
    aw = text[c.end: c.end + _WIDE]
    score = 0

    def _strong(window: str, w_start: int):
        """强动作匹配（全局坐标 window[w_start:]）。

        排除：① 落在弱状态词组内（没有退款/未退款…）；
        ② 与候选之间隔着另一个日期候选（动作词归属那个候选）。
        """
        weak_spans = [
            (w_start + x.start(), w_start + x.end())
            for x in _WEAK_STATUS_RE.finditer(window)
        ]
        for m in _STRONG_ACTION_RE.finditer(window):
            gs = w_start + m.start()
            ge = w_start + m.end()
            if any(ws <= gs < we for ws, we in weak_spans):
                continue
            if ge <= c.start:
                blocked = [q for q in all_cands if ge <= q.start < c.start]
            else:
                blocked = [q for q in all_cands if c.end < q.start <= gs]
            if blocked:
                continue
            return m
        return None

    # 「…，到10月16日」候选前紧邻追述介词 → 该候选是追述/截止日，
    # 前窗强动作（承诺日的动作）不加给它
    is_followup = bn.rstrip("，,")[-1:] in ("到", "至")
    action_b = None if is_followup else _strong(bw, max(0, c.start - _WIDE))
    action_a = _strong(aw, c.end)
    if action_b or action_a:
        score += 2
        near_b = (
            None if is_followup
            else _strong(bn, max(0, c.start - _NEAR))
        )
        if near_b or _strong(an, c.end):
            score += 1

    # 弱状态：仅 +1（如「10月16日，没有退款」是追述不是发起）
    if _WEAK_STATUS_RE.search(bw) or _WEAK_STATUS_RE.search(aw):
        score += 1
    # 当天词：今天4月1日 / 现在都9月11号
    if _TODAY_RE.search(bn) or _TODAY_RE.search(an):
        score += 3
    elif _TODAY_RE.search(bw) or _TODAY_RE.search(aw):
        score += 2
    # 候选后紧跟「，已经X天」= 叙述当天
    if _ELAPSED_RE.search(an):
        score += 3
    # 问题复发：9月19日再次出现无法使用
    if _RECURE_RE.search(aw):
        score += 2
    # 紧凑前缀串：保底 +2（前缀即单据时间戳，年份自带）
    prefix2 = text[max(0, c.start - 2): c.start]
    prefix4 = text[max(0, c.start - 4): c.start]
    if not c.bare and c.year is not None and (
        prefix2 == "TS" or prefix4 in ("预处理-", "预处理_")
    ):
        score = max(score, 2)
    # 裸数字：必须有当天/强动作/「日」字信号，否则压到阈值下
    if c.bare:
        if text[c.end: c.end + 1] == "日":
            score += 2
        if not (action_a or action_b or _TODAY_RE.search(bw + aw)):
            score -= 4
    # 非投诉事件：仅在完全零分时紧邻扣分
    if score == 0 and (_OTHER_EVT_RE.search(bn) or _OTHER_EVT_RE.search(an)):
        score -= 2
    return score


def _text_years(text: str) -> dict[int, int]:
    """收集文本中出现的年份及频次（4位/2位年份，含年月与完整日期）。"""
    freq: dict[int, int] = {}

    def bump(y: int) -> None:
        freq[y] = freq.get(y, 0) + 1

    for m in _FULL_YMD_RE.finditer(text):
        bump(int(m.group(1)))
    for m in _FULL_2Y_RE.finditer(text):
        bump(2000 + int(m.group(1)))
    for m in _YEAR_MONTH_RE.finditer(text):
        bump(int(m.group(1)))
    for m in _YEAR_MONTH_2Y_RE.finditer(text):
        bump(2000 + int(m.group(1)))
    for m in _COMPACT_RE.finditer(text):
        bump(int(m.group(1)))
    return freq


def _resolve_year(c: _Cand, text: str, years: dict[int, int],
                  fallback_year: int | None) -> int | None:
    """候选年份补全：自带 > 距候选最近的文本年份 > 文本最高频 > fallback。"""
    if c.year is not None:
        return c.year
    near_y: int | None = None
    near_dist = 10**9
    for pat in (r"20\d{2}", r"(?<!20)(\d{2})\s*年"):
        for m in re.finditer(pat, text):
            g0 = m.group(0)
            y = int(g0) if g0.startswith("20") else 2000 + int(g0[:2])
            d = min(abs(m.start() - c.start), abs(m.end() - c.end))
            if d < near_dist:
                near_dist, near_y = d, y
    if near_y is not None and near_dist <= 60:
        return near_y
    if years:
        return max(years, key=lambda y: years[y])
    return fallback_year


def _resolve_relative(text: str, anchor: date | None) -> str | None:
    """「上个月X号」解析；无 anchor 或该月无此日 → None。"""
    m = _REL_RE.search(text)
    if not m or anchor is None:
        return None
    first = (anchor.replace(day=1) - timedelta(days=1)).replace(day=1)
    day = int(m.group(2))
    try:
        return date(first.year, first.month, day).isoformat()
    except ValueError:
        return None


def recover_complaint_date(
    text: str | None,
    *,
    fallback_year: int | None = None,
    anchor: date | None = None,
) -> str | None:
    """从自由文本恢复投诉/诉求发起日期，返回 ``YYYY-MM-DD``；无法确定 → None。

    :param fallback_year: 文本完全无年份时的兜底年（调用方应给批次传播年份，
        而非采集时点年——后者不是叙述年份）；不给且文本无年份 → None
    :param anchor: 相对日期（上个月X号）参考日
    """
    if not text or not text.strip():
        return None

    # 相对日期：仅当邻近动作/当天词时采纳
    rm = _REL_RE.search(text)
    if rm:
        seg = text[max(0, rm.start() - _WIDE): rm.end()]
        if _STRONG_ACTION_RE.search(seg) or _TODAY_RE.search(seg):
            rel = _resolve_relative(text, anchor)
            if rel:
                return rel

    cands = _extract_candidates(text)
    if not cands:
        return None
    years = _text_years(text)

    best: tuple[int, _Cand] | None = None
    for c in cands:
        s = _score(c, text, cands)
        if best is None or s > best[0] or (
            s == best[0] and c.start < best[1].start
        ):
            # 同分取较早候选：投诉/诉求发起在先，追述日期在后
            best = (s, c)
    assert best is not None
    score, cand = best
    if score < _SCORE_MIN:
        return None
    year = _resolve_year(cand, text, years, fallback_year)
    if year is None:
        return None
    return cand.iso(year)


def extract_leading_handle_date(
    handle_text: str | None,
    *,
    fallback_year: int,
) -> str | None:
    """从「处理结果」字段开头抽取裸 M.D（如「6.23，已确认销户」）。

    字段位置本身赋予日期语义（处理完成时间），故不需要动作邻近信号；
    仅接受字段**开头**形态，避免字段中部数字误读。年份由调用方经批次
    传播确定后传入。
    """
    if not handle_text:
        return None
    m = _LEADING_HANDLE_RE.match(handle_text)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    if not _valid_md(month, day):
        return None
    return date(fallback_year, month, day).isoformat()


def recover_from_fields(
    fields: list,
    *,
    fallback_year: int | None = None,
    anchor: date | None = None,
) -> str | None:
    """便捷入口：多个文本字段拼接后恢复（空字段自动忽略）。"""
    text = " ".join(str(f).strip() for f in fields if f and str(f).strip())
    return recover_complaint_date(
        text, fallback_year=fallback_year, anchor=anchor
    )


def parse_iso(s: str) -> datetime:
    """供调用方把返回值转 datetime（时区/UTC 存储由调用方按规范处理）。"""
    return datetime.strptime(s, "%Y-%m-%d")
