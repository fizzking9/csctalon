# -*- coding: utf-8 -*-
"""宽表构建服务测试(wide_table_builder.py)。

覆盖:
- _select_ticket / _select_merchant_refund / _select_wallet 纯选择逻辑
- _build_wide_batch DB 集成(退款 + 关联关系 → 宽表行组装)
"""

import uuid
from datetime import datetime, timezone

from app.models.device import Device
from app.models.external_complaint import ExternalComplaint
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.refund_wide import RefundWide
from app.models.ticket import ServiceTicket
from app.models.upload import DataMatchRelation
from app.models.wallet import WalletWithdrawal
from app.services.wide_table_builder import (
    _build_wide_batch,
    _select_merchant_refund,
    _select_ticket,
    _select_wallet,
)


def _uniq(prefix: str) -> str:
    """生成唯一 SN(因 _build_wide_batch 内部 commit, 需避免跨运行残留冲突)。"""
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


# ---- _select_merchant_refund: 取第一条命中 ----

def test_select_merchant_refund_first_hit():
    """正向: 商户退款取第一条命中的。"""
    mr1 = MerchantRefund()
    mr1.id = 1
    mr2 = MerchantRefund()
    mr2.id = 2
    picked = _select_merchant_refund([("1", "order_no"), ("2", "order_no")], {"1": mr1, "2": mr2})
    assert picked.id == 1


# ---- _build_wide_batch DB 集成 ----

def test_build_wide_batch_assembles_row(db):
    """正向: 退款 + 关联工单/商户退款/钱包 → 宽表行字段组装正确。"""
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
    mr = MerchantRefund(merchant_id="M123", refund_amount=90)
    db.add(mr)
    db.flush()
    w = WalletWithdrawal(op_amount=100)
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
        dst_table="merchant_refund", dst_id=str(mr.id),
        match_type="order_no", confidence=100, matched_by="auto", status="matched",
    ))
    db.add(DataMatchRelation(
        src_table="refund", src_id=rno,
        dst_table="wallet", dst_id=str(w.id),
        match_type="device_sn", confidence=70, matched_by="auto", status="matched",
    ))
    db.flush()

    result = _build_wide_batch(db, [rno])
    assert result["built"] == 1
    assert result["skipped"] == 0

    wide = db.get(RefundWide, rno)
    assert wide is not None
    assert wide.status == "已退款"
    assert wide.refund_amount == 100
    assert wide.operator == "张三"
    assert wide.ticket_no == ticket_no
    assert wide.service_category == "投诉"
    assert wide.merchant_id == "M123"
    assert wide.wallet_id == str(w.id)


def test_build_wide_batch_skips_missing_refund(db):
    """边界: 目标退费单不存在 → skipped+1, 不建行。"""
    result = _build_wide_batch(db, ["RFW-NOT-EXIST"])
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

    result = _build_wide_batch(db, [rno])
    assert result["built"] == 1

    wide = db.get(RefundWide, rno)
    assert wide.has_external_complaint is True
    assert wide.external_complaint_count == 1
