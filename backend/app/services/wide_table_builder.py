"""退费宽表(物化)构建服务

把 matcher 产出的 DataMatchRelation 关联关系物化到 refund_wide 宽表,
供分析引擎直接查询(引擎禁止 JOIN)。

构建策略:
- 全量构建(only_refund_nos=None):为 refunds 表所有退费单构建宽表行
  (refund 主表字段始终填充;ticket/merchant_refund/wallet 关联字段在有匹配时填充,否则 NULL;
   external_complaint 按 device_sn 聚合,与关联关系无关)
- 增量构建(only_refund_nos=[...]):只为指定退费单重建,用于同步后钩子

同 SN 多工单选择规则:优先 order_no 命中的工单,否则取最新 finish_time 的工单。
同 SN 多笔钱包取最新 op_time 的钱包。
商户退款按 order_no 精确命中,至多一条。
外部投诉按 device_sn 聚合为 count + latest_time,与 matcher 关系无关(直接查 external_complaints 表)。
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.external_complaint import ExternalComplaint
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.refund_wide import RefundWide
from app.models.ticket import ServiceTicket
from app.models.upload import DataMatchRelation
from app.models.wallet import WalletWithdrawal

logger = logging.getLogger(__name__)

# tz-aware 最小时间,用作排序兜底(None 排到最后)
_TZ_MIN = datetime.min.replace(tzinfo=timezone.utc)

# upsert 时需更新的字段(refund_no 是冲突键,不更新)
_WIDE_UPDATE_FIELDS = [
    "refund_method", "status", "refund_reason", "operator", "auditor", "device_sn",
    "refund_amount", "created_at_raw",
    "ticket_no", "service_item", "service_category", "ticket_finish_time",
    "ticket_handle_duration", "ticket_handler",
    "wx_refund_no", "mr_trade_time", "mr_refund_amount", "mr_refund_status", "mr_fee",
    "merchant_id",
    "wallet_id", "wallet_op_time", "wallet_op_amount",
    "has_external_complaint", "external_complaint_count", "external_complaint_latest_time",
    "matched_at",
]


def build_refund_wide(db: Session, only_refund_nos: list[str] | None = None) -> dict:
    """构建 refund_wide 物化宽表

    Args:
        db: 数据库会话(函数内不 commit,由调用方决定;但 upsert 后会 commit)
        only_refund_nos: 指定退费单号列表(增量构建);None=全量构建所有退费单

    Returns:
        {"built": int, "updated": int, "skipped": int}
    """
    # 1. 确定要处理的退费单号集合
    if only_refund_nos:
        target_refund_nos = list(dict.fromkeys(only_refund_nos))  # 去重保序
    else:
        target_refund_nos = list(db.scalars(select(Refund.refund_no)))

    if not target_refund_nos:
        return {"built": 0, "updated": 0, "skipped": 0}

    # 2. 批量拉取退费单主表记录
    refunds: dict[str, Refund] = {
        r.refund_no: r
        for r in db.scalars(
            select(Refund).where(Refund.refund_no.in_(target_refund_nos))
        )
    }

    # 3. 拉取关联关系(src_table='refund' AND status='matched'),按 refund_no 分组
    rel_rows = db.execute(
        select(DataMatchRelation.src_id, DataMatchRelation.dst_table, DataMatchRelation.dst_id, DataMatchRelation.match_type).where(
            DataMatchRelation.src_table == "refund",
            DataMatchRelation.status == "matched",
            DataMatchRelation.src_id.in_(target_refund_nos),
        )
    ).all()
    # refund_no → {dst_table: [(dst_id, match_type), ...]}
    refund_rel_map: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for src_id, dst_table, dst_id, match_type in rel_rows:
        refund_rel_map[src_id][dst_table].append((dst_id, match_type))

    # 4. 收集所有 dst_id,批量拉取关联表记录
    ticket_ids: set[str] = set()
    mr_ids: set[str] = set()
    wallet_ids: set[str] = set()
    for tables in refund_rel_map.values():
        for dst_id, _ in tables.get("ticket", []):
            ticket_ids.add(dst_id)
        for dst_id, _ in tables.get("merchant_refund", []):
            mr_ids.add(dst_id)
        for dst_id, _ in tables.get("wallet", []):
            wallet_ids.add(dst_id)

    tickets: dict[str, ServiceTicket] = {}
    if ticket_ids:
        tickets = {t.ticket_no: t for t in db.scalars(select(ServiceTicket).where(ServiceTicket.ticket_no.in_(ticket_ids)))}

    merchant_refunds: dict[str, MerchantRefund] = {}
    if mr_ids:
        merchant_refunds = {m.wx_refund_no: m for m in db.scalars(select(MerchantRefund).where(MerchantRefund.wx_refund_no.in_(mr_ids)))}

    wallets: dict[str, WalletWithdrawal] = {}
    if wallet_ids:
        # wallet_id 在关系表中是 str(w.id),需转 int 查询
        wallet_int_ids = [int(wid) for wid in wallet_ids if wid.isdigit()]
        if wallet_int_ids:
            wallets = {str(w.id): w for w in db.scalars(select(WalletWithdrawal).where(WalletWithdrawal.id.in_(wallet_int_ids)))}

    # 5. 外部投诉按 device_sn 聚合(与 matcher 关系无关,直接查表)
    device_sns: set[str] = {r.device_sn for r in refunds.values() if r.device_sn}
    ec_stats: dict[str, tuple[int, datetime | None]] = {}
    if device_sns:
        ec_rows = db.execute(
            select(
                ExternalComplaint.device_sn,
                func.count(ExternalComplaint.id),
                func.max(ExternalComplaint.complaint_date),
            )
            .where(ExternalComplaint.device_sn.in_(device_sns))
            .group_by(ExternalComplaint.device_sn)
        ).all()
        ec_stats = {row[0]: (row[1], row[2]) for row in ec_rows}

    # 6. 组装 RefundWide 行
    now = datetime.now(timezone.utc)
    wide_rows: list[dict] = []
    skipped = 0
    for refund_no in target_refund_nos:
        refund = refunds.get(refund_no)
        if not refund:
            skipped += 1
            continue

        tables = refund_rel_map.get(refund_no, {})

        # 选择工单:优先 order_no 命中,否则最新 finish_time
        ticket = _select_ticket(tables.get("ticket", []), tickets)
        # 商户退款:至多一条(order_no 精确命中)
        merchant_refund = _select_merchant_refund(tables.get("merchant_refund", []), merchant_refunds)
        # 钱包:最新 op_time
        wallet = _select_wallet(tables.get("wallet", []), wallets)

        # 外部投诉聚合
        ec_count, ec_latest = (0, None)
        if refund.device_sn and refund.device_sn in ec_stats:
            ec_count, ec_latest = ec_stats[refund.device_sn]

        wide_rows.append({
            "refund_no": refund_no,
            "refund_method": refund.refund_method,
            "status": refund.status,
            "refund_reason": refund.refund_reason,
            "operator": refund.operator,
            "auditor": refund.auditor,
            "device_sn": refund.device_sn,
            "refund_amount": refund.refund_amount,
            "created_at_raw": refund.created_at_raw,
            "ticket_no": ticket.ticket_no if ticket else None,
            "service_item": ticket.service_item if ticket else None,
            "service_category": ticket.service_category if ticket else None,
            "ticket_finish_time": ticket.finish_time if ticket else None,
            "ticket_handle_duration": ticket.handle_duration if ticket else None,
            "ticket_handler": ticket.handler if ticket else None,
            "wx_refund_no": merchant_refund.wx_refund_no if merchant_refund else None,
            "mr_trade_time": merchant_refund.trade_time if merchant_refund else None,
            "mr_refund_amount": merchant_refund.refund_amount if merchant_refund else None,
            "mr_refund_status": merchant_refund.refund_status if merchant_refund else None,
            "mr_fee": merchant_refund.fee if merchant_refund else None,
            "merchant_id": merchant_refund.merchant_id if merchant_refund else None,
            "wallet_id": str(wallet.id) if wallet else None,
            "wallet_op_time": wallet.op_time if wallet else None,
            "wallet_op_amount": wallet.op_amount if wallet else None,
            "has_external_complaint": ec_count > 0,
            "external_complaint_count": ec_count,
            "external_complaint_latest_time": ec_latest,
            "matched_at": now,
        })

    # 7. 批量 upsert(ON CONFLICT refund_no DO UPDATE)
    if wide_rows:
        stmt = pg_insert(RefundWide).values(wide_rows)
        update_set = {f: getattr(stmt.excluded, f) for f in _WIDE_UPDATE_FIELDS}
        stmt = stmt.on_conflict_do_update(index_elements=["refund_no"], set_=update_set)
        db.execute(stmt)
        db.commit()

    logger.info(
        f"refund_wide 构建完成: built={len(wide_rows)}, skipped={skipped}, "
        f"target={len(target_refund_nos)}"
    )
    return {"built": len(wide_rows), "updated": len(wide_rows), "skipped": skipped}


def _select_ticket(
    ticket_rels: list[tuple[str, str]], tickets: dict[str, ServiceTicket]
) -> ServiceTicket | None:
    """从同 SN 的多条工单关系中选择一条:优先 order_no 命中,否则最新 finish_time"""
    if not ticket_rels:
        return None
    # 优先 order_no 命中
    for dst_id, match_type in ticket_rels:
        if match_type == "order_no" and dst_id in tickets:
            return tickets[dst_id]
    # 否则取最新 finish_time
    related = [tickets[tid] for tid, _ in ticket_rels if tid in tickets]
    related = [t for t in related if t is not None]
    if not related:
        return None
    related.sort(key=lambda t: t.finish_time or _TZ_MIN, reverse=True)
    return related[0]


def _select_merchant_refund(
    mr_rels: list[tuple[str, str]], merchant_refunds: dict[str, MerchantRefund]
) -> MerchantRefund | None:
    """商户退款:至多一条(order_no 精确命中),取第一条命中的"""
    if not mr_rels:
        return None
    for dst_id, _ in mr_rels:
        if dst_id in merchant_refunds:
            return merchant_refunds[dst_id]
    return None


def _select_wallet(
    wallet_rels: list[tuple[str, str]], wallets: dict[str, WalletWithdrawal]
) -> WalletWithdrawal | None:
    """从同 SN 的多笔钱包记录中选择最新 op_time 的一笔"""
    if not wallet_rels:
        return None
    related = [wallets[wid] for wid, _ in wallet_rels if wid in wallets]
    related = [w for w in related if w is not None]
    if not related:
        return None
    related.sort(key=lambda w: w.op_time or _TZ_MIN, reverse=True)
    return related[0]
