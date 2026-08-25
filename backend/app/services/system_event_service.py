"""系统事务通知服务 - 记录/查询系统关键事务事件

供各服务(同步/推送/导入/预警等)在关键节点调用 record_system_event() 写入概要,
供前端「事务通知」页与管理角标查询。
独立于数据采集模块, category 可自由扩展。
"""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.system_event import SystemEvent

logger = logging.getLogger(__name__)


def record_system_event(
    db: Session,
    category: str,
    action: str,
    title: str,
    summary: str | None = None,
    status: str = "info",
    level: str | None = None,
    detail: str | None = None,
    commit: bool = True,
) -> SystemEvent:
    """写入一条系统事务事件。

    category: sync(数据同步) / push(推送接收) / import(导入) / alert(预警) /
              schedule(定时调度) / match(关联匹配) / auth(登录验证)
    status: success / failed / partial / info
    level: 默认按 status 推导(info→info, success→info, partial→warning, failed→error)
    """
    if level is None:
        level = {
            "failed": "error",
            "partial": "warning",
            "error": "error",
            "success": "info",
            "info": "info",
        }.get(status, "info")
    ev = SystemEvent(
        category=category,
        action=action,
        title=title[:255],
        summary=(summary or "")[:512] or None,
        status=status,
        level=level,
        detail=detail,
        is_read=False,
    )
    db.add(ev)
    if commit:
        try:
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.warning(f"[system_event] 写入失败(已回滚,不影响主流程): {e}")
            return ev
        db.refresh(ev)
    return ev


def list_system_events(
    db: Session,
    page: int = 1,
    page_size: int = 30,
    category: str | None = None,
    status: str | None = None,
    level: str | None = None,
    unread_only: bool = False,
) -> dict:
    """分页查询事务事件(默认最新优先)"""
    stmt = select(SystemEvent)
    if category:
        stmt = stmt.where(SystemEvent.category == category)
    if status:
        stmt = stmt.where(SystemEvent.status == status)
    if level:
        stmt = stmt.where(SystemEvent.level == level)
    if unread_only:
        stmt = stmt.where(SystemEvent.is_read.is_(False))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(SystemEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_serialize(e) for e in items],
    }


def event_summary(db: Session) -> dict:
    """角标用概要: 未读数 / 最近24h各类结果计数"""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    unread = db.scalar(
        select(func.count()).select_from(SystemEvent).where(SystemEvent.is_read.is_(False))
    ) or 0
    recent = dict(db.execute(
        select(SystemEvent.status, func.count())
        .where(SystemEvent.created_at >= since)
        .group_by(SystemEvent.status)
    ).all())
    return {
        "unread": int(unread),
        "recent_24h": {k: int(v) for k, v in recent.items()},
    }


def mark_event_read(db: Session, event_id: int) -> bool:
    ev = db.get(SystemEvent, event_id)
    if not ev:
        return False
    if not ev.is_read:
        ev.is_read = True
        ev.read_at = datetime.now(timezone.utc)
        db.commit()
    return True


def mark_all_events_read(db: Session) -> int:
    n = db.scalar(
        select(func.count()).select_from(SystemEvent).where(SystemEvent.is_read.is_(False))
    ) or 0
    if n:
        db.execute(
            SystemEvent.__table__.update()
            .where(SystemEvent.is_read.is_(False))
            .values(is_read=True, read_at=datetime.now(timezone.utc))
        )
        db.commit()
    return int(n)


def _serialize(e: SystemEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "category": e.category,
        "action": e.action,
        "title": e.title,
        "summary": e.summary,
        "status": e.status,
        "level": e.level,
        "detail": e.detail,
        "is_read": e.is_read,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
