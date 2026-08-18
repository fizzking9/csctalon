"""跨部门协作派发路由(规划设计 §8.3 UpstreamTicket)"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.models.upstream_ticket import UpstreamTicket
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/upstream-tickets", tags=["跨部门协作"])


def _serialize(t: UpstreamTicket) -> dict:
    return {
        "id": t.id,
        "issue": t.issue,
        "upstream_dept": t.upstream_dept,
        "dispatched_at": t.dispatched_at.isoformat() if t.dispatched_at else None,
        "responded_at": t.responded_at.isoformat() if t.responded_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "status": t.status,
        "score": t.score,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("", response_model=R[PageBase], summary="跨部门协作列表")
def list_tickets(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    upstream_dept: str | None = None,
    status: str | None = None,
):
    stmt = select(UpstreamTicket)
    if upstream_dept:
        stmt = stmt.where(UpstreamTicket.upstream_dept == upstream_dept)
    if status:
        stmt = stmt.where(UpstreamTicket.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(UpstreamTicket.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return R(data=PageBase(items=[_serialize(t) for t in items], total=total or 0, page=page, page_size=page_size))


@router.post("", response_model=R, summary="派发上游问题")
def create_ticket(payload: dict, db: DBSession, current_user: ManagerUser):
    t = UpstreamTicket(
        issue=payload["issue"],
        upstream_dept=payload["upstream_dept"],
        dispatched_at=datetime.now(timezone.utc),
        status=payload.get("status", "待响应"),
        owner_id=current_user.id,
        created_by=current_user.username,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return R(data=_serialize(t))


@router.put("/{ticket_id}", response_model=R, summary="更新跨部门协作(响应/解决/打分)")
def update_ticket(ticket_id: int, payload: dict, db: DBSession, _: ManagerUser):
    t = db.get(UpstreamTicket, ticket_id)
    if not t:
        raise HTTPException(status_code=404, detail="不存在")
    for k in ("issue", "upstream_dept", "status", "score"):
        if k in payload:
            setattr(t, k, payload[k])
    if "responded_at" in payload:
        t.responded_at = datetime.fromisoformat(payload["responded_at"]) if payload["responded_at"] else None
    if "resolved_at" in payload:
        t.resolved_at = datetime.fromisoformat(payload["resolved_at"]) if payload["resolved_at"] else None
    if payload.get("status") == "已解决" and not t.resolved_at:
        t.resolved_at = datetime.now(timezone.utc)
    if payload.get("status") == "处理中" and not t.responded_at:
        t.responded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(t)
    return R(data=_serialize(t))


@router.delete("/{ticket_id}", response_model=R, summary="删除跨部门协作")
def delete_ticket(ticket_id: int, db: DBSession, _: ManagerUser):
    t = db.get(UpstreamTicket, ticket_id)
    if not t:
        raise HTTPException(status_code=404, detail="不存在")
    db.delete(t)
    db.commit()
    return R()


@router.get("/stats/summary", response_model=R, summary="上游部门响应/解决率排行")
def upstream_stats(db: DBSession, _: CurrentUser):
    rows = db.execute(
        select(
            UpstreamTicket.upstream_dept,
            func.count().label("total"),
            func.count(func.nullif(UpstreamTicket.responded_at, None)).label("responded"),
            func.count(func.nullif(UpstreamTicket.resolved_at, None)).label("resolved"),
        ).group_by(UpstreamTicket.upstream_dept)
    ).all()
    data = [
        {
            "dept": dept,
            "total": int(total or 0),
            "responded": int(responded or 0),
            "resolved": int(resolved or 0),
            "response_rate": round(int(responded or 0) / int(total or 1) * 100, 1),
            "resolve_rate": round(int(resolved or 0) / int(total or 1) * 100, 1),
        }
        for dept, total, responded, resolved in rows
    ]
    data.sort(key=lambda x: -x["resolve_rate"])
    return R(data=data)
