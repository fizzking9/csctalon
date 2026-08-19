"""异常事件登记路由 - 未通知的非预期事项,造成客服负荷激增

管理层(AdminOrSupervisor)登记与维护;普通用户可查看(知情)。
转化链路:异常发生→登记→复盘→转预期事项→下次提前部署。
铁律:用于改进不用于问责。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.incident import Incident, IncidentFollowup, IncidentStatus
from app.models.user import MANAGER_ROLES
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
        "category": i.category,
        "department": i.department,
        "severity": i.severity,
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "closed_at": i.closed_at.isoformat() if i.closed_at else None,
        "is_closed": i.is_closed,
        "is_recurring": i.is_recurring,
        "recurrence_pattern": i.recurrence_pattern,
        "valid_from": i.valid_from.isoformat() if i.valid_from else None,
        "valid_to": i.valid_to.isoformat() if i.valid_to else None,
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
    category: str | None = Query(None, description="来源/类型:生产/运营/商务部/店铺/系统平台开发/其他服务商"),
    department: str | None = Query(None, description="涉及部门"),
    start: str | None = Query(None, description="发生时间起 yyyy-mm-dd"),
    end: str | None = Query(None, description="发生时间止 yyyy-mm-dd"),
):
    stmt = select(Incident)
    if status:
        try:
            stmt = stmt.where(Incident.status == IncidentStatus(status).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态值: {status}")
    if category:
        stmt = stmt.where(Incident.category == category)
    if department:
        stmt = stmt.where(Incident.department == department)
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
        category=payload.get("category"),
        department=payload.get("department"),
        severity=payload.get("severity", "中"),
        is_recurring=bool(payload.get("is_recurring", False)),
        recurrence_pattern=payload.get("recurrence_pattern"),
        valid_from=datetime.fromisoformat(payload["valid_from"]) if payload.get("valid_from") else None,
        valid_to=datetime.fromisoformat(payload["valid_to"]) if payload.get("valid_to") else None,
        created_by=current_user.username,
    )
    db.add(i)
    db.commit()
    db.refresh(i)
    # 通知管理员/主管(异常登记知情)
    try:
        from app.services.notifier import notify_all_admins_and_supervisors
        notify_all_admins_and_supervisors(
            db,
            type="incident",
            title=f"异常事故登记：{i.title}",
            content=payload.get("detail") or "",
            ref_type="incident",
            ref_id=str(i.id),
        )
        db.commit()
    except Exception:
        db.rollback()
    return R(data=_serialize(i))


@router.put("/{incident_id}", response_model=R, summary="更新异常事件")
def update_incident(incident_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    for k in ("title", "subject", "detail", "impact", "duration_minutes", "related_tickets", "review_note",
              "category", "department", "severity", "recurrence_pattern"):
        if k in payload:
            setattr(i, k, payload[k])
    if "occurred_at" in payload:
        i.occurred_at = datetime.fromisoformat(payload["occurred_at"]) if payload["occurred_at"] else None
    if "resolved_at" in payload:
        i.resolved_at = datetime.fromisoformat(payload["resolved_at"]) if payload["resolved_at"] else None
    if "closed_at" in payload:
        i.closed_at = datetime.fromisoformat(payload["closed_at"]) if payload["closed_at"] else None
    if "valid_from" in payload:
        i.valid_from = datetime.fromisoformat(payload["valid_from"]) if payload["valid_from"] else None
    if "valid_to" in payload:
        i.valid_to = datetime.fromisoformat(payload["valid_to"]) if payload["valid_to"] else None
    if "is_recurring" in payload:
        i.is_recurring = bool(payload["is_recurring"])
    if "is_closed" in payload:
        i.is_closed = bool(payload["is_closed"])
        if i.is_closed and not i.closed_at:
            i.closed_at = datetime.now(timezone.utc)
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


# ---------- 跟进时间线(规划设计 §4.2.2) ----------


@router.get("/{incident_id}/followups", response_model=R, summary="异常事件跟进记录")
def list_followups(incident_id: int, db: DBSession, current_user: CurrentUser):
    rows = db.scalars(
        select(IncidentFollowup)
        .where(IncidentFollowup.incident_id == incident_id)
        .order_by(IncidentFollowup.id.asc())
    )
    return R(
        data=[
            {
                "id": f.id,
                "incident_id": f.incident_id,
                "author_id": f.author_id,
                "content": f.content,
                "status_change": f.status_change,
                "created_by": f.created_by,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in rows
        ]
    )


@router.post("/{incident_id}/followups", response_model=R, summary="新增跟进记录")
def add_followup(incident_id: int, payload: dict, db: DBSession, current_user: CurrentUser):
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    f = IncidentFollowup(
        incident_id=incident_id,
        author_id=current_user.id,
        content=payload["content"],
        status_change=payload.get("status_change"),
        created_by=current_user.username,
    )
    db.add(f)
    # 若提供了状态变更, 同步更新异常状态
    if payload.get("status") and payload["status"] in {s.value for s in IncidentStatus}:
        i.status = payload["status"]
    if payload.get("is_closed"):
        i.is_closed = True
        if not i.closed_at:
            i.closed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(f)
    return R(
        data={
            "id": f.id,
            "content": f.content,
            "status_change": f.status_change,
            "created_by": f.created_by,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
    )
