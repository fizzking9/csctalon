"""#299 device_combo 历史切片回补: 纯函数单测

覆盖: 夜间窗口判定 / 自适应片宽 / 缺失日分段切块 / 触顶折半 / 窗口时间换算。
不触网不触库(BMS 导出/导入链路靠生产夜间首跑 + 凭证核验)。
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import combo_backfill as cb

CN = timezone(timedelta(hours=8))


class TestNightWindow:
    def test_window_bounds(self):
        assert cb.in_night_window(datetime(2026, 10, 11, 1, 0, tzinfo=CN)) is True
        assert cb.in_night_window(datetime(2026, 10, 11, 4, 44, tzinfo=CN)) is True
        # 04:45 整已超出窗口
        assert cb.in_night_window(datetime(2026, 10, 11, 4, 45, tzinfo=CN)) is False
        assert cb.in_night_window(datetime(2026, 10, 11, 0, 59, tzinfo=CN)) is False
        assert cb.in_night_window(datetime(2026, 10, 11, 5, 0, tzinfo=CN)) is False
        assert cb.in_night_window(datetime(2026, 10, 11, 8, 30, tzinfo=CN)) is False

    def test_night_deadline(self):
        dl = cb.night_deadline(datetime(2026, 10, 11, 1, 5, tzinfo=CN))
        assert dl == datetime(2026, 10, 11, 4, 45, tzinfo=CN)


class TestSliceWidth:
    def test_era_widths(self):
        assert cb.slice_width_for(date(2024, 1, 1)) == 30
        assert cb.slice_width_for(date(2024, 8, 31)) == 30
        assert cb.slice_width_for(date(2024, 9, 1)) == 21
        assert cb.slice_width_for(date(2025, 5, 31)) == 21
        assert cb.slice_width_for(date(2025, 6, 1)) == 14
        assert cb.slice_width_for(date(2025, 12, 31)) == 14
        assert cb.slice_width_for(date(2026, 1, 1)) == 7
        assert cb.slice_width_for(date(2026, 10, 9)) == 7


class TestPlanSlices:
    def test_empty(self):
        assert cb.plan_slices([]) == []

    def test_single_run_2024_monthly(self):
        # 2024-01-01 起连续 35 天 → 30 + 5 两片
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(35)]
        slices = cb.plan_slices(days)
        assert slices == [
            (date(2024, 1, 1), date(2024, 1, 30)),
            (date(2024, 1, 31), date(2024, 2, 4)),
        ]

    def test_gap_splits_runs(self):
        # 连续 3 天, 断 2 天, 再连续 2 天 → 两段两片
        days = [
            date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3),
            date(2026, 3, 6), date(2026, 3, 7),
        ]
        slices = cb.plan_slices(days)
        assert slices == [
            (date(2026, 3, 1), date(2026, 3, 3)),
            (date(2026, 3, 6), date(2026, 3, 7)),
        ]

    def test_2026_weekly_chunks(self):
        days = [date(2026, 6, 1) + timedelta(days=i) for i in range(15)]
        slices = cb.plan_slices(days)
        assert slices == [
            (date(2026, 6, 1), date(2026, 6, 7)),
            (date(2026, 6, 8), date(2026, 6, 14)),
            (date(2026, 6, 15), date(2026, 6, 15)),
        ]

    def test_slices_partition_all_missing_days(self):
        """切片并集必须恰好等于缺失日集合(不重不漏)——回补正确性的核心不变量。"""
        days = [date(2025, 1, 1) + timedelta(days=i) for i in range(100)
                if i % 3 != 0]  # 制造断点
        slices = cb.plan_slices(days)
        covered: set[date] = set()
        for s, e in slices:
            d = s
            while d <= e:
                assert d not in covered, f"重叠: {d}"
                covered.add(d)
                d += timedelta(days=1)
        assert covered == set(days)


class TestSplitRange:
    def test_split_week(self):
        children = cb.split_range(date(2026, 6, 1), date(2026, 6, 7))
        assert children == [
            (date(2026, 6, 1), date(2026, 6, 4)),
            (date(2026, 6, 5), date(2026, 6, 7)),
        ]

    def test_split_children_partition_parent(self):
        s, e = date(2025, 1, 10), date(2025, 2, 20)
        children = cb.split_range(s, e)
        assert len(children) == 2
        assert children[0][0] == s
        assert children[1][1] == e
        assert children[0][1] + timedelta(days=1) == children[1][0]

    def test_single_day_unsplittable(self):
        # 单日触顶(单日 10 万行)无法再切 → 空列表, 由调用方记 failed
        assert cb.split_range(date(2026, 6, 1), date(2026, 6, 1)) == []

    def test_two_days_split(self):
        assert cb.split_range(date(2026, 6, 1), date(2026, 6, 2)) == [
            (date(2026, 6, 1), date(2026, 6, 1)),
            (date(2026, 6, 2), date(2026, 6, 2)),
        ]


class TestWindowDatetimes:
    def test_window_bounds_beijing(self):
        ws, we = cb.window_datetimes(date(2024, 1, 1), date(2024, 1, 7))
        assert ws == datetime(2024, 1, 1, 0, 0, 0, tzinfo=CN)
        assert we == datetime(2024, 1, 7, 23, 59, 59, tzinfo=CN)


@pytest.mark.parametrize("rows,expected_capped", [
    (679, False),
    (99_999, False),
    (100_000, True),
    (100_005, True),
])
def test_cap_threshold(rows, expected_capped):
    assert (rows >= cb.CAP_ROWS) is expected_capped
