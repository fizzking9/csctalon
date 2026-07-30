"""HR 人力快照导入服务 - xlsx 快照 → SCD2 任职历史 + 生命周期 + 别名归一

匹配优先级(用户强调 alias 是归一关键):
1. 名称1/名称2/客服名 精确查 PersonAlias.alias → 命中即用其 person
2. 姓名 查 Person.display_name:0 命中→新建 Person+注册别名;1 命中→用;≥2 命中→errors 跳过(防错并)

幂等:同份快照重复导入不重复(由 create_assignment 内的 same_from 逻辑保证)。
db 始终第一参数,遵循项目 _execute_upsert 规则。
"""

from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.person import Person, PersonAlias
from app.schemas.staff_assignment import StaffAssignmentCreate
from app.services.person_extractor import _normalize_alias
from app.services.staff_assignment_service import create_assignment, recalculate_employment_status
from app.services.upsert import ImportResult, _to_datetime


def _to_date(val: Any) -> date | None:
    dt = _to_datetime(val)
    return dt.date() if dt else None


def _find_person_by_alias(db: Session, alias_val: str | None) -> Person | None:
    """精确查 PersonAlias.alias,命中返回其 person"""
    if not alias_val:
        return None
    pa = db.scalar(select(PersonAlias).where(PersonAlias.alias == alias_val))
    if pa and pa.person_id:
        return db.get(Person, pa.person_id)
    return None


def _register_alias(db: Session, person_id: int, alias_val: str | None, source_field: str) -> None:
    """注册 HR 提供的别名到指定 person(幂等,安全归一)

    - alias 不存在 → 新建并挂到 person
    - alias 存在且 person_id 为 NULL(待归并) → 认领,挂到 person(纯归一,无错并风险)
    - alias 已挂在本 person → 跳过
    - alias 已挂在别的 person → 跳过(这是归并冲突,交由人工确认,不在此处强并)
    """
    if not alias_val:
        return
    existing = db.scalar(select(PersonAlias).where(PersonAlias.alias == alias_val))
    if existing:
        if existing.person_id is None:
            existing.person_id = person_id
        return
    db.add(
        PersonAlias(
            person_id=person_id,
            alias=alias_val,
            alias_normalized=_normalize_alias(alias_val),
            source_table="staff_snapshot",
            source_field=source_field,
        )
    )


def upsert_staff_snapshot(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """HR 快照导入主入口

    mapping 目标字段(由 data_parser.COLUMN_TEMPLATES["staff"] 建议):
      display_name/alias_kefu/alias_name1/alias_name2/remark/specialty/position/snapshot_date
      可选生命周期:hire_date/probation_end_date/regularization_date/resignation_date
    """
    from app.services.upsert import apply_mapping

    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            display_name = data.get("display_name")
            alias_kefu = data.get("alias_kefu")
            alias_name1 = data.get("alias_name1")
            alias_name2 = data.get("alias_name2")
            specialty = data.get("specialty")
            position = data.get("position")
            remark = data.get("remark")
            snapshot_date = _to_date(data.get("snapshot_date")) or date.today()

            # 可选生命周期列(Excel 以后补列时自动识别)
            hire_date = _to_date(data.get("hire_date"))
            probation_end_date = _to_date(data.get("probation_end_date"))
            regularization_date = _to_date(data.get("regularization_date"))
            resignation_date = _to_date(data.get("resignation_date"))

            # 1. 按别名匹配 Person(名称1 → 名称2 → 客服名)
            person = (
                _find_person_by_alias(db, alias_name1)
                or _find_person_by_alias(db, alias_name2)
                or _find_person_by_alias(db, alias_kefu)
            )

            # 2. 按 display_name 匹配/新建
            if person is None:
                if not display_name:
                    result.add_error(idx, "姓名为空且无别名可匹配,无法建人", data)
                    continue
                matches = list(db.scalars(select(Person).where(Person.display_name == display_name)))
                if len(matches) >= 2:
                    result.add_error(
                        idx,
                        f"姓名 '{display_name}' 匹配到 {len(matches)} 个人员,需人工归并",
                        data,
                    )
                    continue
                if len(matches) == 1:
                    person = matches[0]
                else:
                    # 0 命中 → 新建 Person(HR 是权威来源,导入即确认真人)
                    person = Person(display_name=display_name, is_confirmed=True, role="agent")
                    db.add(person)
                    db.flush()

            # 3. HR 认领即确认:HR 是权威来源,匹配到的已有 person 标记为已确认
            person.is_confirmed = True

            # 4. 注册 HR 提供的别名到已确定 person(无论新建还是匹配已有)
            #    幂等:已存在别名跳过/认领 NULL 别名;挂在他人的别名不抢(归并冲突交人工)
            _register_alias(db, person.id, alias_name1, "名称1")
            _register_alias(db, person.id, alias_name2, "名称2")
            _register_alias(db, person.id, alias_kefu, "客服名")
            db.flush()

            # 5. 同步备注(不覆盖已有)
            if remark and not person.remark:
                person.remark = remark

            # 6. 同步生命周期(不覆盖已有)
            life_changed = False
            for fld, val in (
                ("hire_date", hire_date),
                ("probation_end_date", probation_end_date),
                ("regularization_date", regularization_date),
                ("resignation_date", resignation_date),
            ):
                if val and getattr(person, fld) is None:
                    setattr(person, fld, val)
                    life_changed = True
            if life_changed:
                recalculate_employment_status(person)

            # 7. 创建任职记录(幂等 + 自动关闭旧 current)
            create_assignment(
                db,
                person.id,
                StaffAssignmentCreate(
                    effective_from=snapshot_date,
                    position=position,
                    specialty=specialty,
                    group_name=None,  # HR 快照无分组列
                    source="xlsx_import",
                ),
                result=result,
            )
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}", data)
    db.commit()


def preview_staff_snapshot(df: pd.DataFrame, mapping: dict[str, str], db: Session) -> dict:
    """HR 快照导入预览(dry-run,只读不写库)

    复用 upsert_staff_snapshot 的匹配逻辑,但只计算每行将发生的动作,不写库。
    供 /persons/import-snapshot/preview 端点用,呈现给用户确认后才合并。
    返回 {summary:{total,new,update,skip,conflict}, rows:[...]}
    """
    from app.models.staff_assignment import StaffAssignment
    from app.services.upsert import apply_mapping

    summary = {"total": 0, "new": 0, "update": 0, "skip": 0, "conflict": 0}
    rows: list[dict] = []

    for idx, row in enumerate(df.to_dict("records")):
        data = apply_mapping(row, mapping)
        display_name = data.get("display_name")
        alias_kefu = data.get("alias_kefu")
        alias_name1 = data.get("alias_name1")
        alias_name2 = data.get("alias_name2")
        specialty = data.get("specialty")
        position = data.get("position")
        snapshot_date = _to_date(data.get("snapshot_date")) or date.today()

        # 1. 别名匹配(与 upsert 一致:名称1→名称2→客服名)
        person = (
            _find_person_by_alias(db, alias_name1)
            or _find_person_by_alias(db, alias_name2)
            or _find_person_by_alias(db, alias_kefu)
        )

        action: str | None = None
        changes: dict = {}
        matched_person_id = None
        matched_name = None

        if person is None:
            # 2. display_name 匹配
            if not display_name:
                action = "conflict"
                changes["reason"] = "姓名为空且无别名可匹配,无法建人"
            else:
                matches = list(db.scalars(select(Person).where(Person.display_name == display_name)))
                if len(matches) >= 2:
                    action = "conflict"
                    changes["reason"] = f"姓名 '{display_name}' 命中 {len(matches)} 人,需人工归并"
                    changes["matched_ids"] = [m.id for m in matches]
                elif len(matches) == 1:
                    person = matches[0]
                else:
                    # 0 命中 → 新建 person + 别名 + 任职 + 标确认
                    action = "new"
                    changes["aliases_to_add"] = [v for v in (alias_name1, alias_name2, alias_kefu) if v]
                    changes["assignment"] = "new"
                    changes["will_confirm"] = True

        if person is not None and action is None:
            # 命中已有 person → 计算 update vs skip
            matched_person_id = person.id
            matched_name = person.display_name
            # 别名将新增:尚不存在于任何 person 的别名
            aliases_to_add = []
            for v in (alias_name1, alias_name2, alias_kefu):
                if not v:
                    continue
                if db.scalar(select(PersonAlias).where(PersonAlias.alias == v)) is None:
                    aliases_to_add.append(v)
            # 任职动作:snapshot_date 是否已有任职
            exist_assign = db.scalar(
                select(StaffAssignment).where(
                    StaffAssignment.person_id == person.id,
                    StaffAssignment.effective_from == snapshot_date,
                )
            )
            if exist_assign is not None:
                same = exist_assign.position == position and exist_assign.specialty == specialty
                assignment_action = "skip" if same else "update"
                if not same:
                    changes["position_change"] = {"old": exist_assign.position, "new": position}
                    changes["specialty_change"] = {"old": exist_assign.specialty, "new": specialty}
            else:
                assignment_action = "new"
                if person.position != position:
                    changes["position_change"] = {"old": person.position, "new": position}
                if person.specialty != specialty:
                    changes["specialty_change"] = {"old": person.specialty, "new": specialty}
            changes["aliases_to_add"] = aliases_to_add
            changes["assignment"] = assignment_action
            changes["will_confirm"] = not person.is_confirmed

            has_change = bool(aliases_to_add) or assignment_action != "skip" or not person.is_confirmed
            action = "update" if has_change else "skip"

        if action is None:
            action = "skip"

        summary[action] = summary.get(action, 0) + 1
        summary["total"] += 1
        rows.append({
            "row_idx": idx,
            "display_name": display_name,
            "matched_person_id": matched_person_id,
            "matched_name": matched_name,
            "action": action,
            "snapshot_date": str(snapshot_date),
            "position": position,
            "specialty": specialty,
            "aliases": [v for v in (alias_name1, alias_name2, alias_kefu) if v],
            "changes": changes,
        })

    return {"summary": summary, "rows": rows}
