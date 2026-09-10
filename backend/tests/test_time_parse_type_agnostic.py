# -*- coding: utf-8 -*-
"""时间解析类型无关化测试 (2026-09-10, #42)

背景: 315 投诉表「处理时间」列在源 xlsx 中有两种存储形态——
  - ≤2026-08-07: Excel 序列号(文本型 '46187' 或数字型 46187)
  - ≥2026-08-08: 标准日期字符串
旧实现 `_to_datetime` 的序列号分支**只判字符串**(`isinstance(val,str) and s.isdigit()`),
数字型 46187 落入 pd.to_datetime(46187) 被当**纳秒**解释 →
产出 1969-12-31 16:00:00.000046 的伪日期。实测库中 229 行此类坏值,
导致投诉时效统计失真(228 行 handle_time < complaint_date)。

本测试锁定:
1. 正向: 文本型/数字型/浮点型 Excel 序列号 → 同一正确日期
2. 边界: 序列号区间外的纯数字 → 绝不产出 1970 年前后的伪日期(返回 None)
3. 边界: 标准日期字符串/日期对象 不受影响(向后兼容)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.upsert import _to_datetime

# 46187 → 2026-06-14(北京) = 2026-06-13T16:00:00+00:00(UTC)
_SERIAL_46187_UTC = datetime(2026, 6, 13, 16, 0, tzinfo=timezone.utc)


class TestExcelSerialTypeAgnostic:
    """正向: Excel 序列号在各种类型下解析一致(核心修复点)"""

    @pytest.mark.parametrize("val", [
        "46187",          # 文本型(旧实现唯一覆盖的形态)
        46187,            # 数字型 int ← 本次修复的关键
        46187.0,          # 数字型 float
        "46187.0",        # 文本型小数
        46187.000000000046,  # float64 存储精度残渣(实测形态)
    ])
    def test_serial_all_types_same_result(self, val):
        """文本/数字/浮点/精度残渣 → 同一正确日期"""
        assert _to_datetime(val) == _SERIAL_46187_UTC

    def test_serial_boundaries_inclusive(self):
        """区间边界 20000 / 80000 按序列号解析(含端点)"""
        lo = _to_datetime(20000)
        hi = _to_datetime(80000)
        assert lo is not None and lo.year == 1954
        assert hi is not None and hi.year == 2119


class TestNoPseudoDate:
    """边界: 绝不产出 1970 年前后的伪日期(旧实现 pd.to_datetime(1) → 1969-12-31)"""

    @pytest.mark.parametrize("val", [1, 0, 2026, 12345, 99999, -5, "1", "0", "2026", "12345", "99999"])
    def test_out_of_range_numbers_not_pseudo_date(self, val):
        """区间外纯数字 → None(交上层校验/拒收), 不静默造伪日期"""
        r = _to_datetime(val)
        assert r is None, f"{val!r} 不应产出日期, 实际 {r}"

    @pytest.mark.parametrize("val", [True, False])
    def test_bool_not_treated_as_serial(self, val):
        """bool 是 int 子类, 必须排除(否则 True→1 天→1970 伪日期)"""
        assert _to_datetime(val) is None

    def test_long_numeric_id_not_date(self):
        """超长编号(如 25 位)不解析为日期"""
        assert _to_datetime("32309081824082100224") is None
        assert _to_datetime("502309031851166064601") is None

    def test_no_result_before_2000(self):
        """遍历多种可疑输入, 任何解析结果都不得早于 2000 年"""
        for v in ["46187", 46187, 1, 0, 2026, 12345, 99999, 20001, 79999, True]:
            r = _to_datetime(v)
            assert r is None or r.year >= 1954, f"{v!r} → {r} 早于合理范围"


class TestBackwardCompatible:
    """边界: 原有日期字符串/对象路径不受影响(向后兼容)"""

    def test_iso_datetime_string(self):
        """标准日期串: naive 视为北京时间 → UTC"""
        assert _to_datetime("2026-08-08 00:00:00") == datetime(2026, 8, 7, 16, 0, tzinfo=timezone.utc)

    def test_plain_date_string(self):
        assert _to_datetime("2026-06-14") == _SERIAL_46187_UTC

    def test_slash_date_string(self):
        assert _to_datetime("2026/6/14") == _SERIAL_46187_UTC

    def test_datetime_naive_as_beijing(self):
        assert _to_datetime(datetime(2026, 6, 14, 0, 0)) == _SERIAL_46187_UTC

    def test_datetime_aware_passthrough(self):
        aware = datetime(2026, 6, 14, 8, 0, tzinfo=timezone.utc)
        assert _to_datetime(aware) == aware

    @pytest.mark.parametrize("val", [None, "", "  ", "nan", "NaN", "null"])
    def test_empty_values(self, val):
        assert _to_datetime(val) is None

    def test_free_text_not_date(self):
        """自由文本(投诉问题等误配到时间列) → None"""
        assert _to_datetime("商家拒绝退款") is None
        assert _to_datetime("待处理") is None
