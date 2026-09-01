# -*- coding: utf-8 -*-
"""人员任职历史 SCD Type 2 服务逻辑测试。

对应架构文档: docs/arch/A-20260901-002-staff-assignment-scd2-service-test.md (LOCKED v1.0)
核心不变式:
- 每人至多一条 is_current=true(部分唯一索引保证)
- 新增开区间任职自动关闭旧 current(effective_to = new_from - 1天)
- 删除 current 时提升 effective_from 最大前一条
- get_assignment_at 按日期返回当时有效任职
"""

from datetime import date

import pytest

from app.models.person import Person
from app.schemas.staff_assignment import StaffAssignmentCreate
from app.services import staff_assignment_service as svc


def _make_person(db, name: str = "测试客服") -> Person:
    """构造 Person(display_name 必填), 用事务 fixture 保证用例隔离。"""
    p = Person(display_name=name)
    db.add(p)
    db.flush()
    return p


def _create(db, person_id: int, **kw) -> None:
    """包装 create_assignment, 自动 flush。"""
    payload = StaffAssignmentCreate(**kw)
    svc.create_assignment(db, person_id, payload)
    db.flush()


def test_first_open_assignment_is_current(db):
    """正向: 首条开区间任职 → is_current=true, 且镜像到 Person。"""
    p = _make_person(db)
    _create(db, p.id, effective_from=date(2026, 1, 1),
            position="客服", specialty="接待", group_name="A组")

    p = db.get(Person, p.id)
    assert p.position == "客服"
    assert p.specialty == "接待"
    assert p.group_name == "A组"

    ass = svc.list_assignments(db, p.id)
    assert len(ass) == 1
    assert ass[0].is_current is True
    assert ass[0].effective_to is None


def test_new_open_assignment_closes_old_current(db):
    """正向: 第二条开区间任职 → 旧 current 关闭(effective_to=new_from-1天), 新 current=true。"""
    p = _make_person(db)
    _create(db, p.id, effective_from=date(2026, 1, 1), position="客服")
    _create(db, p.id, effective_from=date(2026, 6, 1), position="组长")

    ass = svc.list_assignments(db, p.id)
    assert len(ass) == 2
    old, new = (ass[1], ass[0])  # 按 effective_from 倒序
    assert new.is_current is True
    assert new.effective_to is None
    assert old.is_current is False
    # 旧 current 关闭: effective_to = new_from - 1天
    assert old.effective_to == date(2026, 5, 31)


def test_closed_history_record_does_not_touch_current(db):
    """边界: 补录历史封闭区间 → 不动 current, is_current=false。"""
    p = _make_person(db)
    _create(db, p.id, effective_from=date(2026, 6, 1), position="组长")
    # 补录更早的封闭区间(2025 年)
    _create(db, p.id, effective_from=date(2025, 1, 1),
            effective_to=date(2025, 12, 31), position="客服")

    ass = svc.list_assignments(db, p.id)
    assert len(ass) == 2
    history = [a for a in ass if not a.is_current]
    current = [a for a in ass if a.is_current]
    assert len(history) == 1
    assert history[0].effective_to == date(2025, 12, 31)
    # current 未被触碰
    assert len(current) == 1
    assert current[0].effective_from == date(2026, 6, 1)
    assert current[0].effective_to is None


def test_new_assignment_before_current_raises(db):
    """边界: 新增开区间任职日期早于当前 → 抛 ValueError。"""
    p = _make_person(db)
    _create(db, p.id, effective_from=date(2026, 6, 1), position="组长")
    with pytest.raises(ValueError):
        _create(db, p.id, effective_from=date(2026, 1, 1), position="客服")


def test_delete_current_promotes_previous(db):
    """边界: 删除 current → 前一条(effective_from 最大)提升为 current + 镜像。"""
    p = _make_person(db)
    _create(db, p.id, effective_from=date(2026, 1, 1), position="客服")
    _create(db, p.id, effective_from=date(2026, 6, 1), position="组长")

    ass = svc.list_assignments(db, p.id)
    current = next(a for a in ass if a.is_current)
    svc.delete_assignment(db, p.id, current.id)
    db.flush()

    remaining = svc.list_assignments(db, p.id)
    assert len(remaining) == 1
    promoted = remaining[0]
    assert promoted.is_current is True
    assert promoted.effective_to is None  # 提升后重新开区间
    assert promoted.position == "客服"

    p = db.get(Person, p.id)
    assert p.position == "客服"  # 镜像回滚到上一任


def test_delete_only_current_clears_person_mirror(db):
    """边界: 删除唯一 current → 清空 Person 镜像。"""
    p = _make_person(db)
    _create(db, p.id, effective_from=date(2026, 1, 1), position="客服")

    ass = svc.list_assignments(db, p.id)
    svc.delete_assignment(db, p.id, ass[0].id)
    db.flush()

    assert svc.list_assignments(db, p.id) == []
    p = db.get(Person, p.id)
    assert p.position is None
    assert p.specialty is None
    assert p.group_name is None


def test_get_assignment_at_hits_inside_range(db):
    """正向: get_assignment_at 命中生效区间内的日期。"""
    p = _make_person(db)
    _create(db, p.id, effective_from=date(2026, 1, 1),
            effective_to=date(2026, 6, 30), position="客服")
    _create(db, p.id, effective_from=date(2026, 7, 1), position="组长")

    # 区间内(封闭区间 2026-01-01 ~ 2026-06-30)
    hit = svc.get_assignment_at(db, p.id, date(2026, 3, 15))
    assert hit is not None
    assert hit.position == "客服"
    # 开区间内(当前任职)
    hit2 = svc.get_assignment_at(db, p.id, date(2026, 8, 1))
    assert hit2 is not None
    assert hit2.position == "组长"


def test_get_assignment_at_misses_outside_range(db):
    """边界: get_assignment_at 在封闭区间结束日之后、开区间开始前 → None。"""
    p = _make_person(db)
    _create(db, p.id, effective_from=date(2026, 1, 1),
            effective_to=date(2026, 6, 30), position="客服")
    _create(db, p.id, effective_from=date(2026, 7, 1), position="组长")

    # 早于最早任职
    assert svc.get_assignment_at(db, p.id, date(2025, 12, 31)) is None
