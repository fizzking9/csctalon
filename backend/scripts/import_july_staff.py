"""导入7月份客服人员岗位信息 Excel 文件
"""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import SessionLocal
from app.models.person import Person, PersonAlias
from app.models.staff_assignment import StaffAssignment
from app.services.person_extractor import _normalize_alias
from app.schemas.staff_assignment import StaffAssignmentCreate


EXCEL_PATH = Path(__file__).parent.parent.parent / "01样本数据" / "客服人员7月份岗位信息.xlsx"
DEPARTMENT = "企微"
EFFECTIVE_FROM = date(2026, 7, 1)
JULY_END = date(2026, 7, 31)
RESIGNATION_DATE = date(2026, 8, 1)

POSITION_MAP = {
    "组长": "组长",
    "副主管": "副主管",
    "主管": "主管",
    "接待": "客服",
    "退款挽回": "客服",
    "套餐问题": "客服",
    "投诉接待": "客服",
    "退款专员": "客服",
}

SPECIALTY_MAP = {
    "组长": None,
    "副主管": None,
    "主管": None,
    "接待": "咨询接待",
    "退款挽回": "退款挽回",
    "套餐问题": "套餐专项",
    "投诉接待": "投诉接待",
    "退款专员": "退款挽回",
}


def get_or_create_person(db: Session, name: str, position: str | None) -> tuple[Person, bool]:
    """查找或创建人员"""
    # 先按姓名查找
    person = db.scalar(select(Person).where(Person.display_name == name))
    if person:
        return person, False
    
    # 创建新人员
    person = Person(
        display_name=name,
        department=DEPARTMENT,
        is_confirmed=True,
        role="supervisor" if position == "主管" else "agent",
    )
    db.add(person)
    db.flush()
    return person, True


def safe_register_alias(db: Session, person_id: int, alias_val: str, source: str) -> bool:
    """安全注册别名,处理重复"""
    if not alias_val:
        return False
    alias_str = str(alias_val).strip()
    if not alias_str:
        return False
    
    # 先查数据库
    existing = db.scalar(select(PersonAlias).where(PersonAlias.alias == alias_str))
    if existing:
        if existing.person_id is None:
            existing.person_id = person_id
            return True
        return False
    
    # 查session中是否已有待添加的相同别名
    for obj in db.new:
        if isinstance(obj, PersonAlias) and obj.alias == alias_str:
            if obj.person_id is None:
                obj.person_id = person_id
            return False
    
    # 新增别名
    db.add(PersonAlias(
        person_id=person_id,
        alias=alias_str,
        alias_normalized=_normalize_alias(alias_str),
        source_table="july_staff_import",
        source_field=source,
    ))
    return True


def main():
    print("=" * 60)
    print("开始导入7月份客服人员岗位信息")
    print("=" * 60)

    df_position = pd.read_excel(EXCEL_PATH, sheet_name="企微7月份人员岗位及专项")
    df_alias = pd.read_excel(EXCEL_PATH, sheet_name="企微客服人员以及别名", header=1)

    # 构建别名字典
    alias_dict: dict[str, list[str]] = {}
    alias_cols = ["姓名一", "姓名二", "姓名三", "姓名四", "姓名五", "姓名六"]
    for _, row in df_alias.iterrows():
        std_name = str(row.get("标准姓名", "")).strip()
        if not std_name or pd.isna(std_name):
            continue
        aliases = []
        seen = set()
        for col in alias_cols:
            val = row.get(col)
            if val and not pd.isna(val):
                a = str(val).strip()
                if a and a not in seen:
                    seen.add(a)
                    aliases.append(a)
        alias_dict[std_name] = aliases

    db = SessionLocal()
    stats = {
        "new_persons": 0,
        "updated_persons": 0,
        "new_aliases": 0,
        "new_assignments": 0,
        "updated_assignments": 0,
        "resigned": 0,
        "errors": [],
    }

    try:
        for idx, row in df_position.iterrows():
            name = str(row.get("姓名", "")).strip()
            group = str(row.get("所属组", "")).strip() if not pd.isna(row.get("所属组")) else None
            status = str(row.get("状态", "")).strip() if not pd.isna(row.get("状态")) else None
            position_raw = str(row.get("岗位", "")).strip() if not pd.isna(row.get("岗位")) else None
            remark = str(row.get("备注", "")).strip() if not pd.isna(row.get("备注")) else None

            if not name:
                stats["errors"].append(f"第{idx+2}行: 姓名为空")
                continue

            position = POSITION_MAP.get(position_raw, position_raw) if position_raw else None
            specialty = SPECIALTY_MAP.get(position_raw)

            try:
                # 1. 获取或创建人员
                person, is_new = get_or_create_person(db, name, position)
                if is_new:
                    stats["new_persons"] += 1
                    action = "新建"
                else:
                    stats["updated_persons"] += 1
                    action = "更新"

                # 更新基本信息
                if not person.department:
                    person.department = DEPARTMENT
                person.is_confirmed = True
                if position == "主管" and person.role != "supervisor":
                    person.role = "supervisor"
                if remark and not person.remark:
                    person.remark = remark

                # 2. 处理离职/转正状态
                if status and "离职" in status:
                    person.resignation_date = RESIGNATION_DATE
                    person.is_active = False
                    person.employment_status = "resigned"
                    stats["resigned"] += 1
                elif status == "转正":
                    if not person.regularization_date:
                        person.regularization_date = EFFECTIVE_FROM
                    if person.employment_status != "resigned":
                        person.employment_status = "regular"
                elif status == "试用":
                    if not person.hire_date:
                        person.hire_date = EFFECTIVE_FROM
                    if person.employment_status not in ("resigned", "regular"):
                        person.employment_status = "probation"

                # 3. 注册别名 (去重)
                all_aliases = [name]  # 标准姓名
                all_aliases.extend(alias_dict.get(name, []))
                seen_aliases = set()
                for a in all_aliases:
                    if a and a not in seen_aliases:
                        seen_aliases.add(a)
                        if safe_register_alias(db, person.id, a, "july_import"):
                            stats["new_aliases"] += 1

                db.flush()

                # 4. 处理任职记录
                # 检查是否有更新的current记录(8月及以后)
                future_current = db.scalar(
                    select(StaffAssignment).where(
                        StaffAssignment.person_id == person.id,
                        StaffAssignment.is_current.is_(True),
                        StaffAssignment.effective_from > EFFECTIVE_FROM,
                    )
                )

                # 确定7月任职记录的参数
                is_resigned = bool(status and "离职" in status)
                effective_to = JULY_END if (is_resigned or future_current is not None) else None
                is_current = not (is_resigned or future_current is not None)

                # 查找7月1日是否已有记录
                existing_july = db.scalar(
                    select(StaffAssignment).where(
                        StaffAssignment.person_id == person.id,
                        StaffAssignment.effective_from == EFFECTIVE_FROM,
                    )
                )

                if existing_july:
                    # 更新现有记录
                    changed = False
                    if existing_july.position != position:
                        existing_july.position = position
                        changed = True
                    if existing_july.specialty != specialty:
                        existing_july.specialty = specialty
                        changed = True
                    if existing_july.group_name != group:
                        existing_july.group_name = group
                        changed = True
                    if existing_july.effective_to != effective_to:
                        existing_july.effective_to = effective_to
                        changed = True
                    if is_current and not existing_july.is_current and future_current is None:
                        existing_july.is_current = True
                        changed = True
                    elif not is_current and existing_july.is_current and future_current is not None:
                        existing_july.is_current = False
                        changed = True
                    
                    if changed:
                        stats["updated_assignments"] += 1
                        if existing_july.is_current:
                            person.position = position
                            person.specialty = specialty
                            person.group_name = group
                else:
                    # 如果当前current记录早于7月1日,且我们要插入新的current,需要先关闭旧的
                    if is_current:
                        old_current = db.scalar(
                            select(StaffAssignment).where(
                                StaffAssignment.person_id == person.id,
                                StaffAssignment.is_current.is_(True),
                                StaffAssignment.effective_from < EFFECTIVE_FROM,
                            )
                        )
                        if old_current:
                            old_current.effective_to = EFFECTIVE_FROM - date.resolution
                            old_current.is_current = False
                    
                    # 创建新的任职记录
                    new_assign = StaffAssignment(
                        person_id=person.id,
                        effective_from=EFFECTIVE_FROM,
                        effective_to=effective_to,
                        is_current=is_current,
                        position=position,
                        specialty=specialty,
                        group_name=group,
                        source="xlsx_import",
                        change_reason="7月份岗位信息导入" + (f" - {remark}" if remark else ""),
                    )
                    db.add(new_assign)
                    
                    if is_current:
                        person.position = position
                        person.specialty = specialty
                        person.group_name = group
                    
                    stats["new_assignments"] += 1

                db.flush()
                db.commit()  # 每人提交一次,避免大事务失败
                
                status_flag = " (8月离职)" if is_resigned else ""
                print(f"  [{action}] {name}: {group} - {position}/{specialty}{status_flag}")

            except Exception as e:
                db.rollback()
                stats["errors"].append(f"{name}: {str(e)}")
                print(f"  [错误] {name}: {e}")
                continue

        print()
        print("=" * 60)
        print("导入完成!")
        print(f"  新建人员: {stats['new_persons']}")
        print(f"  更新人员: {stats['updated_persons']}")
        print(f"  新增别名: {stats['new_aliases']}")
        print(f"  新增任职: {stats['new_assignments']}")
        print(f"  更新任职: {stats['updated_assignments']}")
        print(f"  离职标记: {stats['resigned']}")
        if stats["errors"]:
            print(f"  错误: {len(stats['errors'])}")
            for err in stats["errors"]:
                print(f"    - {err}")
        print("=" * 60)

    finally:
        db.close()


if __name__ == "__main__":
    main()
