"""系统事务通知服务 - 记录/查询系统关键事务事件

供各服务(同步/推送/导入/预警等)在关键节点调用 record_system_event() 写入概要,
供前端「事务通知」页与管理角标查询。
独立于数据采集模块, category 可自由扩展。

2026-09-29 治理增强(事故: LLM key 失配 14h/100 次 403 无人察觉):
  - 角标降噪: info 级事件默认不进未读(仍入库可查), 只有 warning/error 亮角标;
    可用 badge=True 显式覆盖(需要管理员确认的 info)。
  - 通用去重: dedup_minutes 参数, 窗口内同 category+action+level+title 只记一条,
    避免持续性故障(如每 30 分钟重试的预生成)刷屏。去重判定走 DB, 跨进程/重启有效。
  - 系统外发: error 级事件默认后台线程推群机器人 webhook(见 notification_channel
    .dispatch_system_alert_async), 失败静默不影响主业务; 用 external_alert=False 关闭。
"""

import logging
from datetime import datetime, timedelta, timezone
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
    *,
    badge: bool | None = None,
    dedup_minutes: int | None = None,
    external_alert: bool | None = None,
) -> SystemEvent | None:
    """写入一条系统事务事件。

    category: sync(数据同步) / push(推送接收) / import(导入) / alert(预警) /
              schedule(定时调度) / match(关联匹配) / auth(登录验证) / llm(模型链路)
    status: success / failed / partial / info
    level: 默认按 status 推导(info→info, success→info, partial→warning, failed→error)

    badge: 是否进入未读角标。None(默认)=仅 warning/error 亮角标, info 自动已读;
           True=强制进未读; False=强制不进。
    dedup_minutes: 去重窗口(分钟)。窗口内已存在同 category+action+level+title 的
           事件则跳过(返回 None), 用于持续性故障降噪。判定走 DB, 跨 worker/重启有效。
    external_alert: 是否外发群机器人。None(默认)=仅 error 外发; True/False 显式控制。
           外发失败只记日志, 不影响入库与主业务。
    """
    if level is None:
        level = {
            "failed": "error",
            "partial": "warning",
            "error": "error",
            "success": "info",
            "info": "info",
        }.get(status, "info")

    # ── 去重(先查后写; 查/写失败都不阻塞, 退化为照常写入) ──
    if dedup_minutes and dedup_minutes > 0:
        try:
            since = datetime.now(timezone.utc) - timedelta(minutes=dedup_minutes)
            exists = db.query(SystemEvent.id).filter(
                SystemEvent.category == category,
                SystemEvent.action == action,
                SystemEvent.level == level,
                SystemEvent.title == title[:255],
                SystemEvent.created_at >= since,
            ).first()
            if exists:
                return None
        except Exception as e:  # noqa: BLE001
            logger.warning("[system_event] 去重查询失败, 照常写入: %s", e)

    # ── 角标策略: 默认只有 warning/error 进未读, info 留痕但不打扰 ──
    if badge is None:
        unread = level in ("warning", "error")
    else:
        unread = badge

    ev = SystemEvent(
        category=category,
        action=action,
        title=title[:255],
        summary=(summary or "")[:512] or None,
        status=status,
        level=level,
        detail=detail,
        is_read=not unread,
        read_at=datetime.now(timezone.utc) if not unread else None,
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

    # ── error 级系统事件外发群机器人(后台线程, fire-and-forget) ──
    should_alert = level == "error" if external_alert is None else external_alert
    if should_alert and (not commit or ev.id):
        try:
            from app.services.notification_channel import dispatch_system_alert_async

            dispatch_system_alert_async(
                title=ev.title, summary=ev.summary, category=category, action=action)
        except Exception as e:  # noqa: BLE001  外发准备失败不影响主流程
            logger.warning("[system_event] 外发调度失败(不影响主流程): %s", e)
    return ev


def update_system_event(
    db: Session,
    event_id: int,
    *,
    status: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    detail: str | None = None,
    level: str | None = None,
    commit: bool = False,
) -> SystemEvent | None:
    """回填一条已存在的事件(工单 #165 异步任务用: 占位 running → success/failed)。

    为什么要有「占位 + 回填」: 手动体检/研判是 202 + 轮询模式, 需要**一行**事件贯穿
    running→终态, 而不是「开始写一行、结束再写一行」(重复留痕会淹没真实变化, 同 #166 教训)。
    event_id 不存在 → 返回 None(调用方自行决定是否降级为新建)。
    """
    ev = db.get(SystemEvent, event_id)
    if ev is None:
        return None
    if status is not None:
        ev.status = status
        ev.level = level or {
            "failed": "error", "partial": "warning", "success": "info",
            "running": "info", "info": "info",
        }.get(status, "info")
    if title is not None:
        ev.title = title[:255]
    if summary is not None:
        ev.summary = summary[:512] or None
    if detail is not None:
        ev.detail = detail
    if commit:
        try:
            db.commit()
            db.refresh(ev)
        except Exception as e:  # noqa: BLE001  留痕失败不影响业务主流程
            db.rollback()
            logger.warning(f"[system_event] 回填失败(已回滚,不影响主流程): {e}")
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
    """角标用概要: 未读数(分级) / 最近24h各类结果计数

    2026-09-29: unread 仅统计 warning/error(info 默认不进未读);
    另给 unread_error, 供前端铃铛红色高亮(系统故障一眼可见)。
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    unread = db.scalar(
        select(func.count()).select_from(SystemEvent).where(SystemEvent.is_read.is_(False))
    ) or 0
    unread_error = db.scalar(
        select(func.count()).select_from(SystemEvent).where(
            SystemEvent.is_read.is_(False), SystemEvent.level == "error")
    ) or 0
    unread_warning = db.scalar(
        select(func.count()).select_from(SystemEvent).where(
            SystemEvent.is_read.is_(False), SystemEvent.level == "warning")
    ) or 0
    recent = dict(db.execute(
        select(SystemEvent.status, func.count())
        .where(SystemEvent.created_at >= since)
        .group_by(SystemEvent.status)
    ).all())
    return {
        "unread": int(unread),
        "unread_error": int(unread_error),
        "unread_warning": int(unread_warning),
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
