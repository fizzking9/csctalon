"""策略文档路由 - 发布页核心

架构定位(H41~43):策略/规范统一发布,保证全员一致。
- 管理层(admin/supervisor):草稿编辑、发布、新建版本、查看已读情况
- 客服(agent):只看已发布文档,查看时自动标记已读

版本管理:同 title 只能有一个 published;发布新版本时旧 published 自动 archived。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.strategy import StrategyDoc, StrategyReadReceipt
from app.models.user import RoleEnum, User
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/strategy", tags=["策略发布"])


# ---------- 序列化 ----------


def _serialize_doc(d: StrategyDoc, read_count: int | None = None) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "category": d.category,
        "version": d.version,
        "status": d.status,
        "published_at": d.published_at.isoformat() if d.published_at else None,
        "published_by": d.published_by,
        "created_by": d.created_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        **({"read_count": read_count} if read_count is not None else {}),
    }


# ---------- 列表与详情 ----------


@router.get("", response_model=R[PageBase], summary="策略文档列表")
def list_docs(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
):
    """管理层看全部;客服只看 published"""
    stmt = select(StrategyDoc)
    # 客服只能看已发布
    if current_user.role == RoleEnum.AGENT.value:
        stmt = stmt.where(StrategyDoc.status == "published")
    else:
        if status:
            stmt = stmt.where(StrategyDoc.status == status)
    if category:
        stmt = stmt.where(StrategyDoc.category == category)
    if keyword:
        stmt = stmt.where(StrategyDoc.title.ilike(f"%{keyword}%"))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(StrategyDoc.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    # 批量查已读数 + 当前用户是否已读
    doc_ids = [d.id for d in items]
    read_counts = {
        r[0]: r[1]
        for r in db.execute(
            select(StrategyReadReceipt.doc_id, func.count())
            .where(StrategyReadReceipt.doc_id.in_(doc_ids))
            .group_by(StrategyReadReceipt.doc_id)
        ).all()
    } if doc_ids else {}
    my_reads = set(
        db.scalars(
            select(StrategyReadReceipt.doc_id).where(
                StrategyReadReceipt.doc_id.in_(doc_ids),
                StrategyReadReceipt.user_id == current_user.id,
            )
        )
    ) if doc_ids else set()
    result_items = []
    for d in items:
        item = _serialize_doc(d, read_counts.get(d.id, 0))
        item["has_read"] = d.id in my_reads
        result_items.append(item)
    return R(
        data=PageBase(items=result_items, total=total or 0, page=page, page_size=page_size)
    )


@router.get("/{doc_id}", response_model=R, summary="文档详情")
def get_doc(doc_id: int, db: DBSession, current_user: CurrentUser):
    doc = db.get(StrategyDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 客服只能看已发布
    if current_user.role == RoleEnum.AGENT.value and doc.status != "published":
        raise HTTPException(status_code=403, detail="无权查看该文档")
    data = _serialize_doc(doc)
    data["content"] = doc.content
    # 客服查看已发布文档 → 自动标记已读(首次阅读时间保留)
    if current_user.role == RoleEnum.AGENT.value and doc.status == "published":
        _mark_read(db, doc_id, current_user.id)
        data["has_read"] = True
    # 当前用户是否已读
    elif current_user.role != RoleEnum.AGENT.value:
        receipt = db.scalar(
            select(StrategyReadReceipt).where(
                StrategyReadReceipt.doc_id == doc_id, StrategyReadReceipt.user_id == current_user.id
            )
        )
        data["has_read"] = receipt is not None
    return R(data=data)


# ---------- CRUD(管理层) ----------


@router.post("", response_model=R, summary="创建草稿")
def create_doc(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    doc = StrategyDoc(
        title=title,
        category=payload.get("category"),
        content=payload.get("content"),
        version=1,
        status="draft",
        created_by=current_user.username,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return R(data=_serialize_doc(doc, 0))


@router.put("/{doc_id}", response_model=R, summary="更新文档(仅草稿)")
def update_doc(doc_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    doc = db.get(StrategyDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status != "draft":
        raise HTTPException(status_code=400, detail="仅草稿可编辑,已发布请新建版本")
    for k in ("title", "category", "content"):
        if k in payload:
            setattr(doc, k, payload[k])
    db.commit()
    db.refresh(doc)
    return R(data=_serialize_doc(doc))


@router.delete("/{doc_id}", response_model=R, summary="删除文档(仅草稿/已归档)")
def delete_doc(doc_id: int, db: DBSession, _: AdminOrSupervisor):
    doc = db.get(StrategyDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status == "published":
        raise HTTPException(status_code=400, detail="已发布文档不能删除,请先归档")
    # 级联删除已读回执
    for r in db.scalars(select(StrategyReadReceipt).where(StrategyReadReceipt.doc_id == doc_id)):
        db.delete(r)
    db.flush()
    db.delete(doc)
    db.commit()
    return R()


# ---------- 发布与版本 ----------


@router.post("/{doc_id}/publish", response_model=R, summary="发布文档")
def publish_doc(doc_id: int, db: DBSession, current_user: AdminOrSupervisor):
    """发布草稿:status→published,同 title 的其他 published 自动 archived"""
    doc = db.get(StrategyDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status != "draft":
        raise HTTPException(status_code=400, detail=f"仅草稿可发布,当前状态: {doc.status}")
    # 同 title 的其他 published 文档归档
    others = db.scalars(
        select(StrategyDoc).where(
            StrategyDoc.title == doc.title,
            StrategyDoc.status == "published",
            StrategyDoc.id != doc_id,
        )
    ).all()
    for o in others:
        o.status = "archived"
    doc.status = "published"
    doc.published_at = datetime.now(timezone.utc)
    doc.published_by = current_user.username
    db.commit()
    db.refresh(doc)
    return R(data=_serialize_doc(doc))


@router.post("/{doc_id}/new-version", response_model=R, summary="基于已发布文档新建版本")
def new_version(doc_id: int, db: DBSession, current_user: AdminOrSupervisor):
    """复制已发布文档内容为新草稿,version = 同 title max(version)+1"""
    src = db.get(StrategyDoc, doc_id)
    if not src:
        raise HTTPException(status_code=404, detail="文档不存在")
    max_ver = db.scalar(
        select(func.max(StrategyDoc.version)).where(StrategyDoc.title == src.title)
    ) or 0
    doc = StrategyDoc(
        title=src.title,
        category=src.category,
        content=src.content,
        version=max_ver + 1,
        status="draft",
        created_by=current_user.username,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return R(data=_serialize_doc(doc, 0))


# ---------- 已读回执 ----------


def _mark_read(db, doc_id: int, user_id: int) -> None:
    """标记已读(首次阅读时间保留,重复查看不更新 read_at)"""
    db.execute(
        pg_insert(StrategyReadReceipt)
        .values(doc_id=doc_id, user_id=user_id, read_at=datetime.now(timezone.utc))
        .on_conflict_do_nothing(index_elements=["doc_id", "user_id"])
    )
    db.commit()


@router.post("/{doc_id}/read", response_model=R, summary="标记已读")
def mark_read(doc_id: int, db: DBSession, current_user: CurrentUser):
    doc = db.get(StrategyDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    _mark_read(db, doc_id, current_user.id)
    return R(data={"doc_id": doc_id, "has_read": True})


@router.get("/{doc_id}/receipts", response_model=R, summary="已读情况(管理层)")
def list_receipts(doc_id: int, db: DBSession, _: AdminOrSupervisor):
    """返回已读/未读用户列表(所有 active 的 supervisor+agent)"""
    doc = db.get(StrategyDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 应读用户:所有 active 的 supervisor 和 agent(admin 默认视为已读,不统计)
    should_read = list(
        db.scalars(
            select(User).where(
                User.is_active.is_(True),
                User.role.in_([RoleEnum.SUPERVISOR.value, RoleEnum.AGENT.value]),
            ).order_by(User.role, User.display_name)
        )
    )
    read_user_ids = set(
        db.scalars(select(StrategyReadReceipt.user_id).where(StrategyReadReceipt.doc_id == doc_id))
    )
    read_list = []
    unread_list = []
    for u in should_read:
        info = {"user_id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role}
        if u.id in read_user_ids:
            receipt = db.scalar(
                select(StrategyReadReceipt).where(
                    StrategyReadReceipt.doc_id == doc_id, StrategyReadReceipt.user_id == u.id
                )
            )
            info["read_at"] = receipt.read_at.isoformat() if receipt and receipt.read_at else None
            read_list.append(info)
        else:
            unread_list.append(info)
    return R(
        data={
            "doc_id": doc_id,
            "title": doc.title,
            "total": len(should_read),
            "read_count": len(read_list),
            "unread_count": len(unread_list),
            "read_users": read_list,
            "unread_users": unread_list,
        }
    )
