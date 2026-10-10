"""业务专项 API - 专项列表/详情/负责人变更/临时专项等"""

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.person import Person
from app.models.specialty import PersonSpecialtyAssignment, Specialty, SpecialtyLeadershipHistory
from app.schemas.common import R

router = APIRouter(prefix="/specialties", tags=["系统-业务专项"])


@router.get("", response_model=R, summary="专项列表(含负责人/分配人数)")
def list_specialties(
    db: DBSession,
    _: CurrentUser,
    is_active: bool | None = Query(True, description="是否只返回启用的专项"),
    include_temporary: bool | None = Query(True, description="是否包含临时专项"),
):
    stmt = select(Specialty)
    if is_active is not None:
        stmt = stmt.where(Specialty.is_active == is_active)
    if include_temporary is False:
        stmt = stmt.where(Specialty.is_temporary.is_(False))
    stmt = stmt.order_by(Specialty.display_order, Specialty.name)
    specialties = list(db.scalars(stmt))

    result = []
    for s in specialties:
        # 统计当前分配人数
        assign_count = db.scalar(
            select(func.count(Person.id))
            .join(PersonSpecialtyAssignment, PersonSpecialtyAssignment.person_id == Person.id)
            .where(
                PersonSpecialtyAssignment.specialty_id == s.id,
                PersonSpecialtyAssignment.is_current.is_(True),
                Person.is_active.is_(True),
            )
        ) or 0

        result.append({
            "id": s.id,
            "code": s.code,
            "name": s.name,
            "description": s.description,
            "difficulty_weight": s.difficulty_weight,
            "is_temporary": s.is_temporary,
            "is_active": s.is_active,
            "display_order": s.display_order,
            "valid_from": s.valid_from,
            "valid_to": s.valid_to,
            "current_lead": {
                "id": s.current_lead.id,
                "display_name": s.current_lead.display_name,
            } if s.current_lead else None,
            "current_deputy_lead": {
                "id": s.current_deputy_lead.id,
                "display_name": s.current_deputy_lead.display_name,
            } if s.current_deputy_lead else None,
            "assigned_count": assign_count,
            "created_at": s.created_at,
        })
    return R(data=result)


@router.get("/{specialty_id}", response_model=R, summary="专项详情(含分配人员)")
def get_specialty(
    specialty_id: int,
    db: DBSession,
    _: CurrentUser,
):
    s = db.get(Specialty, specialty_id)
    if not s:
        raise HTTPException(status_code=404, detail="专项不存在")

    # 查询当前分配人员
    assignments = list(db.scalars(
        select(PersonSpecialtyAssignment)
        .where(
            PersonSpecialtyAssignment.specialty_id == s.id,
            PersonSpecialtyAssignment.is_current.is_(True),
        )
    ))

    persons_map = {p.id: p for p in db.scalars(select(Person).where(Person.id.in_([a.person_id for a in assignments]))).all()}

    # 查询负责人历史
    leadership_history = list(db.scalars(
        select(SpecialtyLeadershipHistory)
        .where(SpecialtyLeadershipHistory.specialty_id == s.id)
        .order_by(SpecialtyLeadershipHistory.effective_from.desc())
    ))

    return R(data={
        "id": s.id,
        "code": s.code,
        "name": s.name,
        "description": s.description,
        "difficulty_weight": s.difficulty_weight,
        "is_temporary": s.is_temporary,
        "is_active": s.is_active,
        "current_lead": {
            "id": s.current_lead.id,
            "display_name": s.current_lead.display_name,
        } if s.current_lead else None,
        "assigned_persons": [
            {
                "id": persons_map[a.person_id].id if a.person_id in persons_map else None,
                "display_name": persons_map[a.person_id].display_name if a.person_id in persons_map else None,
                "position": persons_map[a.person_id].position if a.person_id in persons_map else None,
                "group_name": persons_map[a.person_id].group_name if a.person_id in persons_map else None,
                "is_primary": a.is_primary,
                "workload_ratio": a.workload_ratio,
                "effective_from": a.effective_from,
            }
            for a in assignments if a.person_id in persons_map
        ],
        "leadership_history": [
            {
                "id": h.id,
                "person_id": h.person_id,
                "person_name": h.person.display_name if h.person else None,
                "role": h.role,
                "effective_from": h.effective_from,
                "effective_to": h.effective_to,
                "is_current": h.is_current,
                "change_reason": h.change_reason,
            }
            for h in leadership_history
        ],
    })


@router.post("", response_model=R, summary="创建专项(支持临时专项)")
def create_specialty(
    db: DBSession,
    _: AdminUser,
    name: str = Query(..., description="专项名称"),
    code: str | None = Query(None, description="专项编码(标准专项用)"),
    description: str | None = Query(None, description="专项描述"),
    difficulty_weight: float = Query(1.0, description="难度系数"),
    is_temporary: bool = Query(False, description="是否临时专项"),
    valid_from: date | None = Query(None, description="生效日期(临时专项)"),
    valid_to: date | None = Query(None, description="结束日期(临时专项)"),
    current_lead_id: int | None = Query(None, description="负责人ID"),
):
    # 检查重名
    existing = db.scalar(select(Specialty).where(Specialty.name == name))
    if existing:
        raise HTTPException(status_code=400, detail=f"专项 {name} 已存在")

    s = Specialty(
        code=code,
        name=name,
        description=description,
        difficulty_weight=difficulty_weight,
        is_temporary=is_temporary,
        is_active=True,
        valid_from=valid_from,
        valid_to=valid_to,
        display_order=99 if is_temporary else 10,
    )

    if current_lead_id:
        lead = db.get(Person, current_lead_id)
        if not lead:
            raise HTTPException(status_code=404, detail="负责人不存在")
        s.current_lead_id = current_lead_id

    db.add(s)
    db.flush()

    # 如果有负责人，创建负责人历史
    if current_lead_id:
        hist = SpecialtyLeadershipHistory(
            specialty_id=s.id,
            person_id=current_lead_id,
            role="lead",
            effective_from=valid_from or date.today(),
            is_current=True,
            change_reason="创建专项" if not is_temporary else "创建临时专项",
            source="manual",
        )
        db.add(hist)

    db.commit()
    db.refresh(s)
    return R(data={"id": s.id, "name": s.name, "is_temporary": s.is_temporary})


@router.post("/{specialty_id}/transfer-lead", response_model=R, summary="专项负责人轮岗")
def transfer_lead(
    specialty_id: int,
    db: DBSession,
    _: AdminUser,
    person_id: int = Query(..., description="新负责人ID"),
    role: str = Query("lead", description="角色: lead=负责人, deputy=副负责人"),
    effective_from: date | None = Query(None, description="生效日期,默认今天"),
    change_reason: str | None = Query(None, description="变更原因"),
):
    s = db.get(Specialty, specialty_id)
    if not s:
        raise HTTPException(status_code=404, detail="专项不存在")
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")

    if role not in ("lead", "deputy"):
        raise HTTPException(status_code=400, detail="role必须是lead或deputy")

    effective_date = effective_from or date.today()

    # 结束当前负责人任职
    if role == "lead" and s.current_lead_id:
        current_hist = db.scalar(
            select(SpecialtyLeadershipHistory).where(
                SpecialtyLeadershipHistory.specialty_id == s.id,
                SpecialtyLeadershipHistory.role == "lead",
                SpecialtyLeadershipHistory.is_current.is_(True),
            )
        )
        if current_hist:
            current_hist.is_current = False
            current_hist.effective_to = effective_date
    elif role == "deputy" and s.current_deputy_lead_id:
        current_hist = db.scalar(
            select(SpecialtyLeadershipHistory).where(
                SpecialtyLeadershipHistory.specialty_id == s.id,
                SpecialtyLeadershipHistory.role == "deputy",
                SpecialtyLeadershipHistory.is_current.is_(True),
            )
        )
        if current_hist:
            current_hist.is_current = False
            current_hist.effective_to = effective_date

    # 创建新负责人历史
    new_hist = SpecialtyLeadershipHistory(
        specialty_id=s.id,
        person_id=p.id,
        role=role,
        effective_from=effective_date,
        is_current=True,
        change_reason=change_reason or "轮岗",
        source="manual",
    )
    db.add(new_hist)

    # 更新镜像字段
    if role == "lead":
        s.current_lead_id = p.id
    else:
        s.current_deputy_lead_id = p.id

    # 自动分配人员到专项
    existing_assign = db.scalar(
        select(PersonSpecialtyAssignment).where(
            PersonSpecialtyAssignment.person_id == p.id,
            PersonSpecialtyAssignment.specialty_id == s.id,
            PersonSpecialtyAssignment.is_current.is_(True),
        )
    )
    if not existing_assign:
        assign = PersonSpecialtyAssignment(
            person_id=p.id,
            specialty_id=s.id,
            is_primary=False,
            effective_from=effective_date,
            is_current=True,
            workload_ratio=0.5,  # 负责人默认50%工作量在该专项
            change_reason=f"调任{role}",
            source="manual",
        )
        db.add(assign)

    db.commit()
    return R(data={"ok": True, "specialty": s.name, "new_lead": p.display_name, "role": role})


@router.post("/{specialty_id}/assign", response_model=R, summary="分配人员到专项")
def assign_persons(
    specialty_id: int,
    db: DBSession,
    _: AdminUser,
    person_ids: list[int] = Query(..., description="人员ID列表"),
    is_primary: bool = Query(False, description="是否设为主专项"),
    workload_ratio: float = Query(1.0, description="工作量配比"),
    effective_from: date | None = Query(None, description="生效日期"),
    change_reason: str | None = Query(None, description="分配原因"),
):
    s = db.get(Specialty, specialty_id)
    if not s:
        raise HTTPException(status_code=404, detail="专项不存在")

    effective_date = effective_from or date.today()
    assigned = 0

    for pid in person_ids:
        p = db.get(Person, pid)
        if not p:
            continue

        # 结束旧的同专项分配
        old = db.scalar(
            select(PersonSpecialtyAssignment).where(
                PersonSpecialtyAssignment.person_id == pid,
                PersonSpecialtyAssignment.specialty_id == s.id,
                PersonSpecialtyAssignment.is_current.is_(True),
            )
        )
        if old:
            old.is_current = False
            old.effective_to = effective_date

        # 如果设为主专项，结束其他主专项
        if is_primary:
            old_primary = db.scalars(
                select(PersonSpecialtyAssignment).where(
                    PersonSpecialtyAssignment.person_id == pid,
                    PersonSpecialtyAssignment.is_primary.is_(True),
                    PersonSpecialtyAssignment.is_current.is_(True),
                    PersonSpecialtyAssignment.specialty_id != s.id,
                )
            ).all()
            for op in old_primary:
                op.is_primary = False

        # 创建新分配
        assign = PersonSpecialtyAssignment(
            person_id=pid,
            specialty_id=s.id,
            is_primary=is_primary,
            effective_from=effective_date,
            is_current=True,
            workload_ratio=workload_ratio,
            change_reason=change_reason or "分配专项",
            source="manual",
        )
        db.add(assign)

        # 同步镜像字段
        if is_primary:
            p.primary_specialty = s.name
            p.specialty = s.name

        assigned += 1

    db.commit()
    return R(data={"assigned": assigned, "specialty": s.name})


@router.post("/{specialty_id}/archive", response_model=R, summary="归档专项(停用)")
def archive_specialty(
    specialty_id: int,
    db: DBSession,
    _: AdminUser,
):
    s = db.get(Specialty, specialty_id)
    if not s:
        raise HTTPException(status_code=404, detail="专项不存在")

    s.is_active = False
    s.valid_to = date.today()

    # 结束所有当前分配
    current_assigns = db.scalars(
        select(PersonSpecialtyAssignment).where(
            PersonSpecialtyAssignment.specialty_id == s.id,
            PersonSpecialtyAssignment.is_current.is_(True),
        )
    ).all()
    for a in current_assigns:
        a.is_current = False
        a.effective_to = date.today()
        a.change_reason = "专项归档"

    db.commit()
    return R(data={"ok": True, "archived": s.name})
