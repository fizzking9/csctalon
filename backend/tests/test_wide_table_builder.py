# -*- coding: utf-8 -*-
"""宽表构建服务测试(wide_table_builder.py)。

覆盖:
- _select_ticket / _select_wallet 纯选择逻辑
- _build_wide_batch DB 集成(退款 + 关联关系 → 宽表行组装)
- _in_scope_refund_nos 行范围(A-20260908-001: 线上+已退款 ∪ 带wallet ∪ 外部投诉SN)
- mr_*(商户退款)字段不再物化
"""

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.device import Device
from app.models.external_complaint import ExternalComplaint
from app.models.refund import Refund
from app.models.refund_wide import RefundWide
from app.models.ticket import ServiceTicket
from app.models.upload import DataMatchRelation
from app.models.wallet import WalletWithdrawal
from app.services.wide_table_builder import (
    _build_wide_batch,
    _in_scope_refund_nos,
    _scope_refund_no_subquery,
    _select_ticket,
    _select_wallet,
)


def _uniq(prefix: str) -> str:
    """生成唯一 SN

    ⚠️ 2026-09-22 起: `_build_wide_batch` 已支持 `commit=False`, 测试不再往库里
    泄漏数据(见下方调用点)。本函数保留唯一性仅为**同一事务内**避免重复键冲突。
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _ticket(ticket_no: str, finish_time=None, **kw) -> ServiceTicket:
    return ServiceTicket(ticket_no=ticket_no, finish_time=finish_time, **kw)


# ---- _select_ticket: 优先 order_no, 否则最新 finish_time ----

def test_select_ticket_prefers_order_no():
    """正向: 有多条关系时优先选 order_no 命中的工单。"""
    tickets = {
        "GDC001": _ticket("GDC001", finish_time=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        "GDC002": _ticket("GDC002", finish_time=datetime(2026, 6, 1, tzinfo=timezone.utc)),
    }
    rels = [("GDC002", "device_sn"), ("GDC001", "order_no")]
    picked = _select_ticket(rels, tickets)
    assert picked.ticket_no == "GDC001"  # order_no 优先, 即使 finish_time 更早


def test_select_ticket_latest_finish_time_when_no_order():
    """边界: 无 order_no 命中时选 finish_time 最新的。"""
    tickets = {
        "GDC001": _ticket("GDC001", finish_time=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        "GDC002": _ticket("GDC002", finish_time=datetime(2026, 6, 1, tzinfo=timezone.utc)),
    }
    rels = [("GDC001", "device_sn"), ("GDC002", "device_sn")]
    picked = _select_ticket(rels, tickets)
    assert picked.ticket_no == "GDC002"


def test_select_ticket_none_when_no_rels():
    """边界: 无关系时返回 None。"""
    assert _select_ticket([], {}) is None


# ---- _select_wallet: 最新 op_time ----

def test_select_wallet_latest_op_time():
    """正向: 多笔钱包记录选 op_time 最新的一笔。"""
    w1 = WalletWithdrawal(op_time=datetime(2026, 1, 1, tzinfo=timezone.utc))
    w1.id = 1
    w2 = WalletWithdrawal(op_time=datetime(2026, 6, 1, tzinfo=timezone.utc))
    w2.id = 2
    wallets = {"1": w1, "2": w2}
    picked = _select_wallet([("1", "device_sn"), ("2", "device_sn")], wallets)
    assert picked.id == 2


# ---- _build_wide_batch DB 集成 ----

def test_build_wide_batch_assembles_row(db):
    """正向: 退款 + 关联工单/钱包 → 宽表行字段组装正确(A-20260908-001: 不再含 mr_*)。"""
    sn = _uniq("SN-WIDE")
    rno = f"RFW{uuid.uuid4().hex[:8]}"
    ticket_no = f"GDC-WIDE-{uuid.uuid4().hex[:6]}"
    # 基础数据(device_sn 需满足 FK)
    db.add(Device(sn=sn))
    db.flush()
    db.add(Refund(
        refund_no=rno, device_sn=sn,
        status="已退款", refund_amount=100, operator="张三",
    ))
    db.add(ServiceTicket(
        ticket_no=ticket_no, device_sn=sn,
        service_item="退费", service_category="投诉",
        finish_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
    ))
    w = WalletWithdrawal(op_amount=100, device_sn=sn)
    db.add(w)
    db.flush()
    # 关联关系
    db.add(DataMatchRelation(
        src_table="refund", src_id=rno,
        dst_table="ticket", dst_id=ticket_no,
        match_type="device_sn", confidence=80, matched_by="auto", status="matched",
    ))
    db.add(DataMatchRelation(
        src_table="refund", src_id=rno,
        dst_table="wallet", dst_id=str(w.id),
        match_type="device_sn", confidence=70, matched_by="auto", status="matched",
    ))
    db.flush()

    # ⚠️ 必须传 commit=False —— 否则内部 db.commit() 会绕过 fixture 事务回滚,
    #    造的数据永久留在测试库(实测累计泄漏 193 行), 污染后续依赖真实数据的用例。
    result = _build_wide_batch(db, [rno], commit=False)
    assert result["built"] == 1
    assert result["skipped"] == 0

    wide = db.get(RefundWide, rno)
    assert wide is not None
    assert wide.status == "已退款"
    assert wide.refund_amount == 100
    assert wide.operator == "张三"
    assert wide.ticket_no == ticket_no
    assert wide.service_category == "投诉"
    assert wide.wallet_id == str(w.id)
    # A-20260908-001: mr_* 字段不再物化(模型已删列)
    assert not hasattr(wide, "wx_refund_no")
    assert not hasattr(wide, "mr_refund_amount")
    assert not hasattr(wide, "merchant_id")
    assert not hasattr(wide, "ticket_finish_time")


def test_build_wide_batch_skips_missing_refund(db):
    """边界: 目标退费单不存在 → skipped+1, 不建行。"""
    result = _build_wide_batch(db, ["RFW-NOT-EXIST"], commit=False)
    assert result["built"] == 0
    assert result["skipped"] == 1
    assert db.get(RefundWide, "RFW-NOT-EXIST") is None


def test_build_wide_batch_external_complaint_agg(db):
    """正向: 同 device_sn 的外部投诉被聚合到宽表行。"""
    sn = _uniq("SN-EC")
    rno = f"RFW{uuid.uuid4().hex[:8]}"
    db.add(Device(sn=sn))
    db.flush()
    db.add(Refund(refund_no=rno, device_sn=sn))
    db.add(ExternalComplaint(
        device_sn=sn,
        complaint_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
    ))
    db.flush()

    result = _build_wide_batch(db, [rno], commit=False)
    assert result["built"] == 1

    wide = db.get(RefundWide, rno)
    assert wide.has_external_complaint is True
    assert wide.external_complaint_count == 1


# ---- _in_scope_refund_nos 行范围(A-20260908-001 方案B) ----

def test_in_scope_includes_online_refunded(db):
    """行范围含「线上+已退款」(主体, 与 E 表一一对应)。"""
    sn = _uniq("SN-SCOPE")
    rno_online_done = f"RFW{uuid.uuid4().hex[:8]}"
    rno_online_pending = f"RFW{uuid.uuid4().hex[:8]}"
    rno_offline_done = f"RFW{uuid.uuid4().hex[:8]}"
    db.add(Device(sn=sn))
    db.flush()
    db.add(Refund(refund_no=rno_online_done, device_sn=sn, refund_method="线上", status="已退款", refund_amount=100))
    db.add(Refund(refund_no=rno_online_pending, device_sn=sn, refund_method="线上", status="申请中", refund_amount=100))
    db.add(Refund(refund_no=rno_offline_done, device_sn=sn, refund_method="线下", status="已退款", refund_amount=100))
    db.flush()

    scope = _in_scope_refund_nos(db)
    assert rno_online_done in scope     # 线上+已退款 → 在
    assert rno_online_pending not in scope  # 线上但申请中 → 不在
    assert rno_offline_done not in scope    # 线下已退款 → 不在(非 A 路)


def test_in_scope_includes_wallet_and_complaint(db):
    """行范围含「带 wallet 关联」与「设备有外部投诉」的退款单(审计/客诉辅助)。"""
    sn_wallet = _uniq("SN-SCOPE-W")
    sn_ec = _uniq("SN-SCOPE-EC")
    sn_plain = _uniq("SN-SCOPE-P")
    rno_wallet = f"RFW{uuid.uuid4().hex[:8]}"
    rno_ec = f"RFW{uuid.uuid4().hex[:8]}"
    rno_plain = f"RFW{uuid.uuid4().hex[:8]}"
    db.add(Device(sn=sn_wallet))
    db.add(Device(sn=sn_ec))
    db.add(Device(sn=sn_plain))
    db.flush()
    # 申请中 + 带 wallet 关联 → 应保留(审计辅助)
    db.add(Refund(refund_no=rno_wallet, device_sn=sn_wallet, refund_method="转入提现账户", status="申请中", refund_amount=100))
    w = WalletWithdrawal(op_amount=100, device_sn=sn_wallet)
    db.add(w)
    db.flush()
    db.add(DataMatchRelation(
        src_table="refund", src_id=rno_wallet, dst_table="wallet", dst_id=str(w.id),
        match_type="device_sn", confidence=70, matched_by="auto", status="matched",
    ))
    # 申请中 + 设备有外部投诉 → 应保留(客诉分类)
    db.add(Refund(refund_no=rno_ec, device_sn=sn_ec, refund_method="线上", status="申请中", refund_amount=100))
    db.add(ExternalComplaint(device_sn=sn_ec, complaint_date=datetime(2026, 7, 1, tzinfo=timezone.utc)))
    # 申请中 + 无任何关联 + 独立SN(无外部投诉)→ 不在范围
    db.add(Refund(refund_no=rno_plain, device_sn=sn_plain, refund_method="线下", status="申请中", refund_amount=100))
    db.flush()

    scope = _in_scope_refund_nos(db)
    assert rno_wallet in scope   # 带 wallet 关联 → 在
    assert rno_ec in scope       # 设备有外部投诉 → 在
    assert rno_plain not in scope  # 无关联申请中(独立SN) → 不在


# ---- 残留清理子查询(A-20260908-001 §4.2 / Reviewer W1) ----

def test_scope_subquery_excludes_out_of_scope_row(db):
    """残留清理子查询应命中「不在行范围」的 refund_wide 行。

    说明: 不实际执行 DELETE(内部 commit 会破坏 db fixture 事务回滚),
    只验证子查询 SQL 能正确圈定"应删除"的残留行(范围外)。
    """
    sn_in = _uniq("SN-SUB-IN")
    sn_out = _uniq("SN-SUB-OUT")
    rno_in = f"RFW{uuid.uuid4().hex[:8]}"
    rno_out = f"RFW{uuid.uuid4().hex[:8]}"
    db.add(Device(sn=sn_in))
    db.add(Device(sn=sn_out))
    db.flush()
    # 范围内: 线上+已退款
    db.add(Refund(refund_no=rno_in, device_sn=sn_in, refund_method="线上", status="已退款", refund_amount=100))
    # 范围外: 线下+申请中(无 wallet/无外部投诉)
    db.add(Refund(refund_no=rno_out, device_sn=sn_out, refund_method="线下", status="申请中", refund_amount=100))
    db.flush()

    # 预置两条宽表行(模拟旧全量残留)
    _now = datetime.now(ZoneInfo("UTC"))
    db.execute(pg_insert(RefundWide).values([
        {"refund_no": rno_in, "refund_method": "线上", "status": "已退款", "refund_amount": 100,
         "has_external_complaint": False, "external_complaint_count": 0, "matched_at": _now},
        {"refund_no": rno_out, "refund_method": "线下", "status": "申请中", "refund_amount": 100,
         "has_external_complaint": False, "external_complaint_count": 0, "matched_at": _now},
    ]).on_conflict_do_nothing(index_elements=["refund_no"]))
    db.flush()

    # 残留判定用 NOT EXISTS(与 build_refund_wide 实现一致, 走 hash anti join)
    sub = _scope_refund_no_subquery()
    no_longer_in_scope = ~exists(
        select(1).where(sub.c.refund_no == RefundWide.refund_no)
    )
    stale = db.scalars(
        select(RefundWide.refund_no).where(no_longer_in_scope)
    ).all()
    assert rno_out in stale       # 范围外行 → 命中(待删)
    assert rno_in not in stale    # 范围内行 → 不命中(保留)
    # 子查询自身含范围内行
    scope_in_sub = db.scalars(select(sub.c.refund_no)).all()
    assert rno_in in scope_in_sub
    assert rno_out not in scope_in_sub
