# -*- coding: utf-8 -*-
"""AI 助手自然语言时间识别测试 (工单 #66, 2026-09-11)。

覆盖: ai_time_parser.parse_time_intent 的相对/绝对/区间表达解析、优先级、未来窗口裁剪、
非法日期丢弃, 以及 _AiWindow 的窗口优先级与回显。

「今天」一律用 `today=` 注入固定日期(2026-09-11 周五), 避免测试随时钟漂移。
"""

import datetime as _dt

import pytest

from app.services.ai_time_parser import parse_time_intent

# 2026-09-11 是周五; 本周一=09-07, 上周一=08-31, 上上周一=08-24
FRI = _dt.date(2026, 9, 11)


def _p(q: str, today: _dt.date = FRI):
    return parse_time_intent(q, today=today)


# ── 相对单日 ────────────────────────────────────────────────
@pytest.mark.parametrize("q,expected", [
    ("昨天的运营数据", "2026-09-10"),
    ("昨日退款情况", "2026-09-10"),
    ("今天怎么样", "2026-09-11"),
    ("今日经营总览", "2026-09-11"),
    ("前天的投诉", "2026-09-09"),
    ("大前天", "2026-09-08"),
    ("3天前的数据", "2026-09-08"),
    ("4天前", "2026-09-07"),
])
def test_relative_day(q, expected):
    """B1/B2: 相对单日表达 → day 意图且日期正确。"""
    r = _p(q)
    assert r.kind == "day"
    assert r.start == expected == r.end
    assert r.is_effective


# ── 近 N 天 / 口语区间 ──────────────────────────────────────
@pytest.mark.parametrize("q,start,end", [
    ("近3天运营数据", "2026-09-09", "2026-09-11"),
    ("最近7天退款情况", "2026-09-05", "2026-09-11"),
    ("过去5天投诉", "2026-09-07", "2026-09-11"),
    ("这10天接待量", "2026-09-02", "2026-09-11"),
    ("这两天的运营数据对比", "2026-09-10", "2026-09-11"),
    ("这几天表现", "2026-09-10", "2026-09-11"),
])
def test_recent_n_days(q, start, end):
    """B2: 「近N天」含今天往前 N 天;「这两天」= 昨天+今天。"""
    r = _p(q)
    assert r.kind == "range"
    assert (r.start, r.end) == (start, end)


# ── 周 / 月 ────────────────────────────────────────────────
def test_this_week_is_monday_to_today():
    """本周 = 周一 ~ 今天(不等到周日)。"""
    r = _p("本周的运营数据")
    assert (r.start, r.end) == ("2026-09-07", "2026-09-11")


def test_last_week_is_full_week():
    """上周 = 完整自然周(周一~周日)。"""
    r = _p("上周的投诉情况")
    assert (r.start, r.end) == ("2026-08-31", "2026-09-06")


def test_two_weeks_ago():
    """上上周 → 完整自然周, 且不被「上周」提前吃掉。"""
    r = _p("上上周的退款")
    assert (r.start, r.end) == ("2026-08-24", "2026-08-30")


def test_this_month_and_last_month():
    """本月=1日~今天; 上月=完整月。"""
    assert (_p("本月的运营数据").start, _p("本月的运营数据").end) == ("2026-09-01", "2026-09-11")
    assert (_p("上月的退款").start, _p("上月的退款").end) == ("2026-08-01", "2026-08-31")


# ── 绝对日期 ────────────────────────────────────────────────
@pytest.mark.parametrize("q,expected", [
    ("2026-09-01的数据", "2026-09-01"),
    ("2026年8月15日的退款", "2026-08-15"),
    ("9月1日的投诉", "2026-09-01"),
    ("8月30日运营数据", "2026-08-30"),
])
def test_absolute_day(q, expected):
    """绝对日期(ISO / 中文全称 / 月日) → day。"""
    r = _p(q)
    assert r.kind == "day"
    assert r.start == expected


def test_absolute_year_not_overshadowed_by_month_day():
    """回归: 「2026年8月15日」不能被「8月15日」规则二次命中而改用今年。"""
    r = _p("2026年8月15日的退款", today=_dt.date(2027, 1, 1))
    assert r.start == "2026-08-15", "长规则必须先于短规则, 否则年份被覆盖"


def test_absolute_range_two_dates():
    """两个绝对日期 → 覆盖两者的闭区间。"""
    r = _p("9月1日和9月5日的对比")
    assert r.kind == "range"
    assert (r.start, r.end) == ("2026-09-01", "2026-09-05")


# ── 相对月 + 绝对日 ─────────────────────────────────────────
@pytest.mark.parametrize("q,start,end", [
    ("本月1日到本月5日", "2026-09-01", "2026-09-05"),
    ("本月1日到5日", "2026-09-01", "2026-09-05"),
    ("9月1日到5日", "2026-09-01", "2026-09-05"),
    ("上月1日到上月15日", "2026-08-01", "2026-08-15"),
])
def test_month_day_spans(q, start, end):
    """「本月1日到5日」终点省略月份 → 继承起点月。"""
    r = _p(q)
    assert (r.start, r.end) == (start, end)


def test_priority_explicit_day_beats_span():
    """优先级: 明确日期点 > 区间表达(「本月1日到5日」不能被扩成 1日~今天)。"""
    r = _p("本月1日到5日")
    assert r.end == "2026-09-05", "不应被「本月」扩到今天(2026-09-11)"


def test_last_month_day_boundary():
    """跨月边界: 3 月上旬问「上月1日到上月28日」→ 2 月完整区间。"""
    r = _p("上月1日到上月28日", today=_dt.date(2026, 3, 10))
    assert (r.start, r.end) == ("2026-02-01", "2026-02-28")


def test_last_month_day_crosses_year():
    """跨年边界: 1 月问「上月15日」→ 上一年 12 月。"""
    r = _p("上月15日", today=_dt.date(2026, 1, 5))
    assert r.start == "2025-12-15"


# ── 不识别 / 安全兜底 ───────────────────────────────────────
@pytest.mark.parametrize("q", [
    "四路分布和涉诉怎么样",
    "接待量如何",
    "这个系统怎么用",
    "",
])
def test_no_time_expression_returns_none(q):
    """B3: 无时间表达 → kind=none(上层退回 period 默认窗口, 不猜测)。"""
    r = _p(q)
    assert r.kind == "none"
    assert not r.is_effective
    assert r.start is None


def test_future_date_dropped():
    """B3: 未来日期被丢弃 → none, 不产生空窗口(防被误读为「业务归零」)。"""
    r = _p("2099-01-01 的数据")
    assert r.kind == "none", "未来窗口必须丢弃, 不得静默返回 0"


def test_invalid_calendar_date_dropped():
    """B3: 非法日历日(2 月 30) → none, 不猜、不外推。"""
    assert _p("2026-02-30 的数据").kind == "none"
    assert _p("本月30日的数据", today=_dt.date(2026, 2, 10)).kind == "none"


def test_nonexistent_month_day_dropped():
    """该月无此日(3 月无 31 日的上月 31 日) → 丢弃。"""
    assert _p("上月31日", today=_dt.date(2026, 3, 31)).kind == "none"


def test_label_is_human_readable():
    """回显文案必须可读(B1: 让用户看见窗口)。"""
    r = _p("昨天的运营数据")
    assert "2026-09-10" in r.label
    assert "昨天" in r.label
    r2 = _p("近3天运营数据")
    assert "2026-09-09" in r2.label and "2026-09-11" in r2.label
    assert "3天" in r2.label


# ── _AiWindow 优先级与回显 ──────────────────────────────────
class _Payload:
    """AiAnalyzeRequest 的最小替身(避开 pydantic 校验依赖)。"""

    def __init__(self, question, period="daily", date=None, start=None, end=None):
        self.question = question
        self.period = period
        self.date = date
        self.start = start
        self.end = end


def test_aiwindow_nl_applied_when_no_explicit():
    """B1: 无显式参数时, 问句自然语言生效并记录来源/回显。

    ⚠️ `_AiWindow.resolve` 内部走**真实时钟**（无 today 注入），故这里必须
    按「相对今天」动态断言 —— 写死日期会让测试在次日**必然过期**
    （2026-09-13 即因此失败，见 #75 的顺带修复）。
    """
    from app.api.v1.ai import _AiWindow
    today = _dt.date.today()
    yest = (today - _dt.timedelta(days=1)).isoformat()

    w = _AiWindow(_Payload("昨天的运营数据"))
    w.resolve("昨天的运营数据")
    assert w.window_source == "nl"
    assert (w.start, w.end) == (yest, yest)
    assert yest in w.window_label
    eff = w.effective()
    assert eff["start"] == yest and eff["date"] is None


def test_aiwindow_explicit_beats_nl():
    """B4(回归): 显式 start/end 优先于问句识别, 不被"智能"覆盖。"""
    from app.api.v1.ai import _AiWindow
    w = _AiWindow(_Payload("昨天的运营数据", start="2026-08-01", end="2026-08-05"))
    w.resolve("昨天的运营数据")
    assert w.window_source == "explicit_range"
    assert (w.start, w.end) == ("2026-08-01", "2026-08-05")


def test_aiwindow_explicit_date_beats_nl():
    """B4(回归): 显式 date 优先于问句识别。"""
    from app.api.v1.ai import _AiWindow
    w = _AiWindow(_Payload("昨天的运营数据", date="2026-07-01"))
    w.resolve("昨天的运营数据")
    assert w.window_source == "explicit_date"
    assert w.date == "2026-07-01"
    assert w.start is None


def test_aiwindow_period_fallback_on_no_intent():
    """B3(回归): 无时间表达 → 保持 period 默认窗口(改前行为不变)。"""
    from app.api.v1.ai import _AiWindow
    w = _AiWindow(_Payload("四路分布和涉诉怎么样"))
    w.resolve("四路分布和涉诉怎么样")
    assert w.window_source == "period"
    eff = w.effective()
    assert eff == {"period": "daily", "date": None, "start": None, "end": None}


def test_aiwindow_period_label_is_human_readable():
    """#93: period 兜底分支的 label 必须是**人读中文文案**。

    回归: 原实现写 `f"period={self.period}"` —— 把变量名当文案输出，
    生产日志实测 `label=period=daily`，前端回显给用户后无法判断
    是「今天/本周/本月」的哪个窗口（该 label 的用途正是「让用户核对
    按哪个窗口统计」）。
    """
    from app.api.v1.ai import _AiWindow

    cases = [("daily", "今日"), ("weekly", "本周"), ("monthly", "本月")]
    for period, expect in cases:
        w = _AiWindow(_Payload("四路分布和涉诉怎么样", period=period))
        w.resolve("四路分布和涉诉怎么样")
        assert w.window_label == f"{expect}(默认窗口)", (
            f"period={period} 的 label 应为可读中文，实际 = {w.window_label!r}"
        )
        # 不得出现变量名/英文枚举
        assert "period=" not in w.window_label
        assert "daily" not in w.window_label
        assert "weekly" not in w.window_label
        assert "monthly" not in w.window_label


def test_aiwindow_period_label_includes_date_when_given():
    """#93: period 分支带显式 date 时, label 必须显示**具体日期**。

    只写「今日(默认窗口)」而 effective() 实际按 date 统计 → 属误导。
    """
    from app.api.v1.ai import _AiWindow
    w = _AiWindow(_Payload("四路分布和涉诉怎么样", date="2026-09-01"))
    w.resolve("四路分布和涉诉怎么样")
    # date 存在时走的是「指定日期」语义, 必须回显日期
    assert "2026-09-01" in w.window_label


def test_aiwindow_span_uses_week_grain():
    """跨天区间 → period 改用 weekly, 让 grain 走 week(与 external brief 同映射)。

    ⚠️ 同 `test_aiwindow_nl_applied_when_no_explicit`: 走真实时钟, 需动态断言。
    """
    from app.api.v1.ai import _AiWindow
    today = _dt.date.today()
    start = (today - _dt.timedelta(days=2)).isoformat()   # 「近3天」含今天 → 往前2天
    end = today.isoformat()

    w = _AiWindow(_Payload("近3天运营数据"))
    w.resolve("近3天运营数据")
    eff = w.effective()
    assert eff["period"] == "weekly", "跨天区间必须走 week grain, 否则口径与 brief 不一致"
    assert (eff["start"], eff["end"]) == (start, end)


def test_aiwindow_single_day_keeps_period():
    """单日识别 → period 保持原值(不误改成 weekly)。"""
    from app.api.v1.ai import _AiWindow
    w = _AiWindow(_Payload("昨天的运营数据", period="monthly"))
    w.resolve("昨天的运营数据")
    assert w.effective()["period"] == "monthly"


def test_aiwindow_invalid_explicit_still_400():
    """边界: 显式非法参数仍 400(不能因引入自然语言而放松校验)。"""
    from fastapi import HTTPException

    from app.api.v1.ai import _AiWindow

    with pytest.raises(HTTPException) as e1:
        _AiWindow(_Payload("x", start="2026-08-01"))  # 不成对
    assert e1.value.status_code == 400
    # 手写分段校验会放过 2026-13-01(月份非法), 改严格校验后必须拦住
    for bad in ("2026-13-01", "2026-02-30", "abc", "2026/09/01", "2026-9-1"):
        with pytest.raises(HTTPException) as e2:
            _AiWindow(_Payload("x", date=bad))
        assert e2.value.status_code == 400, f"date={bad!r} 应 400"


@pytest.mark.parametrize("bad", ["20260910", "2026-W01-1", "2026-09-10T12:30"])
def test_aiwindow_rejects_contract_foreign_formats(bad):
    """#66 复审: `date.fromisoformat` 会接受 `20260910`/`2026-W01-1`, 但这些是契约外格式。

    必须显式拒绝 —— 否则 AI 入口放行、而 external 入口(同参数)可能表现不同,
    且带时间的串会被静默截断成整天(与请求意图不符)。
    """
    from fastapi import HTTPException

    from app.api.v1.ai import _AiWindow

    with pytest.raises(HTTPException) as e:
        _AiWindow(_Payload("x", date=bad))
    assert e.value.status_code == 400


def test_plain_date_validation_strength_matches_external():
    """#66 复审: `ai._require_plain_date` 与 `external._valid_date_or_400` 必须同强度。

    两处接受度若不一致, 会出现"AI 入口 400 而外部只读入口 200"的裂缝
    (同一个非法日期串, 两个入口行为相反)。
    """
    from fastapi import HTTPException

    from app.api.v1.ai import _require_plain_date
    from app.api.v1.external import _valid_date_or_400

    cases = ["2026-09-10", "20260910", "2026-W01-1", "2026-13-01",
             "2026-02-30", "2026-9-1", "abc", "2026-09-10T12:30"]
    for c in cases:
        def _accepts(fn) -> bool:
            try:
                fn()
                return True
            except HTTPException:
                return False

        ai_ok = _accepts(lambda c=c: _require_plain_date(c, "date"))
        ext_ok = _accepts(lambda c=c: _valid_date_or_400(c))
        assert ai_ok == ext_ok, f"{c!r}: ai={ai_ok} ext={ext_ok} 两处校验强度不一致"
