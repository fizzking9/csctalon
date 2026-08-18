"""统一待办中心路由(规划设计 §五 统一工作流)

汇聚「该我处理的事」: 待办事项 / 待跟进异常 / 待回看措施 / 待读政策 / 待处理工单。
按角色过滤: admin/supervisor/specialty_lead 看全部; specialist/agent 看本人。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession
from app.models.incident import Incident
from app.models.measure import Measure
from app.models.notification import Notification
from app.models.strategy import StrategyDoc, StrategyReadReceipt
from app.models.ticket import ServiceTicket
from app.models.todo import TodoItem
from app.models.user import MANAGER_ROLES
from app.schemas.common import R

router = APIRouter(prefix="/agenda", tags=["统一待办中心"])


def _is_manager(role: str) -> bool:
    return role in MANAGER_ROLES


@router.get("", response_model=R, summary="我的待办聚合")
def my_agenda(
    db: DBSession,
    current_user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
):
    uid = current_user.id
    uname = current_user.username
    manager = _is_manager(current_user.role)
    items: list[dict] = []

    # 1. 待办事项(本人)
    todos = db.scalars(
        select(TodoItem)
        .where(TodoItem.assignee_id == uid, TodoItem.status != "done")
        .order_by(TodoItem.deadline.asc().nulls_last(), TodoItem.id.desc())
        .limit(limit)
    )
    for t in todos:
        items.append(
            {
                "key": f"todo:{t.id}",
                "category": "待办事项",
                "title": t.title,
                "priority": t.priority,
                "deadline": t.deadline.isoformat() if t.deadline else None,
                "status": t.status,
                "ref_type": "todo",
                "ref_id": str(t.id),
            }
        )

    # 2. 待跟进异常(未闭环)
    inc_stmt = select(Incident).where(Incident.is_closed.is_(False))
    if not manager:
        inc_stmt = inc_stmt.where(
            (Incident.registrar_id == uid) | (Incident.created_by == uname)
        )
    incidents = db.scalars(inc_stmt.order_by(Incident.occurred_at.desc().nulls_last()).limit(limit))
    for i in incidents:
        items.append(
            {
                "key": f"incident:{i.id}",
                "category": "异常事故",
                "title": i.title,
                "priority": i.severity or "中",
                "deadline": None,
                "status": i.status,
                "ref_type": "incident",
                "ref_id": str(i.id),
            }
        )

    # 3. 待回看措施(执行中/待回看)
    mea_stmt = select(Measure).where(Measure.status.in_(["executing", "pending_review"]))
    if not manager:
        mea_stmt = mea_stmt.where(Measure.owner_id == uid)
    measures = db.scalars(mea_stmt.order_by(Measure.verify_time.asc().nulls_last()).limit(limit))
    for m in measures:
        items.append(
            {
                "key": f"measure:{m.id}",
                "category": "管理措施",
                "title": m.title,
                "priority": "中",
                "deadline": m.verify_time.isoformat() if m.verify_time else None,
                "status": m.status,
                "ref_type": "measure",
                "ref_id": str(m.id),
            }
        )

    # 4. 待读政策(已发布但本人未读)
    read_ids = set(
        db.scalars(select(StrategyReadReceipt.doc_id).where(StrategyReadReceipt.user_id == uid))
    )
    policies = db.scalars(
        select(StrategyDoc).where(StrategyDoc.status == "published").order_by(StrategyDoc.id.desc()).limit(limit)
    )
    for p in policies:
        if p.id in read_ids:
            continue
        items.append(
            {
                "key": f"policy:{p.id}",
                "category": "待读政策",
                "title": p.title,
                "priority": "中",
                "deadline": None,
                "status": "unread",
                "ref_type": "policy",
                "ref_id": str(p.id),
            }
        )

    # 5. 待处理工单(管理层看全部待处理; 一线看自己处理人)
    tkt_stmt = select(ServiceTicket).where(
        (ServiceTicket.status.is_(None)) | (ServiceTicket.status.notin_(["已完成", "已关闭"]))
    )
    if not manager:
        if current_user.qw_name:
            tkt_stmt = tkt_stmt.where(
                ServiceTicket.handler.in_(
                    [current_user.qw_name, current_user.display_name, f"企微-{current_user.qw_name}"]
                )
            )
        else:
            tkt_stmt = tkt_stmt.where(ServiceTicket.handler == current_user.display_name)
    tickets = db.scalars(tkt_stmt.order_by(ServiceTicket.register_time.desc().nulls_last()).limit(limit))
    for tk in tickets:
        items.append(
            {
                "key": f"ticket:{tk.ticket_no}",
                "category": "待处理工单",
                "title": f"{tk.ticket_no} {tk.service_item or ''}".strip(),
                "priority": "高" if (tk.priority or 0) >= 2 else "中",
                "deadline": None,
                "status": tk.status or "待处理",
                "ref_type": "ticket",
                "ref_id": tk.ticket_no,
            }
        )

    # 6. 未读通知数
    unread = db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.recipient_id == uid, Notification.is_read.is_(False)
        )
    )

    return R(
        data={
            "items": items,
            "unread_notifications": unread or 0,
            "total": len(items),
        }
    )
