"""行政分组 API - 分组列表/详情/负责人变更等"""

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.group import Group, GroupLeadershipHistory, PersonGroupMembership
from app.models.person import Person
from app.schemas.common import R

router = APIRouter(prefix="/groups", tags=["系统-行政分组"])


@router.get("", response_model=R, summary="分组列表(含组长/人数)")
def list_groups(
    db: DBSession,
    _: CurrentUser,
    department: str | None = Query(None, description="按部门过滤"),
    is_active: bool | None = Query(True, description="是否只返回启用的分组"),
):
    stmt = select(Group)
    if department:
        stmt = stmt.where(Group.department == department)
    if is_active is not None:
        stmt = stmt.where(Group.is_active == is_active)
    stmt = stmt.order_by(Group.display_order, Group.name)
    groups = list(db.scalars(stmt))

    result = []
    for g in groups:
        # 统计当前成员数
        member_count = db.scalar(
            select(func.count(Person.id))
            .join(PersonGroupMembership, PersonGroupMembership.person_id == Person.id)
            .where(
                PersonGroupMembership.group_id == g.id,
                PersonGroupMembership.is_current is True,
                Person.is_active is True,
            )
        ) or 0

        result.append({
            "id": g.id,
            "name": g.name,
            "department": g.department,
            "description": g.description,
            "is_active": g.is_active,
            "display_order": g.display_order,
            "current_leader": {
                "id": g.current_leader.id,
                "display_name": g.current_leader.display_name,
            } if g.current_leader else None,
            "current_deputy_leader": {
                "id": g.current_deputy_leader.id,
                "display_name": g.current_deputy_leader.display_name,
            } if g.current_deputy_leader else None,
            "member_count": member_count,
            "created_at": g.created_at,
        })
    return R(data=result)


@router.get("/{group_id}", response_model=R, summary="分组详情(含成员列表)")
def get_group(
    group_id: int,
    db: DBSession,
    _: CurrentUser,
):
    g = db.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="分组不存在")

    # 查询当前成员
    members = list(db.scalars(
        select(Person)
        .join(PersonGroupMembership, PersonGroupMembership.person_id == Person.id)
        .where(
            PersonGroupMembership.group_id == g.id,
            PersonGroupMembership.is_current is True,
        )
        .order_by(Person.display_name)
    ))

    # 查询组长历史
    leadership_history = list(db.scalars(
        select(GroupLeadershipHistory)
        .where(GroupLeadershipHistory.group_id == g.id)
        .order_by(GroupLeadershipHistory.effective_from.desc())
    ))

    return R(data={
        "id": g.id,
        "name": g.name,
        "department": g.department,
        "description": g.description,
        "is_active": g.is_active,
        "current_leader": {
            "id": g.current_leader.id,
            "display_name": g.current_leader.display_name,
        } if g.current_leader else None,
        "current_deputy_leader": {
            "id": g.current_deputy_leader.id,
            "display_name": g.current_deputy_leader.display_name,
        } if g.current_deputy_leader else None,
        "members": [
            {"id": m.id, "display_name": m.display_name, "position": m.position, "employment_status": m.employment_status}
            for m in members
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


@router.post("/{group_id}/transfer-leadership", response_model=R, summary="组长/副主管轮岗")
def transfer_leadership(
    group_id: int,
    db: DBSession,
    _: AdminUser,
    person_id: int = Query(..., description="新负责人ID"),
    role: str = Query("leader", description="角色: leader=组长, deputy=副主管"),
    effective_from: date | None = Query(None, description="生效日期,默认今天"),
    change_reason: str | None = Query(None, description="变更原因"),
):
    g = db.get(Group, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="分组不存在")
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")

    if role not in ("leader", "deputy"):
        raise HTTPException(status_code=400, detail="role必须是leader或deputy")

    effective_date = effective_from or date.today()

    # 结束当前任职
    if role == "leader" and g.current_leader_id:
        current_hist = db.scalar(
            select(GroupLeadershipHistory).where(
                GroupLeadershipHistory.group_id == g.id,
                GroupLeadershipHistory.role == "leader",
                GroupLeadershipHistory.is_current is True,
            )
        )
        if current_hist:
            current_hist.is_current = False
            current_hist.effective_to = effective_date
    elif role == "deputy" and g.current_deputy_leader_id:
        current_hist = db.scalar(
            select(GroupLeadershipHistory).where(
                GroupLeadershipHistory.group_id == g.id,
                GroupLeadershipHistory.role == "deputy",
                GroupLeadershipHistory.is_current is True,
            )
        )
        if current_hist:
            current_hist.is_current = False
            current_hist.effective_to = effective_date

    # 创建新任职历史
    new_hist = GroupLeadershipHistory(
        group_id=g.id,
        person_id=p.id,
        role=role,
        effective_from=effective_date,
        is_current=True,
        change_reason=change_reason or "轮岗",
        source="manual",
    )
    db.add(new_hist)

    # 更新镜像字段
    if role == "leader":
        g.current_leader_id = p.id
    else:
        g.current_deputy_leader_id = p.id

    # 同步到人员分组归属(新组长自动入组)
    existing_membership = db.scalar(
        select(PersonGroupMembership).where(
            PersonGroupMembership.person_id == p.id,
            PersonGroupMembership.is_current is True,
        )
    )
    if existing_membership and existing_membership.group_id != g.id:
        # 从原分组转出
        existing_membership.is_current = False
        existing_membership.effective_to = effective_date
        existing_membership.change_reason = f"调任{role}"
    if not existing_membership or existing_membership.group_id != g.id:
        new_membership = PersonGroupMembership(
            person_id=p.id,
            group_id=g.id,
            effective_from=effective_date,
            is_current=True,
            change_reason=f"调任{role}",
            source="manual",
        )
        db.add(new_membership)
        p.current_group_id = g.id
        p.group_name = g.name

    db.commit()
    return R(data={"ok": True, "group": g.name, "new_leader": p.display_name, "role": role})
