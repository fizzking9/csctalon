"""物化宽表路由 - 手动重建/查询统计"""

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession
from app.models.refund_wide import RefundWide
from app.models.upload import DataMatchRelation
from app.schemas.common import R
from app.services.wide_table_builder import build_refund_wide

router = APIRouter(prefix="/wide-tables", tags=["数据中心-物化宽表"])


@router.post("/refund-wide/rebuild", response_model=R, summary="手动重建退费宽表(全量)")
def rebuild_refund_wide(db: DBSession, _: CurrentUser):
    """全量重建 refund_wide 物化宽表

    遍历所有退费单,重新拉取关联关系与外部投诉聚合,upsert 到 refund_wide 表。
    适用于:首次构建、修复脏数据、matcher 逻辑变更后重建。
    日常增量构建由 BMS 同步/上传后置钩子自动触发,无需手动调用。
    """
    stats = build_refund_wide(db, only_refund_nos=None)
    return R(data=stats, message=f"退费宽表重建完成: built={stats['built']}, skipped={stats['skipped']}")


@router.get("/refund-wide/stats", response_model=R, summary="退费宽表统计")
def refund_wide_stats(db: DBSession, _: CurrentUser):
    """返回 refund_wide 物化宽表的覆盖统计,用于诊断关联完整度"""
    total_wide = db.scalar(select(func.count()).select_from(RefundWide)) or 0
    with_ticket = db.scalar(select(func.count()).select_from(RefundWide).where(RefundWide.ticket_no.isnot(None))) or 0
    with_merchant_refund = db.scalar(select(func.count()).select_from(RefundWide).where(RefundWide.wx_refund_no.isnot(None))) or 0
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
        "with_merchant_refund": with_merchant_refund,
        "with_wallet": with_wallet,
        "with_external_complaint": with_external_complaint,
        "matched_refund_count": matched_refund_count,
        "coverage": f"{total_wide}/{matched_refund_count}" if matched_refund_count else None,
    })
