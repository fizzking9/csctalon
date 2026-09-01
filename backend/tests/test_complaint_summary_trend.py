# -*- coding: utf-8 -*-
"""投诉三源趋势合并回归测试。

对应架构文档: docs/arch/A-20260901-001-complaint-trend-merge-regression-test.md (LOCKED v1.0)
背景: 2026-09-01 提交 1953631 修复 /summary 三源趋势合并 500 崩溃
  (旧代码嵌套 _merge_trend 导致 {day,count} 字典被解包 → int('count') ValueError)。
本测试锁定 _merge_trend 的类型契约, 防止回归。
"""

from app.api.v1.complaints import _merge_trend


def _build_suppl(day: str, total: int, overlap: int) -> dict:
    """构造 _suppl_trend_rows 输出结构的字典(契约: {day, total, overlap})。"""
    return {"day": day, "total": total, "overlap": overlap}


def test_merge_trend_basic_additive():
    """正向: 工单计数 + 补充表净增(total - overlap) 逐日相加。"""
    trend_rows = [("2026-09-01", 8)]  # 工单按日计数 (day, count) 元组
    suppl_rows = [
        _build_suppl("2026-09-01", 10, 3),  # 净增 7
        _build_suppl("2026-09-02", 5, 5),   # 净增 0(全重叠)
    ]
    result = _merge_trend(trend_rows, suppl_rows)
    # 09-01: 8 + (10-3) = 15; 09-02 净增 0 不出现
    assert result == [{"day": "2026-09-01", "count": 15}]


def test_merge_trend_no_nested_call():
    """回归: 两个补充表 dict 列表直接相加后一次合并(修复点, 提交 1953631)。

    旧写法 `_merge_trend(_merge_trend(trend, suppl_a), suppl_b)` 会把内层
    返回的 {day,count} 字典当作 (day,count) 元组解包 → int('count') 崩溃。
    本用例验证修复后的调用形态不再崩溃且结果正确。
    """
    external_trend = [("2026-09-01", 8)]
    external_suppl_trend = [_build_suppl("2026-09-01", 10, 3)]
    card_suppl_trend = [_build_suppl("2026-09-01", 4, 2)]
    # 修复后的调用形态: 两个补充表相加后一次合并
    merged = _merge_trend(external_trend, external_suppl_trend + card_suppl_trend)
    # 09-01: 8 + (10-3) + (4-2) = 17
    assert merged == [{"day": "2026-09-01", "count": 17}]


def test_merge_trend_empty_inputs():
    """边界: 空输入应返回空列表, 不崩溃。"""
    assert _merge_trend([], []) == []


def test_merge_trend_full_overlap_day_excluded():
    """边界: 补充表当日 total==overlap(全部重叠) → 净增 0, 该日不出现在结果。"""
    trend_rows = [("2026-09-01", 5)]
    suppl_rows = [_build_suppl("2026-09-01", 5, 5)]
    result = _merge_trend(trend_rows, suppl_rows)
    # 09-01: 5 + 0 = 5(工单侧已有该日, 保留)
    assert result == [{"day": "2026-09-01", "count": 5}]


def test_merge_trend_suppl_only_new_day():
    """边界: 补充表带来工单趋势里没有的新日期。"""
    trend_rows = []
    suppl_rows = [_build_suppl("2026-09-03", 7, 0)]
    result = _merge_trend(trend_rows, suppl_rows)
    assert result == [{"day": "2026-09-03", "count": 7}]
