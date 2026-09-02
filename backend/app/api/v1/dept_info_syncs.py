"""部门间信息同步路由 - 跨部门协作与信息共享登记

- 发起部门:提交登记,可调整、补充备注
- 接收部门:查阅、标记已读、回复
- 管理层:闭环、审计查询
所有 POST/PUT/PATCH/DELETE 请求由 operation_log 中间件自动落库形成操作审计链路。
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import String, func, or_, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.dept_info_sync import DeptInfoSync, SyncCategory, SyncPriority, SyncStatus
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/dept-info-syncs", tags=["部门间信息同步"])

_DT_KEYS = {"effective_from", "deadline", "responded_at", "closed_at"}


def _serialize(s: DeptInfoSync) -> dict:
    d = {c.name: getattr(s, c.name) for c in s.__table__.columns}
    for k in _DT_KEYS:
        v = d.get(k)
        d[k] = v.isoformat() if v and hasattr(v, "isoformat") else v
    d["created_at"] = s.created_at.isoformat() if s.created_at else None
    d["updated_at"] = s.updated_at.isoformat() if s.updated_at else None
    return d


def _as_list(x):
    if x is None:
        return None
    if isinstance(x, list):
        return x
    return list(x)


@router.get("", response_model=R[PageBase], summary="部门同步列表")
def list_syncs(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None, description="分类: rule_change/process_change/activity_notice/risk_alert/data_sync/coordination/other"),
    priority: str | None = Query(None, description="优先级: low/normal/high/urgent"),
    status: str | None = Query(None, description="状态: draft/sent/read/responded/closed"),
    from_department: str | None = Query(None, description="发起部门(模糊)"),
    to_department: str | None = Query(None, description="接收部门(模糊)"),
    keyword: str | None = Query(None, description="标题/内容关键词"),
    mine_only: bool = Query(False, description="只看我是发送人/在接收人内的记录"),
):
    stmt = select(DeptInfoSync)
    if category:
        try:
            stmt = stmt.where(DeptInfoSync.category == SyncCategory(category).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效分类: {category}")
    if priority:
        try:
            stmt = stmt.where(DeptInfoSync.priority == SyncPriority(priority).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效优先级: {priority}")
    if status:
        try:
            stmt = stmt.where(DeptInfoSync.status == SyncStatus(status).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效状态: {status}")
    if from_department:
        stmt = stmt.where(DeptInfoSync.from_department.ilike(f"%{from_department}%"))
    if to_department:
        stmt = stmt.where(DeptInfoSync.to_departments.ilike(f"%{to_department}%"))
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(DeptInfoSync.title.ilike(like), DeptInfoSync.content.ilike(like)))
    if mine_only:
        uid = current_user.id
        stmt = stmt.where(
            or_(
                DeptInfoSync.sender_id == uid,
                # receiver_ids 是 JSONB 数组,用 cast 到 text 做包含判断即可(无高级 JSON 索引也够用)
                DeptInfoSync.receiver_ids.cast(String).ilike(f"%{uid}%"),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(DeptInfoSync.updated_at.desc().nullslast(), DeptInfoSync.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize(s) for s in items], total=total or 0, page=page, page_size=page_size))


@router.get("/{sync_id}", response_model=R, summary="部门同步详情")
def get_sync(sync_id: int, db: DBSession, _: CurrentUser):
    s = db.get(DeptInfoSync, sync_id)
    if not s:
        raise HTTPException(status_code=404, detail="记录不存在")
    return R(data=_serialize(s))


@router.post("", response_model=R, summary="登记部门同步(草稿)")
def create_sync(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    if not payload.get("title"):
        raise HTTPException(status_code=400, detail="缺少标题")
    data = {
        "title": payload["title"],
        "from_department": payload.get("from_department"),
        "to_departments": payload.get("to_departments"),
        "content": payload.get("content"),
        "impact": payload.get("impact"),
        "attachment_urls": _as_list(payload.get("attachment_urls")),
        "related_incident_id": payload.get("related_incident_id"),
        "related_measure_id": payload.get("related_measure_id"),
        "receiver_ids": _as_list(payload.get("receiver_ids")),
        "remark": payload.get("remark"),
    }
    try:
        data["category"] = SyncCategory(payload.get("category", "other")).value
        data["priority"] = SyncPriority(payload.get("priority", "normal")).value
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    status = payload.get("status", SyncStatus.DRAFT.value)
    if status not in {s.value for s in SyncStatus}:
        raise HTTPException(status_code=400, detail="无效状态")
    data["status"] = status
    for k in ("effective_from", "deadline"):
        v = payload.get(k)
        data[k] = datetime.fromisoformat(v) if v else None
    data["sender_id"] = payload.get("sender_id") or current_user.id
    data["created_by"] = current_user.username
    s = DeptInfoSync(**data)
    db.add(s)
    db.commit()
    db.refresh(s)
    return R(data=_serialize(s))


@router.put("/{sync_id}", response_model=R, summary="更新/调整部门同步")
def update_sync(sync_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    s = db.get(DeptInfoSync, sync_id)
    if not s:
        raise HTTPException(status_code=404, detail="记录不存在")
    for k in (
        "title", "from_department", "to_departments", "content", "impact",
        "related_incident_id", "related_measure_id", "remark",
    ):
        if k in payload:
            setattr(s, k, payload[k])
    if "category" in payload:
        s.category = SyncCategory(payload["category"]).value
    if "priority" in payload:
        s.priority = SyncPriority(payload["priority"]).value
    if "status" in payload:
        s.status = SyncStatus(payload["status"]).value
    if "attachment_urls" in payload:
        s.attachment_urls = _as_list(payload["attachment_urls"])
    if "receiver_ids" in payload:
        s.receiver_ids = _as_list(payload["receiver_ids"])
    for k in ("effective_from", "deadline"):
        if k in payload:
            setattr(s, k, datetime.fromisoformat(payload[k]) if payload[k] else None)
    db.commit()
    db.refresh(s)
    return R(data=_serialize(s))


@router.post("/{sync_id}/classify", response_model=R, summary="分类/优先级调整")
def classify_sync(sync_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    s = db.get(DeptInfoSync, sync_id)
    if not s:
        raise HTTPException(status_code=404, detail="记录不存在")
    if "category" in payload:
        s.category = SyncCategory(payload["category"]).value
    if "priority" in payload:
        s.priority = SyncPriority(payload["priority"]).value
    if payload.get("remark"):
        s.remark = (s.remark or "") + f"\n[分类调整@{datetime.now().isoformat(timespec='minutes')}] {payload['remark']}"
    db.commit()
    db.refresh(s)
    return R(data=_serialize(s))


@router.post("/{sync_id}/register", response_model=R, summary="发布/登记正式同步(草稿→已发布)")
def register_sync(sync_id: int, db: DBSession, current_user: AdminOrSupervisor):
    s = db.get(DeptInfoSync, sync_id)
    if not s:
        raise HTTPException(status_code=404, detail="记录不存在")
    s.status = SyncStatus.SENT.value
    db.commit()
    db.refresh(s)
    return R(data=_serialize(s))


@router.post("/{sync_id}/read", response_model=R, summary="标记已读(阅读回执)")
def read_sync(sync_id: int, db: DBSession, current_user: CurrentUser):
    s = db.get(DeptInfoSync, sync_id)
    if not s:
        raise HTTPException(status_code=404, detail="记录不存在")
    receipts = list(s.read_receipts or [])
    if not any(r.get("user_id") == current_user.id for r in receipts):
        receipts.append({
            "user_id": current_user.id,
            "username": current_user.username,
            "read_at": datetime.now().isoformat(timespec="seconds"),
        })
        s.read_receipts = receipts
    if s.status == SyncStatus.SENT.value:
        s.status = SyncStatus.READ.value
    db.commit()
    db.refresh(s)
    return R(data=_serialize(s))


@router.post("/{sync_id}/respond", response_model=R, summary="接收方回复/协调结果")
def respond_sync(sync_id: int, payload: dict, db: DBSession, current_user: CurrentUser):
    s = db.get(DeptInfoSync, sync_id)
    if not s:
        raise HTTPException(status_code=404, detail="记录不存在")
    if not payload.get("response"):
        raise HTTPException(status_code=400, detail="缺少回复内容")
    s.response = (s.response or "") + f"\n[{current_user.username}@{datetime.now().isoformat(timespec='minutes')}]\n{payload['response']}"
    s.responded_at = datetime.utcnow()
    s.responded_by = current_user.username
    if s.status in {SyncStatus.SENT.value, SyncStatus.READ.value}:
        s.status = SyncStatus.RESPONDED.value
    db.commit()
    db.refresh(s)
    return R(data=_serialize(s))


@router.post("/{sync_id}/close", response_model=R, summary="闭环同步(确认完成)")
def close_sync(sync_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    s = db.get(DeptInfoSync, sync_id)
    if not s:
        raise HTTPException(status_code=404, detail="记录不存在")
    s.status = SyncStatus.CLOSED.value
    s.closed_at = datetime.utcnow()
    s.closed_by = current_user.username
    if payload.get("remark"):
        s.remark = (s.remark or "") + f"\n[闭环@{datetime.now().isoformat(timespec='minutes')}] {payload['remark']}"
    db.commit()
    db.refresh(s)
    return R(data=_serialize(s))


@router.delete("/{sync_id}", response_model=R, summary="删除草稿")
def delete_sync(sync_id: int, db: DBSession, current_user: AdminOrSupervisor):
    s = db.get(DeptInfoSync, sync_id)
    if not s:
        raise HTTPException(status_code=404, detail="记录不存在")
    if s.status != SyncStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="仅草稿可删除")
    db.delete(s)
    db.commit()
    return R(data={"id": sync_id})


@router.get("/meta/categories", response_model=R, summary="同步分类枚举")
def list_categories(_: CurrentUser):
    return R(data=[{"value": s.value, "label": s.name} for s in SyncCategory])


@router.get("/meta/priorities", response_model=R, summary="优先级枚举")
def list_priorities(_: CurrentUser):
    return R(data=[{"value": s.value, "label": s.name} for s in SyncPriority])


@router.get("/meta/statuses", response_model=R, summary="同步状态枚举")
def list_statuses(_: CurrentUser):
    return R(data=[{"value": s.value, "label": s.name} for s in SyncStatus])
