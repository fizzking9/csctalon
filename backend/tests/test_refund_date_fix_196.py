# -*- coding: utf-8 -*-
"""#196 退款日期缺陷修复验收

范围:
1. YY/MMDD 紧凑日期('26/0920' → 2026-09-20): 入库侧 _to_datetime + 审计侧 parse_doc_datetime
2. 空登记日期邻近填充 _fill_empty_register_time(保守: 上下行同一北京自然日才填)
"""
from datetime import datetime, timezone

from app.services.manual_refund_sync import _FILL_MARK_KEY, _fill_empty_register_time
from app.services.refund_data_audit import parse_doc_datetime
from app.services.upsert import _to_datetime

# 2026-09-20 00:00(北京) = 2026-09-19T16:00:00Z
_UTC_0920 = datetime(2026, 9, 19, 16, 0, tzinfo=timezone.utc)


class TestYySlashCompactDate:
    """28 行 '26/0920' 形态（kdocs 9月份登记明细 row 1311~1374 实测）"""

    def test_core_parse(self):
        assert _to_datetime("26/0920") == _UTC_0920

    def test_core_parse_whitespace(self):
        assert _to_datetime(" 26/0920 ") == _UTC_0920

    def test_audit_parse_naive_beijing(self):
        assert parse_doc_datetime("26/0920") == datetime(2026, 9, 20)

    def test_invalid_month(self):
        assert _to_datetime("26/1332") is None
        assert parse_doc_datetime("26/1332") is None

    def test_invalid_day(self):
        assert _to_datetime("26/0241") is None
        assert _to_datetime("26/0230") is None  # 2月30日不存在

    def test_no_misfire_on_other_shapes(self):
        """既有形态不受影响(回归)"""
        assert _to_datetime("2026/6/14") is not None
        assert _to_datetime("20260614") is not None
        assert _to_datetime("2026-06-14") is not None

    def test_yy_slash_strict_shape(self):
        """YY/MMDD 之外的斜杠形态不进新分支(审计侧无 pd 兜底, 严格判 None)"""
        assert parse_doc_datetime("26/09/20") is None   # YY/MM/DD 不认
        assert parse_doc_datetime("2026/0920") is None  # YYYY/MMDD 不认


class TestFillEmptyRegisterTime:
    """空登记日期邻近填充(登记明细 row 2413 形态)"""

    def _row(self, date_val, order="ORDER-1", sn="SN123456"):
        return {"登记日期": date_val, "订单号": order, "设备号": sn,
                "登记人": "张三", "退款金额": "10.0"}

    def test_fill_when_neighbors_same_day(self):
        rows = [self._row("2026/8/29 10:00", "O1"),
                self._row("", "O2"),
                self._row("2026/8/29 11:00", "O3")]
        assert _fill_empty_register_time(rows) == 1
        assert rows[1]["登记日期"] == "2026/8/29 10:00"  # 取上一行原值
        assert rows[1][_FILL_MARK_KEY] is True

    def test_no_fill_when_neighbors_differ(self):
        """上下行不同天 → 证据不足不猜, 保持空"""
        rows = [self._row("2026/8/28", "O1"),
                self._row("", "O2"),
                self._row("2026/8/30", "O3")]
        assert _fill_empty_register_time(rows) == 0
        assert rows[1]["登记日期"] == ""

    def test_no_fill_when_one_side_missing(self):
        rows = [self._row("", "O1"), self._row("2026/8/29", "O2")]
        assert _fill_empty_register_time(rows) == 0

    def test_skip_pure_template_row(self):
        """只有日期空格、无业务字段的预留行不填(模板噪音)"""
        rows = [self._row("2026/8/29", "O1"),
                {"登记日期": ""},
                self._row("2026/8/29", "O2")]
        assert _fill_empty_register_time(rows) == 0

    def test_yy_slash_neighbor_fill(self):
        """邻近行是 YY/MMDD 新形态也可作为锚点"""
        rows = [self._row("26/0920", "O1"),
                self._row(None, "O2"),
                self._row("2026/9/20 15:00", "O3")]
        assert _fill_empty_register_time(rows) == 1
        assert rows[1]["登记日期"] == "26/0920"

    def test_existing_date_untouched(self):
        rows = [self._row("2026/8/28", "O1"), self._row("2026/9/01", "O2")]
        assert _fill_empty_register_time(rows) == 0
        assert rows[1]["登记日期"] == "2026/9/01"
