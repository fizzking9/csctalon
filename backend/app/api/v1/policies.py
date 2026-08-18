"""管理制度路由 - SOP/规范/话术/应急预案等

管理层(AdminOrSupervisor)维护;全员可查看已发布。
所有变更请求(POST/PUT/DELETE)由中间件自动落 operation_logs 表,
其中「分类/审批/发布/归档/调整」等动作通过子路径映射为中文操作标签便于查询统计。
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.policy import Policy, PolicyCategory, PolicyStatus
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/policies", tags=["管理制度"])


_DT_KEYS = {"valid_from", "valid_to", "approved_at"}


def _serialize(p: Policy) -> dict:
    d = {c.name: getattr(p, c.name) for c in p.__table__.columns}
    for k in _DT_KEYS:
        v = d.get(k)
        d[k] = v.isoformat() if v and hasattr(v, "isoformat") else v
    d["created_at"] = p.created_at.isoformat() if p.created_at else None
    d["updated_at"] = p.updated_at.isoformat() if p.updated_at else None
    return d


@router.get("", response_model=R[PageBase], summary="管理制度列表")
def list_policies(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None, description="分类: service_standard/process_sop/management_rule/communication_script/incident_response/other"),
    status: str | None = Query(None, description="状态: draft/pending_approval/published/archived"),
    keyword: str | None = Query(None, description="标题/摘要关键词"),
    only_published: bool = Query(False, description="只看已发布"),
):
    stmt = select(Policy)
    if category:
        try:
            stmt = stmt.where(Policy.category == PolicyCategory(category).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的分类: {category}")
    if status:
        try:
            stmt = stmt.where(Policy.status == PolicyStatus(status).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态: {status}")
    elif only_published:
        stmt = stmt.where(Policy.status == PolicyStatus.PUBLISHED.value)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where((Policy.title.ilike(like)) | (Policy.summary.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(Policy.updated_at.desc().nullslast(), Policy.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize(p) for p in items], total=total or 0, page=page, page_size=page_size))


@router.get("/{policy_id}", response_model=R, summary="管理制度详情")
def get_policy(policy_id: int, db: DBSession, _: CurrentUser):
    p = db.get(Policy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="制度不存在")
    # 浏览计数+1
    p.view_count = (p.view_count or 0) + 1
    db.commit()
    db.refresh(p)
    return R(data=_serialize(p))


@router.post("", response_model=R, summary="新建管理制度(草稿)")
def create_policy(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    if not payload.get("title"):
        raise HTTPException(status_code=400, detail="缺少标题")
    data = {
        "title": payload["title"],
        "category": PolicyCategory(payload.get("category", "other")).value,
        "summary": payload.get("summary"),
        "content": payload.get("content"),
        "attachment_urls": payload.get("attachment_urls"),
        "version": payload.get("version"),
        "owner_id": payload.get("owner_id"),
        "remark": payload.get("remark"),
        "topic_tags": payload.get("topic_tags"),
        "replaces_policy_id": payload.get("replaces_policy_id"),
        "replaced_by_policy_id": payload.get("replaced_by_policy_id"),
        "diagram_urls": payload.get("diagram_urls"),
    }
    for k in _DT_KEYS:
        v = payload.get(k)
        data[k] = datetime.fromisoformat(v) if v else None
    data["status"] = payload.get("status", PolicyStatus.DRAFT.value)
    if data["status"] not in {s.value for s in PolicyStatus}:
        raise HTTPException(status_code=400, detail="无效的状态")
    data["created_by"] = current_user.username
    p = Policy(**data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return R(data=_serialize(p))


@router.put("/{policy_id}", response_model=R, summary="更新管理制度(调整内容/分类/备注)")
def update_policy(policy_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    p = db.get(Policy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="制度不存在")
    updatable = ("title", "category", "summary", "content", "attachment_urls",
                 "version", "owner_id", "remark", "topic_tags", "replaces_policy_id",
                 "replaced_by_policy_id", "diagram_urls")
    for k in updatable:
        if k in payload:
            setattr(p, k, payload[k])
    if "category" in payload:
        try:
            p.category = PolicyCategory(payload["category"]).value
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的分类")
    for k in _DT_KEYS:
        if k in payload:
            setattr(p, k, datetime.fromisoformat(payload[k]) if payload[k] else None)
    if "status" in payload:
        try:
            p.status = PolicyStatus(payload["status"]).value
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的状态")
    db.commit()
    db.refresh(p)
    return R(data=_serialize(p))


@router.post("/{policy_id}/classify", response_model=R, summary="分类调整")
def classify_policy(policy_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    p = db.get(Policy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="制度不存在")
    cat = payload.get("category")
    if not cat:
        raise HTTPException(status_code=400, detail="缺少分类")
    try:
        p.category = PolicyCategory(cat).value
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的分类")
    if payload.get("remark"):
        p.remark = (p.remark or "") + f"\n[分类调整@{datetime.now().isoformat(timespec='minutes')}] {payload['remark']}"
    db.commit()
    db.refresh(p)
    return R(data=_serialize(p))


@router.post("/{policy_id}/review", response_model=R, summary="提交审批")
def submit_policy_review(policy_id: int, db: DBSession, current_user: AdminOrSupervisor):
    p = db.get(Policy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="制度不存在")
    p.status = PolicyStatus.PENDING_APPROVAL.value
    db.commit()
    db.refresh(p)
    return R(data=_serialize(p))


@router.post("/{policy_id}/approve", response_model=R, summary="审批通过并发布")
def approve_policy(policy_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    p = db.get(Policy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="制度不存在")
    p.status = PolicyStatus.PUBLISHED.value
    p.approver_id = current_user.id
    p.approved_at = datetime.now(p.created_at.tzinfo) if p.created_at and p.created_at.tzinfo else datetime.utcnow()
    if payload.get("approver_note"):
        p.approver_note = payload["approver_note"]
    db.commit()
    db.refresh(p)
    return R(data=_serialize(p))


@router.post("/{policy_id}/reject", response_model=R, summary="驳回审批")
def reject_policy(policy_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    p = db.get(Policy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="制度不存在")
    p.status = PolicyStatus.DRAFT.value
    note = payload.get("approver_note") or "驳回"
    p.approver_note = f"[{current_user.username}@{datetime.now().isoformat(timespec='minutes')}] {note}"
    db.commit()
    db.refresh(p)
    return R(data=_serialize(p))


@router.post("/{policy_id}/archive", response_model=R, summary="归档制度")
def archive_policy(policy_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    p = db.get(Policy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="制度不存在")
    p.status = PolicyStatus.ARCHIVED.value
    if payload.get("remark"):
        p.remark = (p.remark or "") + f"\n[归档@{datetime.now().isoformat(timespec='minutes')}] {payload['remark']}"
    db.commit()
    db.refresh(p)
    return R(data=_serialize(p))


@router.delete("/{policy_id}", response_model=R, summary="删除制度草稿")
def delete_policy(policy_id: int, db: DBSession, current_user: AdminOrSupervisor):
    p = db.get(Policy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="制度不存在")
    if p.status != PolicyStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="仅草稿状态可删除,已发布请走归档")
    db.delete(p)
    db.commit()
    return R(data={"id": policy_id})


@router.get("/meta/categories", response_model=R, summary="制度分类枚举")
def list_categories(_: CurrentUser):
    return R(data=[{"value": s.value, "label": s.name} for s in PolicyCategory])


@router.get("/meta/statuses", response_model=R, summary="制度状态枚举")
def list_statuses(_: CurrentUser):
    return R(data=[{"value": s.value, "label": s.name} for s in PolicyStatus])
