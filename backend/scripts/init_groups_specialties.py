"""初始化 Group/Specialty 双体系:
1. 创建新表(如不存在)
2. 从现有 StaffAssignment 数据迁移分组信息
3. 创建标准专项(咨询接待/投诉接待/套餐专项/退款挽回)
4. 迁移人员分组归属和专项分配
5. 设置组长/副主管/专项负责人
6. 同步 Person 镜像字段
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import SessionLocal, engine, Base
from app.models import (
    Person, Group, GroupLeadershipHistory, PersonGroupMembership,
    Specialty, SpecialtyLeadershipHistory, PersonSpecialtyAssignment,
    StaffAssignment, STANDARD_SPECIALTIES,
)


def init_tables():
    """创建新表"""
    print("=== 创建新表 ===")
    Base.metadata.create_all(bind=engine, tables=[
        Group.__table__,
        GroupLeadershipHistory.__table__,
        PersonGroupMembership.__table__,
        Specialty.__table__,
        SpecialtyLeadershipHistory.__table__,
        PersonSpecialtyAssignment.__table__,
    ])
    # Person 表新增列 (current_group_id, primary_specialty) - 由SQLAlchemy CREATE TABLE不会自动ADD COLUMN,
    # 用 ALTER TABLE 简化处理(开发环境)
    from sqlalchemy import text
    with engine.connect() as conn:
        # 添加 current_group_id 列 (如果不存在)
        try:
            conn.execute(text("ALTER TABLE persons ADD COLUMN current_group_id BIGINT"))
            print("  已添加 persons.current_group_id 列")
        except Exception:
            pass  # 列已存在
        try:
            conn.execute(text("ALTER TABLE persons ADD COLUMN primary_specialty VARCHAR(64)"))
            print("  已添加 persons.primary_specialty 列")
        except Exception:
            pass  # 列已存在
        conn.commit()
    print()


def create_standard_specialties(db):
    """创建标准专项"""
    print("=== 创建标准专项 ===")
    spec_map = {}
    for code, info in STANDARD_SPECIALTIES.items():
        existing = db.query(Specialty).filter(Specialty.name == info["name"]).first()
        if existing:
            spec_map[code] = existing
            print(f"  已存在: {info['name']} (难度系数{info['difficulty_weight']})")
            continue
        s = Specialty(
            code=code,
            name=info["name"],
            description=info["description"],
            difficulty_weight=info["difficulty_weight"],
            is_temporary=info["is_temporary"],
            is_active=True,
            display_order=list(STANDARD_SPECIALTIES.keys()).index(code) + 1,
        )
        db.add(s)
        db.flush()
        spec_map[code] = s
        print(f"  创建: {info['name']} (难度系数{info['difficulty_weight']})")
    db.commit()
    print()
    return spec_map


def get_or_create_group(db, name, department="企微"):
    """获取或创建分组"""
    g = db.query(Group).filter(Group.name == name).first()
    if g:
        return g, False
    g = Group(name=name, department=department, is_active=True)
    db.add(g)
    db.flush()
    return g, True


def migrate_groups_and_members(db, spec_map):
    """从现有数据迁移分组和人员归属"""
    print("=== 迁移行政分组 ===")
    
    # 从当前Person记录中提取所有分组
    from sqlalchemy import select
    groups_data = {}
    
    # 先收集所有分组名称和组长信息
    persons = db.scalars(select(Person).where(Person.department == "企微", Person.is_active == True)).all()
    
    for p in persons:
        gname = p.group_name
        if not gname:
            continue
        
        if gname not in groups_data:
            groups_data[gname] = {"leader": None, "deputy": None, "members": []}
        
        # 判断是否组长/主管
        if p.position in ["组长", "主管"]:
            groups_data[gname]["leader"] = p
        elif p.position == "副主管":
            groups_data[gname]["deputy"] = p
        else:
            groups_data[gname]["members"].append(p)
    
    # 张蕾组特殊处理: 张蕾是主管
    if "张蕾组" in groups_data:
        zhanglei = db.scalar(select(Person).where(Person.display_name == "张蕾"))
        if zhanglei:
            groups_data["张蕾组"]["leader"] = zhanglei
    
    group_objs = {}
    for gname, data in groups_data.items():
        g, created = get_or_create_group(db, gname)
        group_objs[gname] = g
        leader = data["leader"]
        deputy = data["deputy"]
        
        if created:
            print(f"  创建分组: {gname}")
        
        # 设置组长
        if leader and g.current_leader_id != leader.id:
            g.current_leader_id = leader.id
            # 创建组长历史记录
            hist = GroupLeadershipHistory(
                group_id=g.id,
                person_id=leader.id,
                role="leader",
                effective_from=date(2026, 7, 1),
                is_current=True,
                source="migration",
                change_reason="初始化迁移"
            )
            db.add(hist)
            print(f"    组长: {leader.display_name}")
        
        # 设置副主管
        if deputy and g.current_deputy_leader_id != deputy.id:
            g.current_deputy_leader_id = deputy.id
            hist = GroupLeadershipHistory(
                group_id=g.id,
                person_id=deputy.id,
                role="deputy",
                effective_from=date(2026, 7, 1),
                is_current=True,
                source="migration",
                change_reason="初始化迁移"
            )
            db.add(hist)
            print(f"    副主管: {deputy.display_name}")
    
    db.flush()
    print()
    
    # 迁移人员分组归属
    print("=== 迁移人员分组归属 ===")
    member_count = 0
    for p in persons:
        gname = p.group_name
        if not gname or gname not in group_objs:
            continue
        
        g = group_objs[gname]
        
        # 检查是否已有归属记录
        existing = db.query(PersonGroupMembership).filter(
            PersonGroupMembership.person_id == p.id,
            PersonGroupMembership.is_current == True
        ).first()
        
        if existing:
            if existing.group_id != g.id:
                existing.is_current = False
                existing.effective_to = date(2026, 6, 30)
            else:
                # 同步current_group_id
                p.current_group_id = g.id
                continue
        
        # 创建新归属
        membership = PersonGroupMembership(
            person_id=p.id,
            group_id=g.id,
            effective_from=date(2026, 7, 1),
            is_current=True,
            source="migration",
            change_reason="初始化迁移"
        )
        db.add(membership)
        
        # 同步 Person 镜像
        p.current_group_id = g.id
        p.group_name = gname
        member_count += 1
    
    db.commit()
    print(f"  迁移 {member_count} 人分组归属")
    print()
    
    return group_objs


def migrate_specialty_assignments(db, spec_map, group_objs):
    """迁移人员专项分配"""
    print("=== 迁移人员专项分配 ===")
    
    # 专项名称到标准名称映射
    spec_name_map = {
        "咨询接待": "consulting",
        "接待": "consulting",
        "投诉接待": "complaint",
        "套餐专项": "package",
        "套餐问题": "package",
        "退款挽回": "refund_recovery",
        "退款专员": "refund_recovery",
    }
    
    # 按组设置专项负责人
    # 董梦玲组/叶珍凤组/朱海洋组/吴晓龙组 → 各组长负责咨询接待(主)
    # 张蕾组(张蕾主管) → 洪佳/苏紫依等负责投诉接待,唐彩慧负责退款挽回
    # 朱海洋组 → 退款+套餐负责人
    specialty_leaders = {
        "complaint": "张蕾",  # 张蕾主管投诉
        "refund_recovery": "唐彩慧",  # 唐彩慧负责退款挽回
        "package": "朱海洋",  # 朱海洋兼管套餐
        "consulting": "董梦玲",  # 董梦玲作为咨询接待负责人代表
    }
    
    # 设置专项负责人
    for code, leader_name in specialty_leaders.items():
        if code not in spec_map:
            continue
        s = spec_map[code]
        leader = db.query(Person).filter(Person.display_name == leader_name).first()
        if leader and s.current_lead_id != leader.id:
            s.current_lead_id = leader.id
            hist = SpecialtyLeadershipHistory(
                specialty_id=s.id,
                person_id=leader.id,
                role="lead",
                effective_from=date(2026, 7, 1),
                is_current=True,
                source="migration",
                change_reason="初始化迁移"
            )
            db.add(hist)
    
    db.flush()
    
    # 迁移人员专项分配
    from sqlalchemy import select
    persons = db.scalars(select(Person).where(Person.department == "企微", Person.is_active == True)).all()
    
    assign_count = 0
    for p in persons:
        # 跳过组长/主管(他们的专项根据负责业务定,但主岗位是管理)
        spec_name = p.specialty
        if not spec_name:
            # 组长默认分配到咨询接待
            if p.position in ["组长", "副主管", "主管"]:
                spec_name = "咨询接待"
            else:
                continue
        
        # 映射到标准专项
        spec_code = None
        for name, code in spec_name_map.items():
            if name in spec_name:
                spec_code = code
                break
        
        if not spec_code or spec_code not in spec_map:
            continue
        
        s = spec_map[spec_code]
        
        # 检查是否已有当前分配
        existing = db.query(PersonSpecialtyAssignment).filter(
            PersonSpecialtyAssignment.person_id == p.id,
            PersonSpecialtyAssignment.specialty_id == s.id,
            PersonSpecialtyAssignment.is_current == True
        ).first()
        
        if existing:
            continue
        
        # 创建分配
        is_primary = True
        assign = PersonSpecialtyAssignment(
            person_id=p.id,
            specialty_id=s.id,
            is_primary=is_primary,
            effective_from=date(2026, 7, 1),
            is_current=True,
            workload_ratio=1.0,
            source="migration",
            change_reason="初始化迁移"
        )
        db.add(assign)
        
        # 同步 Person 镜像(主专项)
        if is_primary:
            p.primary_specialty = s.name
            p.specialty = s.name
        
        assign_count += 1
    
    db.commit()
    print(f"  迁移 {assign_count} 人专项分配")
    print()


def main():
    print("=" * 60)
    print("Group/Specialty 双体系初始化迁移")
    print("=" * 60)
    print()
    
    # 1. 创建表
    init_tables()
    
    db = SessionLocal()
    try:
        # 2. 创建标准专项
        spec_map = create_standard_specialties(db)
        
        # 3. 迁移分组
        group_objs = migrate_groups_and_members(db, spec_map)
        
        # 4. 迁移专项分配
        migrate_specialty_assignments(db, spec_map, group_objs)
        
        print("=" * 60)
        print("初始化完成!")
        print()
        print("架构说明:")
        print("  行政分组 (Group):")
        for gname, g in sorted(group_objs.items()):
            leader_name = g.current_leader.display_name if g.current_leader else "-"
            deputy_name = g.current_deputy_leader.display_name if g.current_deputy_leader else "-"
            print(f"    - {gname}: 组长={leader_name}, 副主管={deputy_name}")
        print()
        print("  业务专项 (Specialty):")
        for code, s in spec_map.items():
            lead_name = s.current_lead.display_name if s.current_lead else "-"
            print(f"    - {s.name} (难度系数{s.difficulty_weight}): 负责人={lead_name}")
        print()
        print("  兼容说明:")
        print("    - persons.group_name / position / specialty 镜像字段保持同步")
        print("    - 现有代码无需修改即可继续工作")
        print("    - 后续可在新表基础上实现组长轮岗、专项负责人轮岗、临时专项等功能")
        print("=" * 60)
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
