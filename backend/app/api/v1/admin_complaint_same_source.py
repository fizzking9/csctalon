"""外部投诉同源案件管理（admin）— 工单 #208（⑤A 最小复核闭环）

- GET   /admin/complaint-same-source/groups          组分页（含成员/证据/④C 取值）
- GET   /admin/complaint-same-source/groups/{id}     组详情
- PATCH /admin/complaint-same-source/groups/{id}     人工终审 needs_review/auto → confirmed/rejected
- POST  /admin/complaint-same-source/run             手动触发 v5 重算（离线小批量，同步执行）

口径单源在 app.services.complaint_same_source；本路由只做鉴权/编排/序列化。
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminUser, DBSession
from app.models.complaint_same_source import ComplaintSameSourceGroup
from app.schemas.common import R
from app.services import complaint_same_source as css

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/complaint-same-source", tags=["admin-complaint-same-source"])

_REVIEW_TARGETS = {"auto", "needs_review"}
_REVIEW_RESULTS = {"confirmed", "rejected"}


@router.get("/groups", response_model=R, summary="同源案件组分页列表(admin)")
def list_groups(
    db: DBSession,
    _: AdminUser,
    status: str | None = Query(None, description="auto/needs_review/confirmed/rejected"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    q = select(ComplaintSameSourceGroup)
    if status:
        q = q.where(ComplaintSameSourceGroup.status == status)
    total = db.scalar(
        select(func.count()).select_from(
            q.with_only_columns(ComplaintSameSourceGroup.id).subquery()
        )
    )
    rows = db.scalars(
        q.order_by(
            ComplaintSameSourceGroup.status,  # needs_review 默认靠前由前端筛，不特殊排序
            ComplaintSameSourceGroup.first_complaint_date.desc().nullslast(),
            ComplaintSameSourceGroup.id.desc(),
        ).limit(page_size).offset((page - 1) * page_size)
    ).all()
    return R(data={
        "total": total or 0,
        "page": page,
        "page_size": page_size,
        "items": [css.group_view(db, g) for g in rows],
    })


@router.get("/groups/{group_id}", response_model=R, summary="同源案件组详情(admin)")
def get_group(group_id: int, db: DBSession, _: AdminUser):
    g = db.get(ComplaintSameSourceGroup, group_id)
    if not g:
        raise HTTPException(404, "同源组不存在")
    return R(data=css.group_view(db, g))


@router.patch("/groups/{group_id}", response_model=R, summary="人工终审同源组(admin)")
def review_group(group_id: int, payload: dict, db: DBSession, admin: AdminUser):
    """⑤A：把 auto/needs_review 组人工确认为 confirmed / rejected。

    已终审（confirmed/rejected）组不可再改（需改请重跑任务解散重建后重新判定）。
    """
    g = db.get(ComplaintSameSourceGroup, group_id)
    if not g:
        raise HTTPException(404, "同源组不存在")
    if g.status not in _REVIEW_TARGETS:
        raise HTTPException(409, f"组已终审({g.status})，不可修改；如需调整请重跑判重任务")
    new_status = (payload.get("status") or "").strip()
    if new_status not in _REVIEW_RESULTS:
        raise HTTPException(400, "status 仅支持 confirmed / rejected")
    g.status = new_status
    g.review_note = (payload.get("note") or None)
    g.reviewed_by = admin.id
    g.reviewed_name = admin.display_name
    g.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("same_source group %s reviewed → %s by %s", group_id, new_status, admin.display_name)
    return R(message="已保存人工判定", data={"id": g.id, "status": g.status})


@router.post("/run", response_model=R, summary="手动触发同源判重 v5 重算(admin)")
def run_grouping(db: DBSession, _: AdminUser, payload: dict | None = None):
    """离线小批量任务（外部投诉量级数千行），同步执行并提交。

    payload: {"dry_run": true} 时只返回统计不落库（默认 false 落库）。
    幂等：confirmed/rejected 人工组保留，auto/needs_review 解散重建。
    """
    dry_run = bool((payload or {}).get("dry_run", False))
    stats = css.rebuild_groups(db, persist=not dry_run)
    if not dry_run:
        db.commit()
    return R(data=stats, message=("dry-run 预览（未落库）" if dry_run else "判重重算完成"))
