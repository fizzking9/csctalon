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
    build_messages,
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


def _mock_complaint_enhanced(handled=1, backlog=3):
    """#139 模拟 _complaint_enhanced 返回结构(仅取 total 用于 daily_trend)。"""
    return {
        "total": {"registered": 5, "handled": handled, "backlog": backlog},
        "by_source": [],
    }


def _make_side_effect(kpi=None, enhanced=None):
    """#139 根据 cached_call 的 key 前缀路由返回 kpi / enhanced mock。

    - key 含 "today:kpi" → 返回 kpi(缺省 _mock_kpi())
    - key 含 "complaint:enhanced" → 返回 enhanced(缺省 _mock_complaint_enhanced())
    """
    kpi = kpi if kpi is not None else _mock_kpi()
    enhanced = enhanced if enhanced is not None else _mock_complaint_enhanced()

    def _side(*a, **_kw):
        key = a[0] if a else ""
        if "complaint:enhanced" in str(key):
            return enhanced
        return kpi
    return _side


def test_days_range_trend_span_gt_14_no_degradation():
    """#136: span > 14 (月报路径) 不再降级, 30 天全展开, 每条独立日期。
    #139 B: 每条额外调 complaint:enhanced → 60 次 cached_call, 含 complaint_handled/pending。
    """
    db = MagicMock()
    fake_today = __import__("datetime").date(2026, 9, 30)
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", side_effect=_make_side_effect()) as m_call:
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
    # #139 B: 每条新增 complaint_handled/pending 字段(取自 _complaint_enhanced.total)
    for item in trend:
        assert item["complaint_handled"] == 1
        assert item["complaint_pending"] == 3
    # #139 B: cached_call 应被调用 60 次(30 today:kpi + 30 complaint:enhanced)
    assert m_call.call_count == 60


def test_days_range_trend_span_le_14_weekly_unchanged():
    """周报路径(span ≤ 14)保持现行: 7 天全展开。
    #139 B: 同样调 complaint:enhanced → 14 次 cached_call。
    """
    db = MagicMock()
    fake_today = __import__("datetime").date(2026, 9, 15)
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", side_effect=_make_side_effect()) as m_call:
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        trend, span = _days_range_trend(db, "2026-09-09", "2026-09-15")
    assert span == 7
    assert len(trend) == 7
    # #139 B: 7 today:kpi + 7 complaint:enhanced = 14
    assert m_call.call_count == 14


def test_days_range_trend_cap_31():
    """#136: span > 31 (异常长窗口) 时 cap 31, 防超载。
    #139 B: cap 同样作用于 complaint:enhanced → 62 次 cached_call。
    """
    db = MagicMock()
    fake_today = __import__("datetime").date(2026, 12, 31)
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", side_effect=_make_side_effect()) as m_call:
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        # 60 天窗口
        trend, span = _days_range_trend(db, "2026-11-01", "2026-12-31")
    assert span == 61
    # cap 31: 不会取 61 天
    assert len(trend) == 31
    # #139 B: 31 today:kpi + 31 complaint:enhanced = 62
    assert m_call.call_count == 62


def test_days_range_trend_future_days_skipped():
    """历史日 future guard: 今天还没到的日期跳过(不会有数据)。
    #139 B: complaint:enhanced 同样只取历史日 → 12 次 cached_call。
    """
    db = MagicMock()
    fake_today = __import__("datetime").date(2026, 9, 15)
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", side_effect=_make_side_effect()) as m_call:
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        # 9-10~9-20 共 11 天, 但 9-16~9-20 还没到 → 应只取 6 天
        trend, span = _days_range_trend(db, "2026-09-10", "2026-09-20")
    assert span == 11
    assert len(trend) == 6  # 9-10~9-15
    assert trend[0]["date"] == "2026-09-10"
    assert trend[-1]["date"] == "2026-09-15"
    # #139 B: 6 today:kpi + 6 complaint:enhanced = 12
    assert m_call.call_count == 12


# ── _enrich_with_daily_trend(集成: 月报路径触发 hourly_summary 嵌入) ────


def test_enrich_with_daily_trend_monthly_no_hourly_summary():
    """2026-09-23: 月报路径(span > 14)按日全展开 daily_trend, 但不再嵌 hourly_summary
    (用户「不关注按小时的数据」; 小时粒度对月报是噪音且占 token)。"""
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
    # 不再嵌 hourly_summary(按天走势足够, 小时粒度移除)
    for item in feed["daily_trend"]:
        assert "hourly_summary" not in item


def test_enrich_with_daily_trend_weekly_no_hourly_but_staff():
    """2026-09-23: 周报路径(span ≤ 14)不再补 hourly_summary(同月报移除);
    #139 D: staff_top5 仍补(整窗一次)。
    """
    db = MagicMock()
    feed = {}
    fake_today = __import__("datetime").date(2026, 9, 15)
    fake_kpi = _mock_kpi(reception_value=100)
    fake_hourly = {
        "slot_labels": [f"{h:02d}:00" for h in range(24)],
        "reception": [5] * 24,
        "refund_count_total": [1] * 24,
    }
    fake_eff = {"data": [{"name": "张三", "work_score": 88, "reception_count": 50,
                          "is_virtual": False, "rank": 1}]}
    # side_effect 按 key 路由: today:kpi / today:hourly / today:staff / complaint:enhanced
    def _side(*a, **_kw):
        key = str(a[0]) if a else ""
        if "today:hourly" in key:
            return fake_hourly
        if "today:staff" in key:
            return fake_eff
        if "complaint:enhanced" in key:
            return _mock_complaint_enhanced()
        return fake_kpi
    with patch("app.services.ai_feed._dt") as m_dt, \
         patch("app.services.ai_feed.cached_call", side_effect=_side):
        m_dt.date.fromisoformat.side_effect = lambda s: __import__("datetime").date.fromisoformat(s)
        m_dt.date.today.return_value = fake_today
        m_dt.timedelta = __import__("datetime").timedelta
        _enrich_with_daily_trend(db, feed, "2026-09-09", "2026-09-15")
    assert "daily_trend" in feed
    assert len(feed["daily_trend"]) == 7
    # 不再嵌 hourly_summary
    for item in feed["daily_trend"]:
        assert "hourly_summary" not in item
    # #139 D: feed.staff_top5 仍存在(整窗一次调用 today_staff_efficiency)
    assert "staff_top5" in feed
    assert len(feed["staff_top5"]) >= 1
    assert feed["staff_top5"][0]["name"] == "张三"


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


# ── build_messages scope 切片 + 体裁注入 (#181 专项分桶) ──────────────────


def _make_full_feed():
    """模拟 _build_feed 返回的完整 feed(三业务段 + 恒量段)。"""
    return {
        "period": "daily",
        "date": "2026-09-21",
        "date_start": "2026-09-21",
        "date_end": "2026-09-21",
        "generated_at": "2026-09-21 08:00:00",
        "_spec": {"金额单位": "元"},
        "reception": {"接待量": 123, "会话数": 456},
        "refund": {"refund_marker": "退款段含涉诉", "routes": {"litigation_ratio": 0.05, "amount": 1234.5}},
        "complaint": {"complaint_marker": "投诉三源段", "by_source": [{"name": "内部", "count": 1}]},
    }


def _build_messages_body(question, **kw):
    """mock _build_feed/glossary/prompt/focus 后调 build_messages, 返回 user body。"""
    db = MagicMock()
    with patch("app.services.ai_feed._build_feed", return_value=_make_full_feed()), \
         patch("app.services.ai_feed.glossary_for_question", return_value="测试口径卡"), \
         patch("app.services.prompt_config_service.load_effective_prompt", return_value="系统提示词v2"), \
         patch("app.services.ai_focus_service.focus_for_question", return_value=""):
        msgs = build_messages(db, "daily", "2026-09-21", question, **kw)
    return msgs[1]["content"]


def test_build_messages_scope_refund_slices_feed():
    """#181 专项桶: scope=refund 强制并入 refund 段(即使问句未命中关键词)。

    泛问句 + refund 桶 → 只喂 refund 段(含涉诉数据)+恒量段, reception/complaint
    段被切掉 → feed 聚焦不截断, 根治「涉诉答暂无」错配。
    """
    body = _build_messages_body("今日整体情况如何", scope="refund", format="brief")
    assert "refund_marker" in body          # refund 段保留(含涉诉)
    assert "litigation_ratio" in body
    assert "接待量" not in body              # reception 段被切掉
    assert "complaint_marker" not in body   # complaint 段被切掉
    assert "【体裁要求】" in body and "简报" in body


def test_build_messages_scope_complaint_slices_feed():
    """#181 投诉专项桶: 只喂 complaint 段(三源), 不喂 refund/reception。"""
    body = _build_messages_body("今日整体情况如何", scope="complaint", format="brief")
    assert "complaint_marker" in body
    assert "refund_marker" not in body
    assert "接待量" not in body


def test_build_messages_overview_keeps_full_feed_and_detail_rule():
    """#181 overview 桶(或 None): 不强制切片(仅按问句命中), 泛问保留全维度;
    format=detail 注入【明细】体裁要求。"""
    body = _build_messages_body("今日整体情况如何", scope="overview", format="detail")
    assert "refund_marker" in body
    assert "complaint_marker" in body
    assert "接待量" in body
    assert "【体裁要求】" in body and "明细" in body


def test_build_messages_scope_union_with_question_hits():
    """#181 取并集: 问句命中 refund+complaint, 桶 scope=refund → 两段都保留。"""
    body = _build_messages_body("今日退款和投诉怎么样", scope="refund", format="brief")
    assert "refund_marker" in body
    assert "complaint_marker" in body
    assert "接待量" not in body  # 未命中 reception → 仍被切掉

