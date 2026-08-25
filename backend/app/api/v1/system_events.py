"""系统事务通知路由(2026-08-25 新增)

面向系统管理员: 查看系统关键事务(同步/推送/导入/预警等)的执行概要,
独立于数据采集模块, 供后续更多事务类型扩展。
"""

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.schemas.common import R
from app.services.system_event_service import (
    event_summary,
    list_system_events,
    mark_all_events_read,
    mark_event_read,
)

router = APIRouter(prefix="/system-events", tags=["事务通知"])


@router.get("", response_model=R, summary="系统事务通知列表(分页, 可筛选)")
def list_events(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
    category: str | None = Query(None, description="类别: sync/push/import/alert/schedule/match/auth"),
    status: str | None = Query(None, description="结果: success/failed/partial/info"),
    level: str | None = Query(None, description="级别: info/warning/error"),
    unread_only: bool = Query(False, description="仅未读"),
):
    return R(data=list_system_events(db, page, page_size, category, status, level, unread_only))


@router.get("/summary", response_model=R, summary="角标概要: 未读数 + 最近24h结果分布")
def summary(db: DBSession, _: CurrentUser):
    return R(data=event_summary(db))


@router.post("/{event_id}/read", response_model=R, summary="标记单条已读")
def mark_read(event_id: int, db: DBSession, _: ManagerUser):
    mark_event_read(db, event_id)
    return R(message="已读")


@router.post("/read-all", response_model=R, summary="全部标记已读")
def mark_all_read(db: DBSession, _: ManagerUser):
    n = mark_all_events_read(db)
    return R(message=f"已标记 {n} 条已读")
