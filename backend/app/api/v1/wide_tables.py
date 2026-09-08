"""物化宽表路由 - 手动重建/查询统计"""

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.cash_flow_wide import CashFlowWide
from app.models.refund_wide import RefundWide
from app.models.upload import DataMatchRelation
from app.schemas.common import R
from app.services.cash_flow_wide_builder import build_cash_flow_wide

router = APIRouter(prefix="/wide-tables", tags=["数据中心-物化宽表"])


@router.post("/cash-flow/rebuild", response_model=R, summary="手动重建现金流水宽表(全量)")
def rebuild_cash_flow_wide(db: DBSession, _: AdminUser):
    """全量重建 cash_flow_wide 物化宽表(A-20260908-004)

    清空后按 ABCD 经营四路每笔出款物化(route 区分), 供对账/快速响应/运营多维分析。
    收紧为管理员: 全量重建扫全库, 普通用户反复触发可拖垮 DB。
    """
    stats = build_cash_flow_wide(db)
    return R(data=stats, message=f"现金流水宽表重建完成: total={stats['total']}")


@router.get("/cash-flow/stats", response_model=R, summary="现金流水宽表统计")
def cash_flow_wide_stats(db: DBSession, _: CurrentUser):
    """返回 cash_flow_wide 各路线行数/金额汇总"""
    rows = db.execute(
        select(
            CashFlowWide.route,
            func.count().label("cnt"),
            func.coalesce(func.sum(CashFlowWide.pay_amount), 0).label("amt"),
        ).group_by(CashFlowWide.route)
    ).all()
    total = db.scalar(select(func.count()).select_from(CashFlowWide)) or 0
    return R(data={
        "total_rows": total,
        "by_route": {r.route: {"count": int(r.cnt or 0), "amount": round(float(r.amt or 0), 2)} for r in rows},
    })



@router.get("/refund-wide/stats", response_model=R, summary="退费宽表统计")
def refund_wide_stats(db: DBSession, _: CurrentUser):
    """返回 refund_wide 物化宽表的覆盖统计,用于诊断关联完整度

    注: A-20260908-001 起 refund_wide 不再物化 merchant_refund(mr_*)关联,
        with_merchant_refund 键已移除(原基于已删列 wx_refund_no)。
    """
    total_wide = db.scalar(select(func.count()).select_from(RefundWide)) or 0
    with_ticket = db.scalar(select(func.count()).select_from(RefundWide).where(RefundWide.ticket_no.isnot(None))) or 0
    with_wallet = db.scalar(select(func.count()).select_from(RefundWide).where(RefundWide.wallet_id.isnot(None))) or 0
    with_external_complaint = db.scalar(
        select(func.count()).select_from(RefundWide).where(RefundWide.has_external_complaint.is_(True))
    ) or 0
    matched_refund_count = db.scalar(
        select(func.count(func.distinct(DataMatchRelation.src_id))).where(
            DataMatchRelation.src_table == "refund",
            DataMatchRelation.status == "matched",
        )
    ) or 0
    return R(data={
        "total_wide_rows": total_wide,
        "with_ticket": with_ticket,
        "with_wallet": with_wallet,
        "with_external_complaint": with_external_complaint,
        "matched_refund_count": matched_refund_count,
        "coverage": f"{total_wide}/{matched_refund_count}" if matched_refund_count else None,
    })
