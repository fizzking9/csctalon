"""异常事件登记路由 - 未通知的非预期事项,造成客服负荷激增

管理层(AdminOrSupervisor)登记与维护;普通用户可查看(知情)。
转化链路:异常发生→登记→复盘→转预期事项→下次提前部署。
铁律:用于改进不用于问责。
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.incident import Incident, IncidentStatus
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/incidents", tags=["异常事件登记"])


def _serialize(i: Incident) -> dict:
    return {
        "id": i.id,
        "title": i.title,
        "subject": i.subject,
        "detail": i.detail,
        "registrar_id": i.registrar_id,
        "impact": i.impact,
        "occurred_at": i.occurred_at.isoformat() if i.occurred_at else None,
        "duration_minutes": i.duration_minutes,
        "related_tickets": i.related_tickets,
        "status": i.status,
        "converted_to_expected": i.converted_to_expected,
        "review_note": i.review_note,
        "created_by": i.created_by,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }


@router.get("", response_model=R[PageBase], summary="异常事件列表")
def list_incidents(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="状态: registered/processing/calmed/reviewed"),
    start: str | None = Query(None, description="发生时间起 yyyy-mm-dd"),
    end: str | None = Query(None, description="发生时间止 yyyy-mm-dd"),
):
    stmt = select(Incident)
    if status:
        try:
            stmt = stmt.where(Incident.status == IncidentStatus(status).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态值: {status}")
    if start:
        stmt = stmt.where(Incident.occurred_at >= datetime.fromisoformat(start))
    if end:
        stmt = stmt.where(Incident.occurred_at <= datetime.fromisoformat(end))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(Incident.occurred_at.desc().nulls_last(), Incident.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize(i) for i in items], total=total or 0, page=page, page_size=page_size))


@router.get("/{incident_id}", response_model=R, summary="异常事件详情")
def get_incident(incident_id: int, db: DBSession, current_user: CurrentUser):
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    return R(data=_serialize(i))


@router.post("", response_model=R, summary="登记异常事件")
def create_incident(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    i = Incident(
        title=payload["title"],
        subject=payload.get("subject"),
        detail=payload.get("detail"),
        registrar_id=payload.get("registrar_id") or current_user.id,
        impact=payload.get("impact"),
        occurred_at=datetime.fromisoformat(payload["occurred_at"]) if payload.get("occurred_at") else None,
        duration_minutes=payload.get("duration_minutes"),
        related_tickets=payload.get("related_tickets"),
        status=IncidentStatus(payload.get("status", "registered")).value,
        created_by=current_user.username,
    )
    db.add(i)
    db.commit()
    db.refresh(i)
    return R(data=_serialize(i))


@router.put("/{incident_id}", response_model=R, summary="更新异常事件")
def update_incident(incident_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    for k in ("title", "subject", "detail", "impact", "duration_minutes", "related_tickets", "review_note"):
        if k in payload:
            setattr(i, k, payload[k])
    if "occurred_at" in payload:
        i.occurred_at = datetime.fromisoformat(payload["occurred_at"]) if payload["occurred_at"] else None
    if "status" in payload:
        i.status = IncidentStatus(payload["status"]).value
    if "converted_to_expected" in payload:
        i.converted_to_expected = bool(payload["converted_to_expected"])
    db.commit()
    db.refresh(i)
    return R(data=_serialize(i))


@router.post("/{incident_id}/convert", response_model=R, summary="转为预期事项")
def convert_incident(incident_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    """复盘后转为预期事项(纳入提前部署):标记 converted_to_expected 并置为已复盘。"""
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    i.converted_to_expected = True
    i.status = IncidentStatus.REVIEWED.value
    if payload.get("review_note"):
        i.review_note = payload["review_note"]
    db.commit()
    db.refresh(i)
    return R(data=_serialize(i))


@router.delete("/{incident_id}", response_model=R, summary="删除异常事件")
def delete_incident(incident_id: int, db: DBSession, current_user: AdminOrSupervisor):
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    db.delete(i)
    db.commit()
    return R()
