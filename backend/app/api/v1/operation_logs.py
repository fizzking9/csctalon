"""操作日志查询路由 - 管理员审计(I47:操作日志要落库可查)"""

from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import AdminUser, DBSession
from app.models.operation_log import OperationLog
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/operation-logs", tags=["操作日志"])


def _serialize(log: OperationLog) -> dict:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "username": log.username,
        "method": log.method,
        "path": log.path,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "status_code": log.status_code,
        "detail": log.detail,
        "ip": log.ip,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


@router.get("", response_model=R[PageBase], summary="操作日志列表(管理员)")
def list_logs(
    db: DBSession,
    _: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    username: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    status_code: int | None = None,
    start: str | None = Query(None, description="起始时间 ISO,如 2026-07-26T00:00:00"),
    end: str | None = Query(None, description="结束时间 ISO"),
):
    stmt = select(OperationLog)
    if username:
        stmt = stmt.where(OperationLog.username == username)
    if action:
        stmt = stmt.where(OperationLog.action == action)
    if target_type:
        stmt = stmt.where(OperationLog.target_type == target_type)
    if status_code is not None:
        stmt = stmt.where(OperationLog.status_code == status_code)
    if start:
        try:
            stmt = stmt.where(OperationLog.created_at >= datetime.fromisoformat(start))
        except ValueError:
            pass
    if end:
        try:
            stmt = stmt.where(OperationLog.created_at <= datetime.fromisoformat(end))
        except ValueError:
            pass

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(OperationLog.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(data=PageBase(items=[_serialize(i) for i in items], total=total or 0, page=page, page_size=page_size))


@router.get("/actions", response_model=R, summary="所有操作类型(用于筛选下拉)")
def list_actions(db: DBSession, _: AdminUser):
    """返回去重后的 action 列表,供前端筛选下拉"""
    rows = db.scalars(select(OperationLog.action).distinct().order_by(OperationLog.action)).all()
    return R(data=list(rows))


@router.get("/targets", response_model=R, summary="所有对象类型(用于筛选下拉)")
def list_target_types(db: DBSession, _: AdminUser):
    rows = db.scalars(
        select(OperationLog.target_type).distinct().where(OperationLog.target_type.is_not(None)).order_by(OperationLog.target_type)
    ).all()
    return R(data=list(rows))
