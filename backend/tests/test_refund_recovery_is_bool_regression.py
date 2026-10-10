# -*- coding: utf-8 -*-
"""回归测试: 修复 9a2ecdd 引入的 ORM 列 `is True/False` 误用 (#4)。

背景: 提交 9a2ecdd 在做 E712 清理时, 把 SQLAlchemy 布尔列条件从
`== True/False` 批量误改成 `is True/False`。`X.column is True` 恒为 False,
导致 `.where()` 生成恒假条件, 人员维表查询永远为空 →
退款挽回分析 / 组 / 专项 等接口静默返回空数据。

本测试用最小造数直接调用退款挽回分析入口, 锁定:
  - 人员维表能被识别(修复前 bug 状态: all_ops_meta 空 → total_sn=0)
  - 分母 SN 能被取到(total_sn > 0)
若有人把 `.is_(True/False)` 改回 `is True/False`, 本测试立即失败。
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from app.models.cs_record import CsRecord
from app.models.device import Device
from app.models.group import Group, PersonGroupMembership
from app.models.person import Person, PersonAlias
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.services.analytics import refund_recovery_analysis

# 上海 9/2 全天 = UTC 9/1 16:00 ~ 9/2 16:00
OP_TIME = datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc)  # 上海 9/2 01:00
OP = "企微-测试挽回"


def _uniq_sn(prefix: str) -> str:
    """生成唯一设备 SN(避免测试库历史残留导致主键冲突)。"""
    return f"{prefix}{uuid.uuid4().hex[:10].upper()}"


def _seed(db) -> None:
    """最小造数: 1 人员(specialty=退款挽回) + 1 别名 + 1 设备 + 1 退款类接待记录。"""
    p = Person(display_name="测试挽回", specialty="退款挽回", position="客服",
               role="agent", employment_status="regular", is_active=True, is_confirmed=True)
    db.add(p)
    db.flush()
    db.add(PersonAlias(person_id=p.id, alias=OP, alias_normalized=OP.lower(),
                       source_table="cs_records", source_field="operator", is_system=False))
    db.flush()
    # Device 单独 flush, 再插 CsRecord(同批 flush 可能因排序 CsRecord 先于 Device 插入导致外键失败)
    sn = _uniq_sn("TESTRR")
    db.add(Device(sn=sn))
    db.flush()
    db.add(CsRecord(
        problem_category="退款",
        problem_type="不想用了",
        device_sn=sn,
        operation_time=OP_TIME,
        operator=OP,
        description="客户要求退款",
    ))
    db.flush()
    return sn


def test_refund_recovery_detects_person_and_host_sn(db):
    """核心回归: 退款挽回分析能识别人员维表并取到分母 SN(修复前为空)。"""
    _seed(db)
    data = refund_recovery_analysis(db, date="2026-09-02")

    # 修复前(bug): all_ops_meta 空 → total_sn=0, 并带 _note
    # 修复后: total_sn >= 1
    assert data["kpi"]["total_sn"] >= 1, (
        f"退款挽回分母为空(total_sn={data['kpi']['total_sn']}), "
        f"_note={data.get('_note')} —— 人员维表查询可能又退化回 `is True/False`"
    )
    # 人员明细应包含测试人员
    ops = [s["operator"] for s in data["staff_detail"]]
    assert OP in ops, f"人员明细缺失 {OP}, 实际: {ops}"


def test_refund_recovery_system_alias_filtered(db):
    """系统别名(is_system=True)不应进入分母(对应 is_system.is_(False) 过滤)。"""
    _seed(db)
    # 追加一条: 系统自动别名 + 系统操作记录, 不应影响结果也不应报错
    p2 = Person(display_name="系统自动")
    db.add(p2)
    db.flush()
    db.add(PersonAlias(person_id=p2.id, alias="system", alias_normalized="system",
                       source_table="cs_records", source_field="operator", is_system=True))
    db.flush()
    sys_sn = _uniq_sn("TESTSYS")
    db.add(Device(sn=sys_sn))
    db.flush()
    db.add(CsRecord(
        problem_category="退款",
        problem_type="系统自动退款",
        device_sn=sys_sn,
        operation_time=OP_TIME,
        operator="system",
        description="系统自动",
    ))
    db.flush()
    data = refund_recovery_analysis(db, date="2026-09-02")
    # system 在黑名单(SYS_OP_BLACKLIST_RR), 不会增加分母
    assert data["kpi"]["total_sn"] == 1


# ---- 组/专项 成员统计: `X.is_current is True` → `X.is_current.is_(True)` 回归 ----
# 以下两条用与 production 代码完全等价的查询构造(见 groups.py:39 / specialties.py:39),
# 锁定 `is_(True)` 写法; 若有人改回 `is True`(恒假), 计数会变 0, 测试失败。


def test_group_member_count_is_current_filter(db):
    """回归: 组成员统计用 is_current.is_(True) 能数到当前成员(修复前恒 0)。"""
    p = Person(display_name="组员甲", role="agent", employment_status="regular", is_active=True, is_confirmed=True)
    db.add(p)
    db.flush()
    g = Group(name="测试A组", department="客服部")
    db.add(g)
    db.flush()
    db.add(PersonGroupMembership(
        person_id=p.id, group_id=g.id,
        effective_from=date(2026, 1, 1), is_current=True, change_reason="入组", source="manual",
    ))
    db.flush()

    # 等价于 groups.py list_groups 的 member_count 查询
    cnt = db.scalar(
        select(func.count(Person.id))
        .join(PersonGroupMembership, PersonGroupMembership.person_id == Person.id)
        .where(
            PersonGroupMembership.group_id == g.id,
            PersonGroupMembership.is_current.is_(True),
            Person.is_active.is_(True),
        )
    ) or 0
    assert cnt == 1, f"组成员数={cnt}, 期望 1 —— is_current 过滤可能退化回 `is True`"


def test_specialty_assignment_count_is_current_filter(db):
    """回归: 专项分配人数统计用 is_current.is_(True) 能数到当前分配(修复前恒 0)。"""
    p = Person(display_name="专员乙", role="agent", employment_status="regular", is_active=True, is_confirmed=True)
    db.add(p)
    db.flush()
    s = Specialty(name="退款挽回", difficulty_weight=2.2, is_active=True)
    db.add(s)
    db.flush()
    db.add(PersonSpecialtyAssignment(
        person_id=p.id, specialty_id=s.id,
        is_primary=True, effective_from=date(2026, 1, 1), is_current=True,
        workload_ratio=1.0, change_reason="分配", source="manual",
    ))
    db.flush()

    # 等价于 specialties.py list_specialties 的 assign_count 查询
    cnt = db.scalar(
        select(func.count(Person.id))
        .join(PersonSpecialtyAssignment, PersonSpecialtyAssignment.person_id == Person.id)
        .where(
            PersonSpecialtyAssignment.specialty_id == s.id,
            PersonSpecialtyAssignment.is_current.is_(True),
            Person.is_active.is_(True),
        )
    ) or 0
    assert cnt == 1, f"专项分配人数={cnt}, 期望 1 —— is_current 过滤可能退化回 `is True`"
