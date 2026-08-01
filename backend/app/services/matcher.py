"""跨表关联匹配服务

匹配规则按优先级:
1. 退费单.platform_order_no == 商户退款.merchant_order_no  (精确)
2. 退费单.platform_order_no == 工单.order_no  (精确)
3. 退费单.device_sn == 工单.device_sn  (精确)
4. 钱包提现.remark 含退费单号 RF...  (正则提取)
5. 退费单.device_sn == 钱包提现.device_sn  (精确)
6. 退费单.merchant_name == 商户.sub_name  (精确)

每次新数据导入后,对新数据 + 之前未关联数据重跑匹配(持续对齐)
"""

import re
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.merchant import Merchant
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.upload import DataMatchRelation
from app.models.wallet import WalletWithdrawal

REFUND_NO_PATTERN = re.compile(r"RF\d{10,}")


def match_all(db: Session, only_unmatched: bool = True) -> dict[str, int]:
    """执行全量匹配,返回各类型新增匹配数

    Args:
        only_unmatched: True=只处理之前未匹配的记录(增量),False=全量重跑

    Note:
        本函数仅负责产出 DataMatchRelation 关联关系。
        refund_wide 物化宽表的重建由调用方(bms_sync/uploads 钩子)在 match_all 之后显式触发,
        以便在源数据更新但无新增关联时也能刷新宽表。
    """
    stats = {
        "refund_merchant_refund": _match_refund_merchant_refund(db, only_unmatched),
        "refund_ticket_order": _match_refund_ticket_by_order(db, only_unmatched),
        "refund_ticket_sn": _match_refund_ticket_by_sn(db, only_unmatched),
        "refund_wallet_remark": _match_refund_wallet_by_remark(db, only_unmatched),
        "refund_wallet_sn": _match_refund_wallet_by_sn(db, only_unmatched),
        "refund_merchant": _match_refund_merchant(db, only_unmatched),
    }
    db.commit()
    return stats


def _exists_relation(db: Session, src_table: str, src_id: str, dst_table: str, match_type: str) -> bool:
    """检查是否已存在某类关联"""
    return bool(
        db.scalar(
            select(func.count())
            .select_from(DataMatchRelation)
            .where(
                DataMatchRelation.src_table == src_table,
                DataMatchRelation.src_id == src_id,
                DataMatchRelation.dst_table == dst_table,
                DataMatchRelation.match_type == match_type,
                DataMatchRelation.status == "matched",
            )
        )
    )


def _add_relation(db: Session, src_table: str, src_id: str, dst_table: str, dst_id: str, match_type: str, confidence: int = 100) -> bool:
    """新增关联(已存在则跳过),返回是否新增"""
    if _exists_relation(db, src_table, src_id, dst_table, match_type):
        return False
    db.add(
        DataMatchRelation(
            src_table=src_table,
            src_id=src_id,
            dst_table=dst_table,
            dst_id=dst_id,
            match_type=match_type,
            confidence=confidence,
            matched_by="auto",
            status="matched",
        )
    )
    return True


def _match_refund_merchant_refund(db: Session, only_unmatched: bool) -> int:
    """退费单.platform_order_no == 商户退款.merchant_order_no"""
    refunds = db.scalars(select(Refund).where(Refund.platform_order_no.isnot(None))).all()
    # 建立 merchant_order_no -> merchant_refund.id 索引(用 id 作关联键,与 wallet 一致;
    # wx_refund_no 在 Excel 导出时被 float 截断且支付行为 0,已非唯一不可作关联键)
    mr_map = {}
    for mr in db.scalars(select(MerchantRefund).where(MerchantRefund.merchant_order_no.isnot(None))).all():
        key = (mr.merchant_order_no or "").strip()
        if key:
            mr_map[key] = str(mr.id)
    count = 0
    for r in refunds:
        key = (r.platform_order_no or "").strip()
        if not key:
            continue
        if only_unmatched and _exists_relation(db, "refund", r.refund_no, "merchant_refund", "order_no"):
            continue
        if key in mr_map:
            if _add_relation(db, "refund", r.refund_no, "merchant_refund", mr_map[key], "order_no"):
                count += 1
    return count


def _match_refund_ticket_by_order(db: Session, only_unmatched: bool) -> int:
    """退费单.Platform_order_no == 工单.order_no"""
    refunds = db.scalars(select(Refund).where(Refund.platform_order_no.isnot(None))).all()
    ticket_map = {}
    for t in db.scalars(select(ServiceTicket).where(ServiceTicket.order_no.isnot(None))).all():
        key = (t.order_no or "").strip()
        if key:
            ticket_map[key] = t.ticket_no
    count = 0
    for r in refunds:
        key = (r.platform_order_no or "").strip()
        if not key:
            continue
        if only_unmatched and _exists_relation(db, "refund", r.refund_no, "ticket", "order_no"):
            continue
        if key in ticket_map:
            if _add_relation(db, "refund", r.refund_no, "ticket", ticket_map[key], "order_no"):
                count += 1
    return count


def _match_refund_ticket_by_sn(db: Session, only_unmatched: bool) -> int:
    """退费单.device_sn == 工单.device_sn"""
    refunds = db.scalars(select(Refund).where(Refund.device_sn.isnot(None))).all()
    # SN -> [ticket_no] (一个SN可能多个工单)
    ticket_sn_map = {}
    for t in db.scalars(select(ServiceTicket).where(ServiceTicket.device_sn.isnot(None))).all():
        ticket_sn_map.setdefault(t.device_sn, []).append(t.ticket_no)
    count = 0
    for r in refunds:
        if not r.device_sn:
            continue
        if only_unmatched and _exists_relation(db, "refund", r.refund_no, "ticket", "device_sn"):
            continue
        for tn in ticket_sn_map.get(r.device_sn, []):
            if _add_relation(db, "refund", r.refund_no, "ticket", tn, "device_sn", confidence=80):
                count += 1
    return count


def _match_refund_wallet_by_remark(db: Session, only_unmatched: bool) -> int:
    """钱包提现.remark 含退费单号 RF..."""
    wallets = db.scalars(select(WalletWithdrawal).where(WalletWithdrawal.remark.isnot(None))).all()
    count = 0
    for w in wallets:
        if not w.remark:
            continue
        m = REFUND_NO_PATTERN.search(w.remark)
        if not m:
            continue
        refund_no = m.group(0)
        if only_unmatched and _exists_relation(db, "refund", refund_no, "wallet", "refund_no_in_remark"):
            continue
        if _add_relation(db, "refund", refund_no, "wallet", str(w.id), "refund_no_in_remark"):
            count += 1
    return count


def _match_refund_wallet_by_sn(db: Session, only_unmatched: bool) -> int:
    """退费单.device_sn == 钱包提现.device_sn"""
    refunds = db.scalars(select(Refund).where(Refund.device_sn.isnot(None))).all()
    wallet_sn_map = {}
    for w in db.scalars(select(WalletWithdrawal).where(WalletWithdrawal.device_sn.isnot(None))).all():
        wallet_sn_map.setdefault(w.device_sn, []).append(w.id)
    count = 0
    for r in refunds:
        if not r.device_sn:
            continue
        if only_unmatched and _exists_relation(db, "refund", r.refund_no, "wallet", "device_sn"):
            continue
        for wid in wallet_sn_map.get(r.device_sn, []):
            if _add_relation(db, "refund", r.refund_no, "wallet", str(wid), "device_sn", confidence=70):
                count += 1
    return count


def _match_refund_merchant(db: Session, only_unmatched: bool) -> int:
    """退费单.merchant_name == 商户.sub_name"""
    refunds = db.scalars(select(Refund).where(Refund.merchant_name.isnot(None))).all()
    merchant_map = {m.sub_name: m.id for m in db.scalars(select(Merchant)).all()}
    count = 0
    for r in refunds:
        if not r.merchant_name:
            continue
        if only_unmatched and _exists_relation(db, "refund", r.refund_no, "merchant", "merchant_name"):
            continue
        mid = merchant_map.get(r.merchant_name.strip())
        if mid and _add_relation(db, "refund", r.refund_no, "merchant", str(mid), "merchant_name"):
            count += 1
    return count


def get_unmatched_summary(db: Session) -> dict[str, int]:
    """获取各表待关联数据统计"""
    # 退费单未关联商户退款(无现金流)
    refund_count = db.scalar(select(func.count()).select_from(Refund)) or 0
    refund_with_mr = db.scalar(
        select(func.count(func.distinct(DataMatchRelation.src_id)))
        .where(
            DataMatchRelation.src_table == "refund",
            DataMatchRelation.dst_table == "merchant_refund",
            DataMatchRelation.status == "matched",
        )
    ) or 0
    refund_with_ticket = db.scalar(
        select(func.count(func.distinct(DataMatchRelation.src_id)))
        .where(
            DataMatchRelation.src_table == "refund",
            DataMatchRelation.dst_table == "ticket",
            DataMatchRelation.status == "matched",
        )
    ) or 0
    return {
        "refund_total": refund_count,
        "refund_without_merchant_refund": max(refund_count - refund_with_mr, 0),
        "refund_without_ticket": max(refund_count - refund_with_ticket, 0),
    }
