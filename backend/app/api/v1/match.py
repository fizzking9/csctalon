"""匹配与待关联数据路由"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DBSession
from app.core.database import get_db
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.upload import DataMatchRelation
from app.schemas.common import PageBase, R
from app.services.matcher import get_unmatched_summary, match_all

router = APIRouter(prefix="/match", tags=["数据中心-关联匹配"])


@router.post("/run", response_model=R, summary="手动触发全量关联匹配")
def run_match(db: DBSession, current_user: CurrentUser, only_unmatched: bool = Query(True)):
    stats = match_all(db, only_unmatched=only_unmatched)
    return R(data=stats, message=f"匹配完成,新增关联 {sum(stats.values())} 条")


@router.get("/summary", response_model=R, summary="待关联数据统计")
def unmatched_summary(db: DBSession, _: CurrentUser):
    return R(data=get_unmatched_summary(db))


@router.get("/relations", response_model=R[PageBase], summary="关联关系列表")
def list_relations(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    src_table: str | None = None,
    dst_table: str | None = None,
    match_type: str | None = None,
):
    stmt = select(DataMatchRelation)
    if src_table:
        stmt = stmt.where(DataMatchRelation.src_table == src_table)
    if dst_table:
        stmt = stmt.where(DataMatchRelation.dst_table == dst_table)
    if match_type:
        stmt = stmt.where(DataMatchRelation.match_type == match_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(DataMatchRelation.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(
        data=PageBase(
            items=[
                {
                    "id": r.id,
                    "src_table": r.src_table,
                    "src_id": r.src_id,
                    "dst_table": r.dst_table,
                    "dst_id": r.dst_id,
                    "match_type": r.match_type,
                    "confidence": r.confidence,
                    "matched_by": r.matched_by,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in items
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/manual", response_model=R, summary="人工补关联")
def manual_match(
    payload: dict,
    db: DBSession,
    current_user: CurrentUser,
):
    """人工建立关联

    Body: src_table, src_id, dst_table, dst_id, match_type, confidence?
    """
    required = ("src_table", "src_id", "dst_table", "dst_id", "match_type")
    for k in required:
        if not payload.get(k):
            raise HTTPException(status_code=400, detail=f"缺少参数: {k}")
    # 校验是否已存在
    exists = db.scalar(
        select(func.count())
        .select_from(DataMatchRelation)
        .where(
            DataMatchRelation.src_table == payload["src_table"],
            DataMatchRelation.src_id == payload["src_id"],
            DataMatchRelation.dst_table == payload["dst_table"],
            DataMatchRelation.dst_id == payload["dst_id"],
            DataMatchRelation.match_type == payload["match_type"],
        )
    )
    if exists:
        raise HTTPException(status_code=400, detail="关联已存在")
    rel = DataMatchRelation(
        src_table=payload["src_table"],
        src_id=payload["src_id"],
        dst_table=payload["dst_table"],
        dst_id=payload["dst_id"],
        match_type=payload["match_type"],
        confidence=payload.get("confidence", 100),
        matched_by="manual",
        status="matched",
    )
    db.add(rel)
    db.commit()
    return R(message="人工关联成功")


@router.get("/unmatched-refunds", response_model=R[PageBase], summary="未关联商户退款的退费单")
def list_unmatched_refunds(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """列出未关联到商户退款(无现金流对账)的退费单,供人工补关联"""
    # 已关联的退费单号集合
    matched_ids = set(
        db.scalars(
            select(DataMatchRelation.src_id).where(
                DataMatchRelation.src_table == "refund",
                DataMatchRelation.dst_table == "merchant_refund",
                DataMatchRelation.status == "matched",
            )
        )
    )
    stmt = select(Refund).order_by(Refund.refund_no.desc())
    all_refunds = list(db.scalars(stmt).unique())
    unmatched = [r for r in all_refunds if r.refund_no not in matched_ids]
    total = len(unmatched)
    start = (page - 1) * page_size
    page_items = unmatched[start : start + page_size]
    return R(
        data=PageBase(
            items=[
                {
                    "refund_no": r.refund_no,
                    "platform_order_no": r.platform_order_no,
                    "device_sn": r.device_sn,
                    "merchant_name": r.merchant_name,
                    "refund_amount": float(r.refund_amount) if r.refund_amount else None,
                    "status": r.status,
                    "created_at_raw": r.created_at_raw.isoformat() if r.created_at_raw else None,
                }
                for r in page_items
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
    )
