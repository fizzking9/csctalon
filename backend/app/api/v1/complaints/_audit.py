"""投诉审计: list_audits / create_audit。"""

from datetime import datetime, timezone

from fastapi import Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession, ManagerUser

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._router import router
from app.models.complaint_audit import ComplaintAudit
from app.schemas.common import PageBase, R


@router.get("/audits", response_model=R[PageBase], summary="投诉审计列表")
def list_audits(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source_type: str | None = None,
):
    stmt = select(ComplaintAudit)
    if source_type:
        stmt = stmt.where(ComplaintAudit.source_type == source_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(ComplaintAudit.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return R(
        data=PageBase(
            items=[
                {
                    "id": a.id,
                    "source_type": a.source_type,
                    "ref_id": a.ref_id,
                    "device_sn": a.device_sn,
                    "involved_persons": a.involved_persons,
                    "attribution": a.attribution,
                    "conclusion": a.conclusion,
                    "judgment": a.judgment,
                    "attachments": a.attachments,
                    "audited_by_name": a.audited_by_name,
                    "audited_at": a.audited_at.isoformat() if a.audited_at else None,
                }
                for a in items
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )

@router.post("/audits", response_model=R, summary="新增投诉审计结论")
def create_audit(payload: dict, db: DBSession, current_user: ManagerUser):
    a = ComplaintAudit(
        source_type=payload.get("source_type", "internal"),
        ref_id=payload.get("ref_id"),
        device_sn=payload.get("device_sn"),
        involved_persons=payload.get("involved_persons"),
        attribution=payload.get("attribution"),
        conclusion=payload.get("conclusion"),
        judgment=payload.get("judgment"),
        attachments=payload.get("attachments"),
        audited_by=current_user.id,
        audited_by_name=current_user.display_name or current_user.username,
        audited_at=datetime.now(timezone.utc),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return R(data={"id": a.id, "conclusion": a.conclusion, "attribution": a.attribution})
