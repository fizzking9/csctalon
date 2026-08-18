"""通知服务 - 各模块统一发站内信入口

用法:
    from app.services.notifier import notify
    notify(db, recipient_ids=[3, 5], type="incident", title="...", content="...", ref_type="incident", ref_id="12")
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import RoleEnum, User


def notify(
    db: Session,
    recipient_ids: Iterable[int] | None,
    *,
    type: str = "info",
    title: str,
    content: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
    roles: Iterable[str] | None = None,
) -> int:
    """给指定用户(或指定角色)发站内信, 返回发送条数。

    recipient_ids 优先; 若为 None 且给了 roles, 则发给所有匹配角色的用户。
    """
    ids: set[int] = set()
    if recipient_ids:
        ids = {int(i) for i in recipient_ids if i}
    elif roles:
        role_vals = list(roles)
        rows = db.scalars(select(User.id).where(User.role.in_(role_vals), User.is_active.is_(True)))
        ids = {int(i) for i in rows}
    if not ids:
        return 0
    count = 0
    for rid in ids:
        db.add(
            Notification(
                recipient_id=rid,
                type=type,
                title=title,
                content=content,
                ref_type=ref_type,
                ref_id=str(ref_id) if ref_id is not None else None,
            )
        )
        count += 1
    db.flush()
    return count


def notify_all_admins_and_supervisors(db: Session, **kw) -> int:
    """通知所有管理员 + 主管(登记异常/发布政策等场景)。"""
    return notify(db, None, roles=[RoleEnum.ADMIN.value, RoleEnum.SUPERVISOR.value], **kw)
