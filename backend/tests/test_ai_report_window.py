# -*- coding: utf-8 -*-
"""AI 报告预生成窗口计算测试 (工单 #132 批次 A)。

覆盖 compute_pregen_window / compute_compare_window 的:
- weekly 正常路径(本周一~昨天)
- weekly 退化路径(今日周一→上周一~上周日)
- monthly 正常路径(本月1日~昨日)
- monthly 退化路径(今日1号→上月1日~上月末)
- daily 返回 None(日报保持现行 date=None 实时数据)
- compute_compare_window 等长前对比窗口

固定日期注入, 避免测试随时钟漂移。
"""

import datetime as _dt

from app.services.ai_report_window import compute_compare_window, compute_pregen_window

# 2026-09-14 是周一, 2026-09-16 是周三, 2026-09-15 是月中(周二), 2026-09-01 是1号
WED = _dt.date(2026, 9, 16)   # 周三
MON = _dt.date(2026, 9, 14)   # 周一
MID = _dt.date(2026, 9, 15)   # 月中(周二)
FIRST = _dt.date(2026, 9, 1)  # 1号


# ── compute_pregen_window: weekly ────────────────────────────────
def test_weekly_normal_returns_monday_to_yesterday():
    """周三: 本周一(09-14)~昨天(09-15)。"""
    win = compute_pregen_window("weekly", ref_date=WED)
    assert win is not None
    assert win["start"] == "2026-09-14"   # 本周一
    assert win["end"] == "2026-09-15"    # 昨天
    assert win["snapshot_date"] == WED.isoformat()
    assert "weekly" in win["label"]
    assert "本周一~昨天" in win["label"]


def test_weekly_monday_degrades_to_last_week():
    """周一: 昨天属上周 → 退化取上周一(09-07)~上周日(09-13)。"""
    win = compute_pregen_window("weekly", ref_date=MON)
    assert win is not None
    assert win["start"] == "2026-09-07"   # 上周一
    assert win["end"] == "2026-09-13"     # 上周日
    assert win["snapshot_date"] == MON.isoformat()
    assert "退化" in win["label"]
    assert "上周整周" in win["label"]


# ── compute_pregen_window: monthly ───────────────────────────────
def test_monthly_normal_returns_first_to_yesterday():
    """月中15号: 本月1日(09-01)~昨日(09-14)。"""
    win = compute_pregen_window("monthly", ref_date=MID)
    assert win is not None
    assert win["start"] == "2026-09-01"   # 本月1日
    assert win["end"] == "2026-09-14"    # 昨天
    assert win["snapshot_date"] == MID.isoformat()
    assert "monthly" in win["label"]
    assert "本月1日~昨天" in win["label"]


def test_monthly_first_day_degrades_to_last_month():
    """1号: 昨天属上月 → 退化取上月1日(08-01)~上月末(08-31)。"""
    win = compute_pregen_window("monthly", ref_date=FIRST)
    assert win is not None
    assert win["start"] == "2026-08-01"   # 上月1日
    assert win["end"] == "2026-08-31"     # 上月末
    assert win["snapshot_date"] == FIRST.isoformat()
    assert "退化" in win["label"]
    assert "上月整月" in win["label"]


# ── compute_pregen_window: daily / unknown ────────────────────────
def test_daily_returns_none_keeps_realtime():
    """daily 返回 None → 日报保持现行 date=None 实时数据(refresh_on_sync 生效)。"""
    assert compute_pregen_window("daily", ref_date=WED) is None


def test_unknown_period_returns_none():
    """未识别的 period(如 range)→ 返回 None 让 caller 走原逻辑, 不应被预生成调用。"""
    assert compute_pregen_window("range", ref_date=WED) is None


# ── compute_pregen_window: snapshot_date = ref_date(今日) ────────
def test_snapshot_date_always_today_not_yesterday():
    """snapshot_date 应该是"今日"(让 matcher 命中), 而非窗口 end(昨日)。

    理由: snapshot_date 是"快照观察点"——"截至今日观察到的昨日整天数据",
    matcher 按 snapshot_date 匹配今日提问, 所以 snapshot_date=今日。
    """
    win = compute_pregen_window("weekly", ref_date=WED)
    assert win is not None
    assert win["snapshot_date"] == WED.isoformat()      # 今日(09-16)
    assert win["end"] == "2026-09-15"                   # 昨日(09-15)


# ── compute_compare_window: 等长前对比窗口 ──────────────────────
def test_compare_window_two_days():
    """2天窗口: 09-14~09-15 → 前对比 09-12~09-13(紧贴 start 之前2天)。"""
    prev_start, prev_end = compute_compare_window("2026-09-14", "2026-09-15")
    assert prev_start == "2026-09-12"
    assert prev_end == "2026-09-13"


def test_compare_window_seven_days():
    """7天窗口: 09-07~09-13 → 前对比 08-31~09-06(紧贴 start 之前7天)。"""
    prev_start, prev_end = compute_compare_window("2026-09-07", "2026-09-13")
    assert prev_start == "2026-08-31"
    assert prev_end == "2026-09-06"


def test_compare_window_one_day():
    """1天窗口: 09-15~09-15 → 前对比 09-14~09-14(紧贴 start 之前1天)。"""
    prev_start, prev_end = compute_compare_window("2026-09-15", "2026-09-15")
    assert prev_start == "2026-09-14"
    assert prev_end == "2026-09-14"


def test_compare_window_monthly_september():
    """整月窗口: 09-01~09-30(30天) → 前对比 08-02~08-31(紧贴 start 之前30天)。

    09-01~09-30 = 30 天; 08-02~08-31 也是 30 天(8 月 31 天), 紧贴 09-01 之前。
    """
    prev_start, prev_end = compute_compare_window("2026-09-01", "2026-09-30")
    assert prev_start == "2026-08-02"
    assert prev_end == "2026-08-31"
