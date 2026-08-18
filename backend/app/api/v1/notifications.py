"""通知/站内信路由(规划设计 §五 统一工作流)"""

from fastapi import APIRouter, Query
from sqlalchemy import func, select, update

from app.api.deps import CurrentUser, DBSession
from app.models.notification import Notification
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/notifications", tags=["通知中心"])


def _serialize(n: Notification) -> dict:
    return {
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "content": n.content,
        "ref_type": n.ref_type,
        "ref_id": n.ref_id,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("/unread-count", response_model=R, summary="未读通知数")
def unread_count(db: DBSession, current_user: CurrentUser):
    n = db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.recipient_id == current_user.id, Notification.is_read.is_(False)
        )
    )
    return R(data={"unread": n or 0})


@router.get("", response_model=R[PageBase], summary="我的通知列表")
def list_notifications(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False, description="仅未读"),
):
    stmt = select(Notification).where(Notification.recipient_id == current_user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(Notification.is_read.asc(), Notification.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize(i) for i in items], total=total or 0, page=page, page_size=page_size))


@router.post("/{notification_id}/read", response_model=R, summary="标记单条已读")
def mark_read(notification_id: int, db: DBSession, current_user: CurrentUser):
    n = db.get(Notification, notification_id)
    if not n or n.recipient_id != current_user.id:
        return R(message="通知不存在")
    if not n.is_read:
        n.is_read = True
        db.commit()
    return R(message="已读")


@router.post("/read-all", response_model=R, summary="全部标记已读")
def mark_all_read(db: DBSession, current_user: CurrentUser):
    db.execute(
        update(Notification)
        .where(Notification.recipient_id == current_user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.commit()
    return R(message="已全部标记已读")
