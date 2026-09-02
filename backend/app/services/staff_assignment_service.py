"""人员任职历史服务 - SCD Type 2 区间维护 + 当前镜像同步

核心不变式(由部分唯一索引 uq_staff_assignments_current_per_person 保证):
- 每人至多一条 is_current=true 的任职记录

写入入口收口:所有岗位/专项/分组变更经本服务,自动同步 Person.position/specialty/group_name 镜像,
避免双写不一致(不开放 PersonUpdate 直接改这三项)。

db 始终第一参数,遵循项目 _execute_upsert 规则。
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.staff_assignment import StaffAssignment
from app.schemas.staff_assignment import StaffAssignmentCreate, StaffAssignmentUpdate


def list_assignments(db: Session, person_id: int) -> list[StaffAssignment]:
    """按生效日期倒序返回某人的任职历史"""
    return list(
        db.scalars(
            select(StaffAssignment)
            .where(StaffAssignment.person_id == person_id)
            .order_by(StaffAssignment.effective_from.desc())
        )
    )


def create_assignment(
    db: Session,
    person_id: int,
    payload: StaffAssignmentCreate,
    result=None,
) -> StaffAssignment:
    """新增任职记录(幂等 + 自动关闭旧 current)

    幂等规则:
    - 同 (person_id, effective_from) 的记录已存在:
      - 内容一致 → 跳过(无变化)
      - 内容不同 → UPDATE 该行(不重复插入)
    - 否则:
      - 若新记录开区间(effective_to=None,即当前任职)→ 关旧 current(effective_to=new_from-1, is_current=false)
      - 插新 current(is_current=true)
    """
    person = db.get(Person, person_id)
    if not person:
        raise ValueError("人员不存在")

    new_from = payload.effective_from
    is_open_current = payload.effective_to is None  # 开区间=当前任职

    # 1. 幂等:同 effective_from 已存在 → 更新内容
    same_from = db.scalar(
        select(StaffAssignment).where(
            StaffAssignment.person_id == person_id,
            StaffAssignment.effective_from == new_from,
        )
    )
    if same_from is not None:
        changed = False
        for fld in ("position", "specialty", "group_name", "change_reason"):
            new_val = getattr(payload, fld)
            if new_val is not None and getattr(same_from, fld) != new_val:
                setattr(same_from, fld, new_val)
                changed = True
        if payload.effective_to is not None and same_from.effective_to != payload.effective_to:
            same_from.effective_to = payload.effective_to
            changed = True
        if changed:
            if same_from.is_current:
                _apply_current_to_person(person, same_from)
            db.flush()
            if result is not None:
                result.updated += 1
        # 无变化则跳过(不计 insert/update);total 由调用方(staff_import)按行计
        return same_from

    # 2. 新增
    if is_open_current:
        # 关闭旧 current
        current = db.scalar(
            select(StaffAssignment).where(
                StaffAssignment.person_id == person_id,
                StaffAssignment.is_current.is_(True),
            )
        )
        if current is not None:
            if new_from <= current.effective_from:
                raise ValueError(
                    f"新增任职生效日期 {new_from} 不晚于当前任职起始 {current.effective_from},无法插入"
                )
            current.effective_to = new_from - timedelta(days=1)
            current.is_current = False
        is_current = True
    else:
        # 历史封闭区间补录 — 不动 current(用户负责避免区间重叠)
        is_current = False

    new_assignment = StaffAssignment(
        person_id=person_id,
        effective_from=new_from,
        effective_to=payload.effective_to,
        is_current=is_current,
        position=payload.position,
        specialty=payload.specialty,
        group_name=payload.group_name,
        change_reason=payload.change_reason,
        source=payload.source,
    )
    db.add(new_assignment)
    db.flush()
    if is_current:
        _apply_current_to_person(person, new_assignment)
    db.flush()
    if result is not None:
        result.inserted += 1
    return new_assignment


def update_assignment(
    db: Session,
    person_id: int,
    assignment_id: int,
    payload: StaffAssignmentUpdate,
) -> StaffAssignment:
    """编辑任职记录非键字段;改 current 的 position/specialty/group_name 会同步镜像
    
    2026-08-14 修正: 不再仅依赖 is_current 标志，改为按 effective_from 判断最近一条任职。
    避免因 is_current 标志异常导致镜像不同步，使运营看板展示过期专项。
    """
    a = db.scalar(
        select(StaffAssignment).where(
            StaffAssignment.id == assignment_id,
            StaffAssignment.person_id == person_id,
        )
    )
    if a is None:
        raise ValueError("任职记录不存在")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(a, k, v)

    # 判断是否为最近一条任职 (按 effective_from 倒序)
    most_recent = db.scalar(
        select(StaffAssignment)
        .where(StaffAssignment.person_id == person_id)
        .order_by(StaffAssignment.effective_from.desc())
        .limit(1)
    )
    should_sync = (most_recent is not None and most_recent.id == a.id) or a.is_current

    if should_sync:
        person = db.get(Person, person_id)
        _apply_current_to_person(person, a)
    db.flush()
    return a


def delete_assignment(db: Session, person_id: int, assignment_id: int) -> None:
    """删除任职记录;若删的是 current,提升 effective_from 最大前一条为 current + 镜像"""
    a = db.scalar(
        select(StaffAssignment).where(
            StaffAssignment.id == assignment_id,
            StaffAssignment.person_id == person_id,
        )
    )
    if a is None:
        raise ValueError("任职记录不存在")
    was_current = a.is_current
    db.delete(a)
    db.flush()
    if was_current:
        person = db.get(Person, person_id)
        # 提升剩余记录中 effective_from 最大者为 current
        prev = db.scalar(
            select(StaffAssignment)
            .where(StaffAssignment.person_id == person_id)
            .order_by(StaffAssignment.effective_from.desc())
            .limit(1)
        )
        if prev is not None:
            prev.is_current = True
            prev.effective_to = None
            _apply_current_to_person(person, prev)
        else:
            # 无任何任职记录 → 清空镜像
            person.position = None
            person.specialty = None
            person.group_name = None
        db.flush()


def get_assignment_at(db: Session, person_id: int, target_date: date) -> StaffAssignment | None:
    """点查询:返回某人在 target_date 当天有效的任职记录(无命中返回 None)

    供后续分析引擎"虚拟人员维度 join"使用。
    """
    return db.scalar(
        select(StaffAssignment)
        .where(
            StaffAssignment.person_id == person_id,
            StaffAssignment.effective_from <= target_date,
            (StaffAssignment.effective_to.is_(None)) | (StaffAssignment.effective_to >= target_date),
        )
        .order_by(StaffAssignment.effective_from.desc())
        .limit(1)
    )


def _apply_current_to_person(person: Person, assignment: StaffAssignment) -> None:
    """把当前任职的 position/specialty/group_name 镜像到 Person(ORM 层同步)"""
    person.position = assignment.position
    person.specialty = assignment.specialty
    person.group_name = assignment.group_name


def recalculate_employment_status(person: Person) -> str:
    """按生命周期日期推导在职状态并写回 employment_status

    优先级:resignation_date → resigned;regularization_date → regular;
    hire_date → probation(已入职未转正);否则 active(无生命周期数据/未入职)。
    """
    if person.resignation_date is not None:
        person.employment_status = "resigned"
    elif person.regularization_date is not None:
        person.employment_status = "regular"
    elif person.hire_date is not None:
        person.employment_status = "probation"
    else:
        person.employment_status = "active"
    return person.employment_status
