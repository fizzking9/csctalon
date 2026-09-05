# -*- coding: utf-8 -*-
"""manual_refunds 辅助列 + 时间质量校验测试(2026-09-05, #20)

背景: 手动退款登记表导入时, 源 Excel 登记日期可能含异常年份(实测 2027~2035 未来年,
集中在 X月26日)。upsert/manual_refund_sync 导入时用 _manual_refund_quality_check:
  - 有 op_time 佐证 → auto_fixed 修正年份
  - 无佐证 → pending 待人工
本次新增 register_time_raw 辅助列: auto_fixed 纠错发生时保留原始值(审计/回滚)。

本测试锁定:
1. _manual_refund_quality_check: 未来年份+有 op_time → auto_fixed 修正年份
2. _manual_refund_quality_check: 未来年份+无 op_time → pending 不误改
3. (辅助列写入在调用层, 由 upsert 集成测试覆盖)
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.upsert import _manual_refund_quality_check

_TZ = timezone.utc


class _FakeResult:
    """mock ImportResult: 仅需 add_warning 收集"""

    def __init__(self):
        self.warnings = []

    def add_warning(self, row_idx, msg):
        self.warnings.append(msg)


def test_future_year_with_op_time_autofixed():
    """R2: 未来年份 register_time + 有 op_time 佐证 → auto_fixed 修正年份"""
    r = _FakeResult()
    reg = datetime(2027, 7, 26, 7, 0, tzinfo=_TZ)     # 脏: 未来年份
    op = datetime(2026, 8, 29, 7, 5, tzinfo=_TZ)      # 佐证: 2026
    fixed, op_out, status, note = _manual_refund_quality_check(reg, op, 0, r)
    assert status == "auto_fixed"
    assert fixed.year == 2026          # 年份被修正
    assert fixed.month == 7 and fixed.day == 26  # 月日保留(与原始一致)
    assert "原始" in (note or "") and "2027" in (note or "")
    assert len(r.warnings) == 1


def test_future_year_no_op_time_pending():
    """R2: 未来年份 register_time + 无 op_time → pending, 不误改值"""
    r = _FakeResult()
    reg = datetime(2031, 8, 26, 7, 0, tzinfo=_TZ)     # 脏: 未来年份
    out, op_out, status, note = _manual_refund_quality_check(reg, None, 0, r)
    assert status == "pending"
    assert out == reg                 # 不自动改(保留原值待人工)
    assert "待人工" in (note or "")


def test_normal_time_ok():
    """正常年份 → 无标记(None)"""
    r = _FakeResult()
    reg = datetime(2026, 7, 17, 7, 0, tzinfo=_TZ)
    out, op_out, status, note = _manual_refund_quality_check(reg, None, 0, r)
    assert status is None
    assert out == reg
    assert note is None
    assert r.warnings == []


def test_op_time_reversal_pending():
    """R3: op_time 早于 register_time 超1天 → pending(倒挂)"""
    r = _FakeResult()
    reg = datetime(2026, 9, 5, 7, 0, tzinfo=_TZ)
    op = datetime(2026, 9, 1, 7, 0, tzinfo=_TZ)      # 早 4 天
    out, op_out, status, note = _manual_refund_quality_check(reg, op, 0, r)
    assert status == "pending"
    assert "早于" in (note or "")


def test_future_op_time_autofixed_by_register():
    """R4(2026-09-05): op_time 未来年份(2029) + register_time 正常(2026) → auto_fixed 修正 op_time 年份"""
    r = _FakeResult()
    reg = datetime(2026, 9, 5, 7, 0, tzinfo=_TZ)      # 正常
    op = datetime(2029, 9, 5, 7, 0, tzinfo=_TZ)       # 脏: 未来年份(录入笔误)
    reg_out, op_out, status, note = _manual_refund_quality_check(reg, op, 0, r)
    assert status == "auto_fixed"
    assert op_out is not None and op_out.year == 2026  # op_time 年份被修正为登记年
    assert op_out.month == 9 and op_out.day == 5       # 月日时分保留
    assert reg_out == reg                              # register_time 不受影响
    assert "操作时间年份异常" in (note or "")


def test_future_op_time_no_register_pending():
    """R4: op_time 未来年份 + register_time 也为异常 → pending 不误改"""
    r = _FakeResult()
    reg = datetime(2029, 8, 26, 7, 0, tzinfo=_TZ)     # 脏
    op = datetime(2029, 8, 26, 7, 0, tzinfo=_TZ)      # 未来
    reg_out, op_out, status, note = _manual_refund_quality_check(reg, op, 0, r)
    assert status == "pending"
    assert op_out == op           # 不自动改
    assert reg_out == reg
    assert "待人工" in (note or "")


def test_future_op_time_leap_day_safe(_test_session=None):
    """R4 闰年兜底(2026-09-06 审查修复): op_time=2028-02-29(闰年未来) + register 2026-02 邻近 → 修正为 2026-02-28 不抛错"""
    from app.services.upsert import _manual_refund_quality_check

    r = _FakeResult()
    # register_time 2026-02-27 正常(与修正后 2/28 相邻, 不倒挂); op 2028-02-29 闰年未来触发 R4
    reg = datetime(2026, 2, 27, 7, 0, tzinfo=_TZ)
    op = datetime(2028, 2, 29, 7, 0, tzinfo=_TZ)      # replace(year=2026) 会抛 ValueError(2/29→非闰)
    reg_out, op_out, status, note = _manual_refund_quality_check(reg, op, 0, r)
    # 应 auto_fixed(不抛错), op_time 修正到 2026-02-28(2/28 兜底)
    assert status == "auto_fixed"
    assert op_out is not None
    assert op_out.year == 2026
    assert (op_out.month, op_out.day) == (2, 28)  # 闰日退到 2/28
    assert reg_out == reg


def test_future_register_leap_day_safe(_test_session=None):
    """R2 闰年兜底(2026-09-06 审查修复): register_time=2028-02-29 + op 2026-02-27 佐证 → 修正 2026-02-28 不抛错"""
    from app.services.upsert import _manual_refund_quality_check

    r = _FakeResult()
    reg = datetime(2028, 2, 29, 7, 0, tzinfo=_TZ)     # 脏: 未来闰日
    op = datetime(2026, 2, 27, 7, 0, tzinfo=_TZ)      # 佐证: 2026(与修正后相邻, 不倒挂)
    reg_out, op_out, status, note = _manual_refund_quality_check(reg, op, 0, r)
    assert status == "auto_fixed"
    assert reg_out is not None
    assert reg_out.year == 2026
    assert (reg_out.month, reg_out.day) == (2, 28)    # 闰日退到 2/28
    assert op_out == op
