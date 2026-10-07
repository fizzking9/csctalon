"""L-3 manual_refunds 2031 乱年份打标脚本测试（工单 #209，#207 选项③）

核心验证：定位条件、待打/已打拆分、行数安全门、只打标不改 register_time、幂等。
造数用唯一单号前缀（全表扫描型函数，断言时按前缀子集核对，不依赖全表无残留）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.manual_refund import ManualRefund
from scripts import mark_manual_future_year_209 as m209

_CN = timezone(timedelta(hours=8))
PREFIX = f"t209_{uuid4().hex[:8]}"
Y2031 = datetime(2031, 5, 20, 10, 0, tzinfo=_CN)
Y2026 = datetime(2026, 5, 20, 10, 0, tzinfo=_CN)


def _mk(db, tag, *, when, amount="67.80", quality=None, sn=None, order_no=None):
    row = ManualRefund(
        order_no=order_no or f"{PREFIX}-{tag}",
        register_time=when,
        device_sn=sn,
        refund_amount=amount,
        is_refunded="是",
        quality_status=quality,
    )
    db.add(row)
    db.flush()
    return row


def _mine(db):
    """本次用例造的行（按唯一单号前缀）。"""
    return list(db.scalars(
        select(ManualRefund).where(ManualRefund.order_no.like(f"{PREFIX}-%"))
    ))


def test_future_year_rows_locate_only_2030_plus(db):
    a = _mk(db, "f1", when=Y2031)
    b = _mk(db, "f2", when=Y2031, amount="67.40")
    c = _mk(db, "f3", when=Y2031, amount="67.80")
    normal = _mk(db, "ok", when=Y2026)

    hit_ids = {r.id for r in m209.future_year_rows(db)}
    # 3 行 2031 全部命中；2026 正常行不命中
    assert {a.id, b.id, c.id} <= hit_ids
    assert normal.id not in hit_ids


def test_split_pending_and_already(db):
    p1 = _mk(db, "p1", when=Y2031)
    p2 = _mk(db, "p2", when=Y2031)
    done = _mk(db, "done", when=Y2031, quality="dup_residual")

    mine = _mine(db)
    pending, already = m209.split_pending(mine)
    assert {r.id for r in pending} == {p1.id, p2.id}
    assert {r.id for r in already} == {done.id}


def test_enforce_count_gate():
    m209.enforce_count(3, 3)  # 相符不抛
    with pytest.raises(SystemExit):
        m209.enforce_count(2, 3)
    with pytest.raises(SystemExit):
        m209.enforce_count(99, 3)


def test_mark_rows_writes_status_note_keeps_register_time(db):
    a = _mk(db, "m1", when=Y2031, amount="67.80")
    b = _mk(db, "m2", when=Y2031, amount="67.40")
    before_reg = a.register_time

    n = m209.mark_rows([a, b], "2026-10-07")
    db.flush()

    assert n == 2
    for r in (a, b):
        assert r.quality_status == "dup_residual"
        assert r.quality_note and "#207" in r.quality_note and "2031" in r.quality_note
        # 关键：register_time 原值不动（保留错误留痕），金额不动
        assert r.register_time == before_reg
        assert float(r.refund_amount) in (67.80, 67.40)

    # 幂等：打标后再拆分，全部进 already，pending 为空
    pending, already = m209.split_pending([a, b])
    assert pending == [] and len(already) == 2


def test_dry_run_does_not_mutate(db):
    # 模拟 dry-run：只定位/拆分、不调 mark_rows → 行保持未打标
    a = _mk(db, "d1", when=Y2031)
    rows = [r for r in m209.future_year_rows(db) if r.order_no == a.order_no]
    pending, _ = m209.split_pending(rows)
    assert len(pending) == 1
    db.flush()
    assert a.quality_status is None  # 未写标
