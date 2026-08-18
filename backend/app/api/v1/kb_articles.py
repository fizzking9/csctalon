"""知识库/话术库路由(规划设计 §七)"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.models.kb_article import KbArticle
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/kb-articles", tags=["知识库"])


def _serialize(k: KbArticle) -> dict:
    return {
        "id": k.id,
        "title": k.title,
        "category": k.category,
        "question": k.question,
        "answer": k.answer,
        "tags": k.tags,
        "related_policy_id": k.related_policy_id,
        "status": k.status,
        "view_count": k.view_count,
        "created_by": k.created_by,
        "created_at": k.created_at.isoformat() if k.created_at else None,
    }


@router.get("", response_model=R[PageBase], summary="知识/话术列表")
def list_articles(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = None,
    keyword: str | None = None,
    include_draft: bool = Query(False, description="是否含草稿(管理用)"),
):
    stmt = select(KbArticle)
    if category:
        stmt = stmt.where(KbArticle.category == category)
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where((KbArticle.title.like(kw)) | (KbArticle.question.like(kw)) | (KbArticle.answer.like(kw)))
    if not include_draft:
        stmt = stmt.where(KbArticle.status == "已发布")
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(KbArticle.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return R(data=PageBase(items=[_serialize(k) for k in items], total=total or 0, page=page, page_size=page_size))


@router.get("/categories", response_model=R, summary="知识分类列表")
def list_categories(db: DBSession, _: CurrentUser):
    rows = db.scalars(select(KbArticle.category).where(KbArticle.category.isnot(None)).distinct())
    return R(data=sorted([c for c in rows]))


@router.get("/{article_id}", response_model=R, summary="知识条目详情(检索计数+1)")
def get_article(article_id: int, db: DBSession, _: CurrentUser):
    k = db.get(KbArticle, article_id)
    if not k:
        raise HTTPException(status_code=404, detail="条目不存在")
    k.view_count = (k.view_count or 0) + 1
    db.commit()
    db.refresh(k)
    return R(data=_serialize(k))


@router.post("", response_model=R, summary="新建知识/话术条目")
def create_article(payload: dict, db: DBSession, current_user: ManagerUser):
    k = KbArticle(
        title=payload["title"],
        category=payload.get("category"),
        question=payload.get("question"),
        answer=payload.get("answer"),
        tags=payload.get("tags"),
        related_policy_id=payload.get("related_policy_id"),
        status=payload.get("status", "草稿"),
        created_by=current_user.username,
    )
    db.add(k)
    db.commit()
    db.refresh(k)
    return R(data=_serialize(k))


@router.put("/{article_id}", response_model=R, summary="更新知识/话术条目")
def update_article(article_id: int, payload: dict, db: DBSession, _: ManagerUser):
    k = db.get(KbArticle, article_id)
    if not k:
        raise HTTPException(status_code=404, detail="条目不存在")
    for f in ("title", "category", "question", "answer", "tags", "related_policy_id", "status"):
        if f in payload:
            setattr(k, f, payload[f])
    db.commit()
    db.refresh(k)
    return R(data=_serialize(k))


@router.delete("/{article_id}", response_model=R, summary="删除知识/话术条目")
def delete_article(article_id: int, db: DBSession, _: ManagerUser):
    k = db.get(KbArticle, article_id)
    if not k:
        raise HTTPException(status_code=404, detail="条目不存在")
    db.delete(k)
    db.commit()
    return R()
