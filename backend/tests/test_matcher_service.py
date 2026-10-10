# -*- coding: utf-8 -*-
"""跨表关联匹配服务测试(matcher.py)。

覆盖匹配规则(见 matcher.py 模块注释):
1. 退费单.platform_order_no == 商户退款.merchant_order_no
2. 退费单.platform_order_no == 工单.order_no
3. 退费单.device_sn == 工单.device_sn
4. 钱包提现.remark 含退费单号 RF...(正则提取)
5. 退费单.device_sn == 钱包提现.device_sn
6. 退费单.merchant_name == 商户.sub_name

纯函数: REFUND_NO_PATTERN(正则) 可零依赖单测。
DB 集成: 各匹配函数用 db fixture 事务隔离。
"""

from sqlalchemy import select

from app.models.device import Device
from app.models.merchant import Merchant
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.upload import DataMatchRelation
from app.models.wallet import WalletWithdrawal
from app.services import matcher


def _mk_device(db, sn: str) -> None:
    """创建 Device(满足 refunds.device_sn / tickets.device_sn 外键约束)。"""
    db.add(Device(sn=sn))
    db.flush()


# ---- REFUND_NO_PATTERN 正则(纯函数) ----

def test_refund_no_pattern_extracts():
    """正向: 从 remark 中提取 RF 开头的退费单号(≥10位数字)。"""
    m = matcher.REFUND_NO_PATTERN.search("退款单号 RF2026090100012345 已处理")
    assert m is not None
    assert m.group(0) == "RF2026090100012345"


def test_refund_no_pattern_min_length():
    """边界: RF 后不足 10 位数字不匹配。"""
    assert matcher.REFUND_NO_PATTERN.search("RF123") is None
    assert matcher.REFUND_NO_PATTERN.search("RF1234567890") is not None  # 恰好 10 位


def test_refund_no_pattern_no_match():
    """边界: 无 RF 单号的 remark 不匹配。"""
    assert matcher.REFUND_NO_PATTERN.search("普通提现备注") is None
    assert matcher.REFUND_NO_PATTERN.search("RF-2026-01") is None  # 连字符打断


# ---- 匹配函数(DB 集成) ----

def _mk_refund(db, refund_no: str, **kw) -> Refund:
    r = Refund(refund_no=refund_no, **kw)
    db.add(r)
    return r


def test_match_refund_merchant_refund(db):
    """正向: 退费单.platform_order_no == 商户退款.merchant_order_no 建立关系。"""
    _mk_refund(db, "RF0000000001", platform_order_no="SP12345")
    mr = MerchantRefund(merchant_order_no="SP12345")
    db.add(mr)
    db.flush()

    count, affected = matcher._match_refund_merchant_refund(db, only_unmatched=True)
    assert count == 1
    assert affected == {"RF0000000001"}
    db.flush()  # 函数内部仅 add_all, 需 flush 让关系可见
    rel = db.scalar(select(DataMatchRelation).where(DataMatchRelation.src_id == "RF0000000001"))
    assert rel is not None
    assert rel.dst_table == "merchant_refund"
    assert rel.match_type == "order_no"
    assert rel.confidence == 100


def test_match_refund_ticket_by_order(db):
    """正向: 退费单.platform_order_no == 工单.order_no 建立关系。"""
    _mk_refund(db, "RF0000000002", platform_order_no="WS999")
    db.add(ServiceTicket(ticket_no="GDC0001", order_no="WS999"))
    db.flush()

    count, affected = matcher._match_refund_ticket_by_order(db, only_unmatched=True)
    assert count == 1
    assert "RF0000000002" in affected


def test_match_refund_ticket_by_sn(db):
    """正向: 退费单.device_sn == 工单.device_sn 建立关系(可能 1:N)。"""
    _mk_device(db, "SN-ABC-001")  # 满足 device_sn 外键
    _mk_refund(db, "RF0000000003", device_sn="SN-ABC-001")
    db.add(ServiceTicket(ticket_no="GDC0002", device_sn="SN-ABC-001"))
    db.add(ServiceTicket(ticket_no="GDC0003", device_sn="SN-ABC-001"))
    db.flush()

    count, _ = matcher._match_refund_ticket_by_sn(db, only_unmatched=True)
    assert count == 2  # 一个退费单关联到 2 个同 SN 工单


def test_match_refund_wallet_by_remark(db):
    """正向: 钱包提现 remark 含 RF 退费单号 → 提取并建立关系。"""
    # 不需要真实 Refund 行, 仅按 remark 提取单号
    db.add(WalletWithdrawal(remark="订单退费转入 RF2026090100012345"))
    db.flush()

    count, affected = matcher._match_refund_wallet_by_remark(db, only_unmatched=True)
    assert count == 1
    assert affected == {"RF2026090100012345"}


def test_match_refund_merchant(db):
    """正向: 退费单.merchant_name == 商户.sub_name 建立关系。"""
    _mk_refund(db, "RF0000000004", merchant_name="上海测试商户")
    db.add(Merchant(sub_name="上海测试商户"))
    db.flush()

    count, affected = matcher._match_refund_merchant(db, only_unmatched=True)
    assert count == 1
    assert "RF0000000004" in affected


def test_match_no_duplicate_when_unmatched_only(db):
    """边界: only_unmatched=True 时已存在关系不重复匹配。"""
    _mk_refund(db, "RF0000000005", platform_order_no="SP555")
    mr = MerchantRefund(merchant_order_no="SP555")
    db.add(mr)
    db.flush()

    # 第一次匹配
    c1, _ = matcher._match_refund_merchant_refund(db, only_unmatched=True)
    assert c1 == 1
    db.flush()
    # 第二次(已有关系)不再新增
    c2, _ = matcher._match_refund_merchant_refund(db, only_unmatched=True)
    assert c2 == 0


def test_match_refund_wallet_by_sn(db):
    """正向: 退费单.device_sn == 钱包提现.device_sn 建立关系。"""
    _mk_device(db, "SN-WALLET-1")  # 满足 device_sn 外键
    _mk_refund(db, "RF0000000006", device_sn="SN-WALLET-1")
    db.add(WalletWithdrawal(device_sn="SN-WALLET-1"))
    db.flush()

    count, affected = matcher._match_refund_wallet_by_sn(db, only_unmatched=True)
    assert count == 1
    assert "RF0000000006" in affected


# ---- 2026-09-05 回归: affected 只含「真正新增关联」的退费单 ----
# 背景: _match_refund_ticket_by_sn / _match_refund_wallet_by_sn 无条件把「有 SN 但无匹配目标」
# 的退费单也计入 affected → match_all 返回 ~15万(全表) → post-sync 宽表每次都全量重建(~2分钟)。

def test_ticket_by_sn_unmatched_sn_not_in_affected(db):
    """反向: 退费单 SN 有值但工单里无该 SN → 不建关系、不进 affected(否则全表进 affected)。"""
    _mk_device(db, "SN-NO-TICKET-1")
    _mk_refund(db, "RF0000000007", device_sn="SN-NO-TICKET-1")  # 无任何工单对应
    db.flush()

    count, affected = matcher._match_refund_ticket_by_sn(db, only_unmatched=True)
    assert count == 0
    assert "RF0000000007" not in affected  # 关键: 不进 affected


def test_ticket_by_sn_mixed_matched_and_unmatched(db):
    """混合: 只有真正匹配到工单的退费单进 affected; 无匹配的 SN 不进。"""
    _mk_device(db, "SN-MIX-1")
    _mk_device(db, "SN-MIX-2")
    _mk_refund(db, "RF0000000008", device_sn="SN-MIX-1")  # 有工单
    _mk_refund(db, "RF0000000009", device_sn="SN-MIX-2")  # 无工单
    db.add(ServiceTicket(ticket_no="GDC0099", device_sn="SN-MIX-1"))
    db.flush()

    count, affected = matcher._match_refund_ticket_by_sn(db, only_unmatched=True)
    assert count == 1
    assert "RF0000000008" in affected
    assert "RF0000000009" not in affected  # 关键: 无匹配不进 affected


def test_wallet_by_sn_unmatched_sn_not_in_affected(db):
    """反向: 退费单 SN 有值但钱包流水里无该 SN → 不建关系、不进 affected。"""
    _mk_device(db, "SN-NO-WALLET-1")
    _mk_refund(db, "RF0000000010", device_sn="SN-NO-WALLET-1")
    db.flush()

    count, affected = matcher._match_refund_wallet_by_sn(db, only_unmatched=True)
    assert count == 0
    assert "RF0000000010" not in affected  # 关键: 不进 affected
