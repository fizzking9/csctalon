"""人员统一管理路由 - 别名提取、归并、拆分"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, AdminOrSupervisor, CurrentUser, DBSession
from app.models.person import Person, PersonAlias
from app.schemas.common import PageBase, R
from app.schemas.person import (
    MergeNewRequest,
    MergeRequest,
    PersonAliasOut,
    PersonDetail,
    PersonOut,
    PersonUpdate,
    SplitRequest,
)
from app.services.person_extractor import extract_person_aliases

router = APIRouter(prefix="/persons", tags=["系统-人员"])


def _with_alias_count(db: Session, p: Person) -> PersonOut:
    cnt = db.scalar(
        select(func.count()).select_from(PersonAlias).where(PersonAlias.person_id == p.id, PersonAlias.is_system.is_(False))
    ) or 0
    out = PersonOut.model_validate(p)
    out.alias_count = cnt
    return out


@router.get("", response_model=R, summary="人员列表(分页)")
def list_persons(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="confirmed/unconfirmed/inactive"),
):
    stmt = select(Person)
    if status == "confirmed":
        stmt = stmt.where(Person.is_confirmed.is_(True), Person.is_active.is_(True))
    elif status == "unconfirmed":
        stmt = stmt.where(Person.is_confirmed.is_(False), Person.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(Person.is_active.is_(False))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(Person.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return R(data=PageBase(items=[_with_alias_count(db, p) for p in items], total=total, page=page, page_size=page_size))


@router.get("/unmerged-aliases", response_model=R, summary="待归并别名列表(系统别名除外)")
def list_unmerged_aliases(db: DBSession, _: CurrentUser):
    items = list(
        db.scalars(
            select(PersonAlias)
            .where(PersonAlias.is_system.is_(False))
            .order_by(PersonAlias.alias_normalized, PersonAlias.id)
        )
    )
    return R(data=[PersonAliasOut.model_validate(a) for a in items])


@router.get("/suggestions", response_model=R, summary="归并建议(normalized 重复的别名组)")
def merge_suggestions(db: DBSession, _: AdminOrSupervisor):
    """返回 alias_normalized 重复的别名组，提示管理员一键归并"""
    rows = db.execute(
        select(PersonAlias.alias_normalized, func.count().label("cnt"))
        .where(PersonAlias.is_system.is_(False))
        .group_by(PersonAlias.alias_normalized)
        .having(func.count() > 1)
    ).all()
    groups = []
    for normalized, _cnt in rows:
        aliases = list(
            db.scalars(select(PersonAlias).where(PersonAlias.alias_normalized == normalized).order_by(PersonAlias.id))
        )
        groups.append({
            "normalized": normalized,
            "aliases": [PersonAliasOut.model_validate(a) for a in aliases],
        })
    return R(data=groups)


@router.get("/{person_id}", response_model=R, summary="人员详情(含别名)")
def get_person(person_id: int, db: DBSession, _: CurrentUser):
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")
    detail = PersonDetail.model_validate(p)
    aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.person_id == person_id).order_by(PersonAlias.id)))
    detail.aliases = [PersonAliasOut.model_validate(a) for a in aliases]
    detail.alias_count = len([a for a in aliases if not a.is_system])
    return R(data=detail)


@router.put("/{person_id}", response_model=R, summary="更新人员信息")
def update_person(person_id: int, payload: PersonUpdate, db: DBSession, _: AdminUser):
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return R(data=_with_alias_count(db, p))


@router.post("/{person_id}/merge", response_model=R, summary="把多个别名归并到该人员")
def merge_aliases(person_id: int, payload: MergeRequest, db: DBSession, _: AdminUser):
    target = db.get(Person, person_id)
    if not target:
        raise HTTPException(status_code=404, detail="目标人员不存在")
    aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.id.in_(payload.alias_ids))))
    if not aliases:
        raise HTTPException(status_code=400, detail="未找到指定别名")
    # 收集被合并的 person_id（除目标外，后续软删除）
    affected = {a.person_id for a in aliases if a.person_id and a.person_id != person_id}
    for a in aliases:
        a.person_id = person_id
    if affected:
        db.execute(update(Person).where(Person.id.in_(affected)).values(is_active=False))
    target.is_confirmed = True
    db.commit()
    db.refresh(target)
    return R(data=_with_alias_count(db, target))


@router.post("/merge", response_model=R, summary="新建人员并归并多个别名")
def merge_new(payload: MergeNewRequest, db: DBSession, _: AdminUser):
    aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.id.in_(payload.alias_ids))))
    if not aliases:
        raise HTTPException(status_code=400, detail="未找到指定别名")
    affected = {a.person_id for a in aliases if a.person_id}
    p = Person(display_name=payload.display_name, role=payload.role, group_name=payload.group_name, is_confirmed=True)
    db.add(p)
    db.flush()
    for a in aliases:
        a.person_id = p.id
    if affected:
        db.execute(update(Person).where(Person.id.in_(affected)).values(is_active=False))
    db.commit()
    db.refresh(p)
    return R(data=_with_alias_count(db, p))


@router.post("/{person_id}/split", response_model=R, summary="从人员拆出别名(各自建新 person)")
def split_aliases(person_id: int, payload: SplitRequest, db: DBSession, _: AdminUser):
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")
    aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.id.in_(payload.alias_ids))))
    new_persons = []
    for a in aliases:
        if a.person_id != person_id:
            continue
        np = Person(display_name=a.alias, is_confirmed=False, role="agent")
        db.add(np)
        db.flush()
        a.person_id = np.id
        new_persons.append(np)
    db.commit()
    return R(data={"split_count": len(new_persons), "new_person_ids": [np.id for np in new_persons]})


@router.post("/refresh-aliases", response_model=R, summary="触发别名重新提取(幂等)")
def refresh_aliases(db: DBSession, _: AdminUser):
    stats = extract_person_aliases(db)
    return R(data=stats)


@router.delete("/{person_id}", response_model=R, summary="停用人员(不物理删除)")
def deactivate_person(person_id: int, db: DBSession, _: AdminUser):
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")
    p.is_active = False
    db.commit()
    return R(data={"id": person_id, "is_active": False})
