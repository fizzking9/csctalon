# -*- coding: utf-8 -*-
"""AI feed 压缩函数测试 (工单 #136 批次 C1)。

覆盖:
- _compact_daily_hourly_summary: 正常 24 槽 / None 输入 / 缺字段 / 全零
- _days_range_trend: 月报路径(span > 14) 取消降级, 按日全展开 cap 31
- _enrich_with_daily_trend: 月报路径调 _enrich_daily_hourly_summary 嵌入 hourly_summary
"""

from unittest.mock import MagicMock, patch

from app.services.ai_feed import (
    _compact_daily_hourly_summary,
    _days_range_trend,
    _enrich_daily_hourly_summary,
    _enrich_with_daily_trend,
)

# ── _compact_daily_hourly_summary(同步纯函数) ─────────────────────────────


def test_compact_daily_hourly_summary_normal():
    """正常 24 槽: 峰值 14:00, 工作时段谷值 08:00(并列谷取首个), 夜间合计 50。"""
    ht = {
        "slot_labels": [f"{h:02d}:00" for h in range(24)],
        "reception": [10, 0, 0, 0, 0, 0, 0, 40, 5, 12, 50, 80, 100, 120, 200, 180, 150, 90, 60, 40, 30, 20, 10, 5],
        "refund_count_total": [0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 5, 7, 9, 11, 15, 12, 8, 5, 3, 2, 1, 1, 0, 0],
    }
    out = _compact_daily_hourly_summary(ht)
    assert out["peak_hour"] == "14:00"
    assert out["peak_reception"] == 200
    # 工作时段 8~23 内并列谷值 5(08:00 与 23:00), min 取首个 = 08:00
    assert out["low_hour"] == "08:00"
    assert out["low_reception"] == 5
    # 夜间 0~7 合计: 10+0+0+0+0+0+0+40 = 50
    assert out["night_reception"] == 50
    # 退款峰值 14:00(15 笔)
    assert out["refund_peak_hour"] == "14:00"


def test_compact_daily_hourly_summary_none():
    """None 输入返回空 dict(防 cached_call 失败)。"""
    assert _compact_daily_hourly_summary(None) == {}


def test_compact_daily_hourly_summary_missing_fields():
    """缺 reception / refund_count_total → 各极值取默认 0, 不抛异常。"""
    ht = {"slot_labels": [f"{h:02d}:00" for h in range(24)]}
    out = _compact_daily_hourly_summary(ht)
    assert out["peak_hour"] == "00:00"  # 空数组时 max default=0
    assert out["peak_reception"] == 0
    assert out["low_reception"] == 0
    assert out["night_reception"] == 0
    assert out["refund_peak_hour"] == "00:00"


def test_compact_daily_hourly_summary_all_zero():
    """全零数组: 峰值=00:00(首位), 谷值=08:00(工作时段首位), night=0。"""
    ht = {
        "slot_labels": [f"{h:02d}:00" for h in range(24)],
        "reception": [0] * 24,
        "refund_count_total": [0] * 24,
    }
    out = _compact_daily_hourly_summary(ht)
    assert out["peak_hour"] == "00:00"
    assert out["low_hour"] == "08:00"
    assert out["night_reception"] == 0


def test_compact_daily_hourly_summary_short_array():
    """reception 长度 < 24: 不越界, 退化到 default 0。"""
    ht = {
        "slot_labels": ["00:00", "01:00"],  # 只有 2 个标签
        "reception": [5, 8],  # 只有 2 个值
        "refund_count_total": [1, 2],
    }
    out = _compact_daily_hourly_summary(ht)
    # 不抛异常, 字段齐全
    assert "peak_hour" in out
    assert "low_hour" in out
    assert "night_reception" in out


def test_compact_daily_hourly_summary_night_only():
    """夜间有值, 工作时段全 0: night_reception 累加夜间, 工作谷值=0。"""
    ht = {
        "slot_labels": [f"{h:02d}:00" for h in range(24)],
        "reception": [30, 0, 0, 0, 0, 0, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        "refund_count_total": [0] * 24,
    }
    out = _compact_daily_hourly_summary(ht)
    assert out["night_reception"] == 50  # 30+20
    assert out["peak_hour"] == "00:00"  # 30 是全天峰值
    assert out["low_hour"] == "08:00"  # 工作时段全 0, 取首个


# ── _days_range_trend(走 cached_call mock) ─────────────────────────────


def _mock_kpi(reception_value=100, refund_count=5, refund_amount=500, complaint=2):
    """模拟 today_kpi 返回结构。"""
    return {
        "reception": {"value": reception_value, "device_count": reception_value - 10},
        "refund_count": {"value": refund_count},
        "refund_amount": {"value": refund_amount},
        "complaint": {"value": complaint},
    }


def test_days_range_trend_span_gt_14_no_degradation():
    """#136: span > 14 (月报路径) 不再降级, 30 天全展开, 每条独立日期。"""
    db = MagicMock()
    fake_today = __import__("datetime").date(2026, 9, 30)
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", return_value=_mock_kpi()) as m_call:
        # 保留 date / timedelta 真实实现, 只 mock today
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        trend, span = _days_range_trend(db, "2026-09-01", "2026-09-30")
    assert span == 30  # 9 月 30 天
    assert len(trend) == 30  # 全展开, 不再 14 + 周聚合
    # 每条 date 唯一且覆盖 09-01~09-30
    dates = [t["date"] for t in trend]
    assert len(set(dates)) == 30
    assert dates[0] == "2026-09-01"
    assert dates[-1] == "2026-09-30"
    # cached_call 应被调用 30 次(today:kpi), 而非 14 次(降级)
    assert m_call.call_count == 30


def test_days_range_trend_span_le_14_weekly_unchanged():
    """周报路径(span ≤ 14)保持现行: 7 天全展开。"""
    db = MagicMock()
    fake_today = __import__("datetime").date(2026, 9, 15)
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", return_value=_mock_kpi()) as m_call:
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        trend, span = _days_range_trend(db, "2026-09-09", "2026-09-15")
    assert span == 7
    assert len(trend) == 7
    assert m_call.call_count == 7


def test_days_range_trend_cap_31():
    """#136: span > 31 (异常长窗口) 时 cap 31, 防超载。"""
    db = MagicMock()
    fake_today = __import__("datetime").date(2026, 12, 31)
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", return_value=_mock_kpi()) as m_call:
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        # 60 天窗口
        trend, span = _days_range_trend(db, "2026-11-01", "2026-12-31")
    assert span == 61
    # cap 31: 不会取 61 天
    assert len(trend) == 31
    # cached_call 也只被调 31 次
    assert m_call.call_count == 31


def test_days_range_trend_future_days_skipped():
    """历史日 future guard: 今天还没到的日期跳过(不会有数据)。"""
    db = MagicMock()
    fake_today = __import__("datetime").date(2026, 9, 15)
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", return_value=_mock_kpi()) as m_call:
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        # 9-10~9-20 共 11 天, 但 9-16~9-20 还没到 → 应只取 6 天
        trend, span = _days_range_trend(db, "2026-09-10", "2026-09-20")
    assert span == 11
    assert len(trend) == 6  # 9-10~9-15
    assert trend[0]["date"] == "2026-09-10"
    assert trend[-1]["date"] == "2026-09-15"
    # cached_call 调用 6 次(只取历史日)
    assert m_call.call_count == 6


# ── _enrich_with_daily_trend(集成: 月报路径触发 hourly_summary 嵌入) ────


def test_enrich_with_daily_trend_monthly_triggers_hourly_summary():
    """#136: 月报路径(span > 14)调 _enrich_daily_hourly_summary 给每条补 hourly_summary。"""
    db = MagicMock()
    feed = {}
    fake_today = __import__("datetime").date(2026, 9, 30)
    fake_kpi = _mock_kpi(reception_value=100)
    fake_hourly = {
        "slot_labels": [f"{h:02d}:00" for h in range(24)],
        "reception": [5] * 24,
        "refund_count_total": [1] * 24,
    }
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", side_effect=lambda *a, **kw: fake_hourly if "hourly" in a[0] else fake_kpi):
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        _enrich_with_daily_trend(db, feed, "2026-09-01", "2026-09-30")
    assert "daily_trend" in feed
    assert len(feed["daily_trend"]) == 30
    # 月报路径每条应有 hourly_summary
    for item in feed["daily_trend"]:
        assert "hourly_summary" in item
        assert item["hourly_summary"]["peak_reception"] == 5
        assert item["hourly_summary"]["night_reception"] == 40  # 8 × 5


def test_enrich_with_daily_trend_weekly_no_hourly_summary():
    """#136: 周报路径(span ≤ 14)不补 hourly_summary, 字段不存在。"""
    db = MagicMock()
    feed = {}
    fake_today = __import__("datetime").date(2026, 9, 15)
    fake_kpi = _mock_kpi(reception_value=100)
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", return_value=fake_kpi) as m_call:
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        _enrich_with_daily_trend(db, feed, "2026-09-09", "2026-09-15")
    assert "daily_trend" in feed
    assert len(feed["daily_trend"]) == 7
    # 周报路径不补 hourly_summary
    for item in feed["daily_trend"]:
        assert "hourly_summary" not in item
    # 只调 today:kpi 不调 today:hourly
    for call in m_call.call_args_list:
        assert "hourly" not in call[0][0]


def test_enrich_daily_hourly_summary_failure_isolated():
    """#136: 某天 today:hourly 失败仅 warning, hourly_summary 字段留空(不影响主 daily_trend)。"""
    db = MagicMock()
    trend = [
        {"date": "2026-09-01", "reception": 100},
        {"date": "2026-09-02", "reception": 200},
    ]
    fake_hourly = {
        "slot_labels": [f"{h:02d}:00" for h in range(24)],
        "reception": [5] * 24,
        "refund_count_total": [1] * 24,
    }
    # 第一天成功, 第二天抛异常
    def _side(*a, **_kw):
        if "2026-09-01" in a[0]:
            return fake_hourly
        raise RuntimeError("redis down")
    with patch("app.services.ai_feed.cached_call", side_effect=_side):
        _enrich_daily_hourly_summary(db, trend)
    # 第一条有 hourly_summary
    assert "hourly_summary" in trend[0]
    assert trend[0]["hourly_summary"]["peak_reception"] == 5
    # 第二条失败但其他字段保留(reception=200), hourly_summary 字段不存在
    assert trend[1]["reception"] == 200
    assert "hourly_summary" not in trend[1]
