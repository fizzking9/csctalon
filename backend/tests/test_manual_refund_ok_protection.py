# -*- coding: utf-8 -*-
"""手动退款 ok 保护 + 问题类别标签测试 (2026-09-08, #36)

背景: 共享文档同步每轮会重算 manual_refunds.quality_status/note,
人工在质量页确认(ok)后, 若源台账仍是脏值, 旧逻辑会把它打回 pending, 反复人工处理。

#36 落地:
1. _manual_refund_quality_check 生成的 note 带 [类别] 前缀(年份异常/操作时间异常/时间倒挂)
2. _freeze_ok_manual_refund_rows: 库内已 ok 且本次同型问题 → 冻结(整行不更新/不重报);
   新型问题(类别不同) → 保留并重报
3. 人员污染(登记人/操作人被数字等污染置空) → 补打 [人员污染] pending

本测试锁定:
1. _manual_refund_quality_kind 从 note 提取类别
2. _manual_refund_quality_check note 带标签(时间倒挂)
3. 冻结: 同型 ok 行被剔除
4. 冻结: 新型问题(类别不同)保留
5. 冻结: 库内非 ok(如 pending)不冻结
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.manual_refund import ManualRefund
from app.services.upsert import (
    _freeze_ok_manual_refund_rows,
    _manual_refund_quality_check,
    _manual_refund_quality_kind,
)

_TZ = timezone.utc


class _FakeResult:
    """mock ImportResult: 仅需 add_warning 收集"""

    def __init__(self):
        self.warnings = []

    def add_warning(self, row_idx, msg):
        self.warnings.append(msg)


# ── 1. 类别提取 ─────────────────────────────────────────────
def test_quality_kind_extract():
    assert _manual_refund_quality_kind("[时间倒挂] 操作时间 2025-09-07 早于登记日期…") == "时间倒挂"
    assert _manual_refund_quality_kind("[年份异常] 登记日期年份异常…") == "年份异常"
    assert _manual_refund_quality_kind("[人员污染] 登记人/退款操作人疑似列错位…") == "人员污染"
    # 无标签历史存量
    assert _manual_refund_quality_kind("操作时间 2025-09-07 早于登记日期…") is None
    assert _manual_refund_quality_kind(None) is None
    assert _manual_refund_quality_kind("") is None


# ── 2. 质量校验 note 带标签 ─────────────────────────────────
def test_quality_check_note_tagged_reversal():
    """R3 时间倒挂 → note 带 [时间倒挂] 前缀"""
    r = _FakeResult()
    reg = datetime(2026, 9, 6, 7, 0, tzinfo=_TZ)
    op = datetime(2025, 9, 7, 7, 0, tzinfo=_TZ)   # 早于登记超 1 天
    _, _, status, note = _manual_refund_quality_check(reg, op, 0, r)
    assert status == "pending"
    assert note.startswith("[时间倒挂] ")
    assert r.warnings and "[时间倒挂]" in r.warnings[0]


def test_quality_check_note_tagged_year():
    """R2 年份异常有佐证 → auto_fixed, note 带 [年份异常]"""
    r = _FakeResult()
    reg = datetime(2027, 7, 26, 7, 0, tzinfo=_TZ)  # 未来年
    op = datetime(2026, 8, 29, 7, 5, tzinfo=_TZ)   # 佐证 2026
    fixed, _, status, note = _manual_refund_quality_check(reg, op, 0, r)
    assert status == "auto_fixed"
    assert fixed.year == 2026
    assert note.startswith("[年份异常] ")


# ── 3. 冻结逻辑(库内 ok 行) ────────────────────────────────
def _mk_row(reg, order_no, kind=None, status="pending"):
    """构造一条待写行(去重键完整), 可选质量类别"""
    note = f"[{kind}] 测试问题" if kind else None
    return {
        "register_time": reg,
        "order_no": order_no,
        "device_sn": None,
        "registrant": "客服A",
        "quality_status": status,
        "quality_note": note,
    }


def _seed_ok(db, reg, order_no, kind):
    """库内插一条 ok 行"""
    row = ManualRefund(
        register_time=reg, order_no=order_no, device_sn=None, registrant="客服A",
        is_refunded="是", quality_status="ok",
        quality_note=f"[{kind}] 已人工确认问题" if kind else "人工确认通过",
    )
    db.add(row)
    db.flush()
    return row


def test_freeze_same_kind_ok_row(db):
    """库内 ok + 本次同型 → 冻结剔除"""
    reg = datetime(2026, 9, 6, 7, 0, tzinfo=_TZ)
    _seed_ok(db, reg, "ORD-OK-1", "时间倒挂")
    rows = [_mk_row(reg, "ORD-OK-1", kind="时间倒挂")]
    kept = _freeze_ok_manual_refund_rows(db, rows)
    assert kept == []


def test_freeze_new_kind_not_frozen(db):
    """库内 ok + 本次新型(类别不同) → 保留重报"""
    reg = datetime(2026, 9, 6, 7, 0, tzinfo=_TZ)
    _seed_ok(db, reg, "ORD-OK-2", "时间倒挂")
    rows = [_mk_row(reg, "ORD-OK-2", kind="人员污染")]
    kept = _freeze_ok_manual_refund_rows(db, rows)
    assert len(kept) == 1


def test_untagged_ok_row_sees_new_tagged_kind(db):
    """库内 ok 无标签(历史 confirm) + 本次带标签问题 → 视为新型, 保留重报"""
    reg = datetime(2026, 9, 6, 7, 0, tzinfo=_TZ)
    row = ManualRefund(
        register_time=reg, order_no="ORD-OK-4", device_sn=None, registrant="客服A",
        is_refunded="是", quality_status="ok", quality_note="人工确认通过",
    )
    db.add(row)
    db.flush()
    rows = [_mk_row(reg, "ORD-OK-4", kind="时间倒挂")]
    kept = _freeze_ok_manual_refund_rows(db, rows)
    assert len(kept) == 1


def test_untagged_ok_row_untagged_new_frozen(db):
    """库内 ok 无标签 + 本次也无标签(未知) → 保守冻结(不重复烦人)"""
    reg = datetime(2026, 9, 6, 7, 0, tzinfo=_TZ)
    row = ManualRefund(
        register_time=reg, order_no="ORD-OK-5", device_sn=None, registrant="客服A",
        is_refunded="是", quality_status="ok", quality_note="人工确认通过",
    )
    db.add(row)
    db.flush()
    rows = [_mk_row(reg, "ORD-OK-5", kind=None)]
    kept = _freeze_ok_manual_refund_rows(db, rows)
    assert kept == []


def test_no_freeze_when_not_ok(db):
    """库内 pending(未人工确认) → 不冻结"""
    reg = datetime(2026, 9, 6, 7, 0, tzinfo=_TZ)
    row = ManualRefund(
        register_time=reg, order_no="ORD-PEND-1", device_sn=None, registrant="客服A",
        is_refunded="是", quality_status="pending",
        quality_note="[时间倒挂] 待人工确认",
    )
    db.add(row)
    db.flush()
    rows = [_mk_row(reg, "ORD-PEND-1", kind="时间倒挂")]
    kept = _freeze_ok_manual_refund_rows(db, rows)
    assert len(kept) == 1


def test_no_freeze_no_order_no(db):
    """无 order_no 的行不去重键不完整 → 不冻结(交现有 dropped/upsert 逻辑)"""
    reg = datetime(2026, 9, 6, 7, 0, tzinfo=_TZ)
    _seed_ok(db, reg, "ORD-OK-3", "时间倒挂")
    rows = [{"register_time": reg, "order_no": None, "device_sn": None,
             "registrant": "客服A", "quality_status": None, "quality_note": None}]
    kept = _freeze_ok_manual_refund_rows(db, rows)
    assert len(kept) == 1
