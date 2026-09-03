"""人员统一管理路由 - 别名提取、归并、拆分"""

from fastapi import APIRouter, HTTPException, Query
from pypinyin import Style, lazy_pinyin
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import AdminOrSupervisor, AdminUser, CurrentUser, DBSession
from app.models.data_source import DataSource
from app.models.group import Group, PersonGroupMembership
from app.models.person import Person, PersonAlias
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.schemas.common import PageBase, R
from app.schemas.person import (
    BatchImportByPrefixRequest,
    BatchMergeRequest,
    BatchUpdateRequest,
    MergeNewRequest,
    MergeRequest,
    PersonAliasOut,
    PersonUpdate,
    SplitRequest,
)
from app.services.audit_timeline import build_person_timeline
from app.services.person_extractor import (
    count_alias_occurrences,
    extract_person_aliases,
    rebuild_unclaimed_aliases,
)

router = APIRouter(prefix="/persons", tags=["系统-人员"])

# source_field 英文名 → 中文标签(数据源字段语义)
FIELD_LABELS: dict[str, str] = {
    "operator": "操作人",
    "auditor": "审核人",
    "handler": "处理人",
    "registrant": "登记人",
    "follower": "跟进人",
}

# 公司缩写前缀(云客猫=YKM),剥后才是人名拼音首字母(YKM_LJF → LJF)
_INITIAL_COMPANY_PREFIXES = ("YKM",)

# 姓氏多音字首字母纠正(pypinyin 默认读音不准,作姓时读音不同)
# 覃(qín 非 tán) / 仇(qiú 非 chóu) / 单(shàn 非 dān) / 区(ōu 非 qū)
# 解(xiè 非 jiě) / 查(zhā 非 chá) / 乐(yuè 非 lè) / 曾(zēng 非 céng)
_SURNAME_INITIAL: dict[str, str] = {
    "覃": "Q", "仇": "Q", "单": "S", "区": "O",
    "解": "X", "查": "Z", "乐": "Y", "曾": "Z",
}


def _name_initials(name: str) -> str:
    """算姓名拼音首字母。第一个字(姓)用多音字纠正表覆盖。"""
    if not name:
        return ""
    first = name[0]
    if first in _SURNAME_INITIAL:
        rest = "".join(py[0].upper() for py in lazy_pinyin(name[1:], style=Style.NORMAL) if py)
        return _SURNAME_INITIAL[first] + rest
    return "".join(py[0].upper() for py in lazy_pinyin(name, style=Style.NORMAL) if py)


def _load_table_labels(db: Session) -> dict[str, str]:
    """从 data_sources 取 table_name → name_cn 中文表名映射。"""
    rows = db.execute(select(DataSource.table_name, DataSource.name_cn)).all()
    return {tn: (cn or tn) for tn, cn in rows if tn}


def _extract_initials(alias: str) -> str | None:
    """从首字母型别名剥公司前缀(YKM),得人名拼音首字母(LJF/HYK)。

    非首字母型(含中文/不全是字母)返回 None。
    """
    s = alias.strip().upper()
    for pfx in _INITIAL_COMPANY_PREFIXES:
        if s.startswith(pfx):
            s = s[len(pfx):].lstrip("-_ ")
            break
    # 必须是纯大写字母(2~6 位)才像人名拼音首字母
    if not (2 <= len(s) <= 6 and s.isalpha() and s.isupper()):
        return None
    return s


def _hr_initial_index(db: Session) -> dict[str, list[dict]]:
    """算所有在职 HR 人员 display_name 的拼音首字母 → [{id,name}] 索引。

    多音字(如覃默认读 tán 而非 qín)可能导致匹配失败,失败项留人工归并。
    """
    persons = list(db.scalars(select(Person).where(Person.is_active.is_(True))))
    idx: dict[str, list[dict]] = {}
    for p in persons:
        ini = _name_initials(p.display_name)
        if ini:
            idx.setdefault(ini, []).append({"id": p.id, "name": p.display_name})
    return idx


def _hr_name_index(db: Session) -> dict[str, list[dict]]:
    """HR display_name(lower) → [{id,name}] 索引,用于 alias_normalized 精确匹配。

    alias_normalized 已剥前缀得裸名(企微-何琴→何琴),若与 HR display_name 唯一相等,
    即可批量归并。比首字母更可靠(精确字符串相等)。
    """
    persons = list(db.scalars(select(Person).where(Person.is_active.is_(True))))
    idx: dict[str, list[dict]] = {}
    for p in persons:
        key = (p.display_name or "").strip().lower()
        if key:
            idx.setdefault(key, []).append({"id": p.id, "name": p.display_name})
    return idx


def _attach_alias_labels(db: Session, aliases: list[PersonAlias]) -> list[PersonAlias]:
    """给别名对象补 source_table_label/source_field_label + 建议归并目标。

    PersonAliasOut.from_attributes 会读取这些属性。
    建议匹配两级(优先级 normalized > initial):
    1. normalized 精确匹配: alias_normalized(剥前缀裸名)与 HR display_name 唯一相等
       → match_source='normalized'(最可靠,如 企微-何琴→何琴)
    2. 首字母匹配: 纯拉丁别名(ZHY/YKM-LJF)剥公司前缀得人名首字母,与 HR 拼音首字母唯一相等
       → match_source='initial'
    唯一匹配 → initial_match_type='unique' + suggested_hr_person_id/name
    多义匹配 → initial_match_type='ambiguous'(留人工)
    无匹配   → initial_match_type=None
    """
    table_map = _load_table_labels(db)
    hr_name_idx = _hr_name_index(db)
    hr_ini_idx = _hr_initial_index(db)
    for a in aliases:
        a.source_table_label = table_map.get(a.source_table, a.source_table) if a.source_table else None
        a.source_field_label = FIELD_LABELS.get(a.source_field, a.source_field) if a.source_field else None
        a.suggested_hr_person_id = None
        a.suggested_hr_person_name = None
        a.initial_match_type = None
        a.match_source = None
        # 1. normalized 精确匹配(优先,最可靠)
        norm = (a.alias_normalized or "").strip().lower()
        matched = hr_name_idx.get(norm, [])
        if len(matched) == 1:
            a.suggested_hr_person_id = matched[0]["id"]
            a.suggested_hr_person_name = matched[0]["name"]
            a.initial_match_type = "unique"
            a.match_source = "normalized"
            continue
        if len(matched) > 1:
            a.initial_match_type = "ambiguous"
            a.match_source = "normalized"
            continue
        # 2. 首字母匹配(纯拉丁别名)
        ini = _extract_initials(a.alias)
        if ini:
            matched = hr_ini_idx.get(ini, [])
            if len(matched) == 1:
                a.suggested_hr_person_id = matched[0]["id"]
                a.suggested_hr_person_name = matched[0]["name"]
                a.initial_match_type = "unique"
                a.match_source = "initial"
            elif len(matched) > 1:
                a.initial_match_type = "ambiguous"
                a.match_source = "initial"
    return aliases


def _with_alias_count(db: Session, p: Person) -> dict:
    cnt = db.scalar(
        select(func.count()).select_from(PersonAlias).where(PersonAlias.person_id == p.id, PersonAlias.is_system.is_(False))
    ) or 0

    # 获取当前分组信息
    current_group = None
    if p.current_group_id:
        g = db.get(Group, p.current_group_id)
        if g:
            current_group = {"id": g.id, "name": g.name}

    # 获取当前所有专项(主+辅)
    current_specialties = []
    assigns = list(db.scalars(
        select(PersonSpecialtyAssignment)
        .where(
            PersonSpecialtyAssignment.person_id == p.id,
            PersonSpecialtyAssignment.is_current.is_(True),
        )
        .order_by(PersonSpecialtyAssignment.is_primary.desc(), PersonSpecialtyAssignment.effective_from.desc())
    ))
    spec_ids = [a.specialty_id for a in assigns]
    if spec_ids:
        specs = {s.id: s for s in db.scalars(select(Specialty).where(Specialty.id.in_(spec_ids))).all()}
        for a in assigns:
            if a.specialty_id in specs:
                current_specialties.append({
                    "id": a.specialty_id,
                    "name": specs[a.specialty_id].name,
                    "code": specs[a.specialty_id].code,
                    "is_primary": a.is_primary,
                    "difficulty_weight": specs[a.specialty_id].difficulty_weight,
                    "workload_ratio": a.workload_ratio,
                })

    out = {
        "id": p.id,
        "display_name": p.display_name,
        "qw_user_id": p.qw_user_id,
        "qw_name": p.qw_name,
        "role": p.role,
        "department": p.department,
        "group_name": p.group_name,
        "position": p.position,
        "specialty": p.specialty,
        "hire_date": p.hire_date,
        "probation_end_date": p.probation_end_date,
        "regularization_date": p.regularization_date,
        "resignation_date": p.resignation_date,
        "employment_status": p.employment_status,
        "is_active": p.is_active,
        "is_confirmed": p.is_confirmed,
        "remark": p.remark,
        "user_id": p.user_id,
        "alias_count": cnt,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "current_group": current_group,
        "current_specialties": current_specialties,
    }
    return out


@router.get("", response_model=R, summary="人员列表(分页)")
def list_persons(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=5000),
    keyword: str | None = Query(None, description="按姓名/部门模糊搜索"),
    status: str | None = Query(None, description="confirmed/unconfirmed/inactive"),
    department: str | None = Query(None, description="按部门过滤"),
    group_id: int | None = Query(None, description="按分组ID过滤"),
    specialty_id: int | None = Query(None, description="按专项ID过滤(包含主/辅专项)"),
    role: str | None = Query(None, description="按角色过滤:agent/supervisor/admin"),
    employment_status: str | None = Query(None, description="按在职状态过滤:active(待入职)/probation(试用期)/regular(正式)/transferred(调岗)/resigned(离职)/inactive(非活跃)"),
    is_confirmed: bool | None = Query(None, description="是否已确认归并"),
    sort_by: str | None = Query(None, description="排序字段:id/display_name/department/role/employment_status/created_at/hire_date"),
    sort_order: str = Query("desc", description="asc/desc"),
):
    stmt = select(Person)
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(Person.display_name.ilike(kw), Person.department.ilike(kw)))
    if status == "confirmed":
        stmt = stmt.where(Person.is_confirmed.is_(True), Person.is_active.is_(True))
    elif status == "unconfirmed":
        stmt = stmt.where(Person.is_confirmed.is_(False), Person.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(Person.is_active.is_(False))
    if department:
        stmt = stmt.where(Person.department == department)
    if group_id:
        # 通过 PersonGroupMembership 关联筛选
        stmt = stmt.join(
            PersonGroupMembership,
            (PersonGroupMembership.person_id == Person.id) &
            (PersonGroupMembership.group_id == group_id) &
            (PersonGroupMembership.is_current.is_(True))
        )
    if specialty_id:
        # 通过 PersonSpecialtyAssignment 关联筛选
        stmt = stmt.join(
            PersonSpecialtyAssignment,
            (PersonSpecialtyAssignment.person_id == Person.id) &
            (PersonSpecialtyAssignment.specialty_id == specialty_id) &
            (PersonSpecialtyAssignment.is_current.is_(True))
        )
    if role:
        stmt = stmt.where(Person.role == role)
    if employment_status:
        stmt = stmt.where(Person.employment_status == employment_status)
    if is_confirmed is not None:
        stmt = stmt.where(Person.is_confirmed.is_(is_confirmed))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    sort_map = {
        "id": Person.id,
        "display_name": Person.display_name,
        "department": Person.department,
        "role": Person.role,
        "employment_status": Person.employment_status,
        "created_at": Person.created_at,
        "hire_date": Person.hire_date,
    }
    col = sort_map.get(sort_by) if sort_by else Person.id
    stmt = stmt.order_by(col.desc() if sort_order == "desc" else col.asc())
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return R(data=PageBase(items=[_with_alias_count(db, p) for p in items], total=total, page=page, page_size=page_size))


@router.get("/unmerged-aliases", response_model=R, summary="待归并别名列表(未关联+非系统)")
def list_unmerged_aliases(db: DBSession, _: CurrentUser):
    """只返回 person_id 为 NULL 的待归并别名(归并工作队列)"""
    items = list(
        db.scalars(
            select(PersonAlias)
            .where(PersonAlias.is_system.is_(False), PersonAlias.person_id.is_(None))
            .order_by(PersonAlias.alias_normalized, PersonAlias.id)
        )
    )
    _attach_alias_labels(db, items)
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
        _attach_alias_labels(db, aliases)
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

    # 获取当前分组信息
    current_group = None
    if p.current_group_id:
        g = db.get(Group, p.current_group_id)
        if g:
            current_group = {"id": g.id, "name": g.name}

    # 获取当前所有专项
    current_specialties = []
    assigns = list(db.scalars(
        select(PersonSpecialtyAssignment)
        .where(
            PersonSpecialtyAssignment.person_id == p.id,
            PersonSpecialtyAssignment.is_current.is_(True),
        )
        .order_by(PersonSpecialtyAssignment.is_primary.desc())
    ))
    spec_ids = [a.specialty_id for a in assigns]
    if spec_ids:
        specs = {s.id: s for s in db.scalars(select(Specialty).where(Specialty.id.in_(spec_ids))).all()}
        for a in assigns:
            if a.specialty_id in specs:
                current_specialties.append({
                    "id": a.specialty_id,
                    "name": specs[a.specialty_id].name,
                    "code": specs[a.specialty_id].code,
                    "is_primary": a.is_primary,
                    "difficulty_weight": specs[a.specialty_id].difficulty_weight,
                    "workload_ratio": a.workload_ratio,
                    "effective_from": a.effective_from,
                })

    # 基础信息
    detail = {
        "id": p.id,
        "display_name": p.display_name,
        "qw_user_id": p.qw_user_id,
        "qw_name": p.qw_name,
        "role": p.role,
        "department": p.department,
        "group_name": p.group_name,
        "position": p.position,
        "specialty": p.specialty,
        "hire_date": p.hire_date,
        "probation_end_date": p.probation_end_date,
        "regularization_date": p.regularization_date,
        "resignation_date": p.resignation_date,
        "employment_status": p.employment_status,
        "is_active": p.is_active,
        "is_confirmed": p.is_confirmed,
        "remark": p.remark,
        "user_id": p.user_id,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "current_group": current_group,
        "current_specialties": current_specialties,
    }

    aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.person_id == person_id).order_by(PersonAlias.id)))
    _attach_alias_labels(db, aliases)
    # 注入每个别名在各业务表的出现位置(回答"别名在哪些地方出现")
    table_map = _load_table_labels(db)
    occ_map = count_alias_occurrences(db, [a.alias for a in aliases])
    for a in aliases:
        a.occurrences = [
            {
                "table": o["table"],
                "field": o["field"],
                "table_label": table_map.get(o["table"], o["table"]),
                "field_label": FIELD_LABELS.get(o["field"], o["field"]),
                "count": o["count"],
            }
            for o in occ_map.get(a.alias, [])
        ]
    detail["aliases"] = [PersonAliasOut.model_validate(a).model_dump() for a in aliases]
    detail["alias_count"] = len([a for a in aliases if not a.is_system])
    return R(data=detail)


@router.get("/{person_id}/timeline", response_model=R, summary="人员审计时序流(跨表合并)")
def person_timeline(
    person_id: int,
    db: DBSession,
    _: CurrentUser,
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
    days: int | None = Query(90, ge=0, description="时间窗口天数,0=不限"),
    include_device_sn: bool = Query(True, description="每条事件包含设备SN(如有)"),
    include_person_name: bool = Query(False, description="每条事件包含操作人姓名(如已有subject通常不需)"),
):
    """人员维度审计时序:用 PersonAlias 在 6 张业务表人名字段查询关联记录,
    按时间倒序统一呈现(refund/ticket/wallet/cs_record/external_complaint/card_complaint,
    merchant_refund 无人员字段不参与)。

    用于人员审计页:时序流 + 明细表双视图。
    """
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")
    result = build_person_timeline(
        db, person_id,
        limit=limit,
        days=days if days else None,
        include_device_sn=include_device_sn,
        include_person_name=include_person_name,
    )
    return R(data=result)


@router.put("/{person_id}", response_model=R, summary="更新人员信息")
def update_person(person_id: int, payload: PersonUpdate, db: DBSession, _: AdminUser):
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")
    # 岗位/专项/分组变更走 StaffAssignment API,PersonUpdate 不含 group_name/position/specialty
    lifecycle_fields = {"hire_date", "probation_end_date", "regularization_date", "resignation_date"}
    data = payload.model_dump(exclude_unset=True)
    has_lifecycle_change = any(k in lifecycle_fields for k in data)
    for k, v in data.items():
        # employment_status 允许直接改(也可由 recalculate 自动推导)
        setattr(p, k, v)
    if has_lifecycle_change:
        from app.services.staff_assignment_service import recalculate_employment_status

        recalculate_employment_status(p)
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
    p = Person(display_name=payload.display_name, role=payload.role, department=payload.department, is_confirmed=True)
    db.add(p)
    db.flush()
    for a in aliases:
        a.person_id = p.id
    if affected:
        db.execute(update(Person).where(Person.id.in_(affected)).values(is_active=False))
    # group_name 作为初始任职记录写入(不直接设 Person.group_name,经 assignment 镜像同步)
    if payload.group_name:
        from datetime import date as date_cls

        from app.schemas.staff_assignment import StaffAssignmentCreate
        from app.services.staff_assignment_service import create_assignment

        create_assignment(
            db,
            p.id,
            StaffAssignmentCreate(
                effective_from=date_cls.today(),
                group_name=payload.group_name,
                source="manual",
            ),
        )
    db.commit()
    db.refresh(p)
    return R(data=_with_alias_count(db, p))


@router.post("/{person_id}/split", response_model=R, summary="从人员拆出别名(各自建新 person)")
def split_aliases(person_id: int, payload: SplitRequest, db: DBSession, _: AdminUser):
    """从人员拆出别名 — 解绑回待归并队列(person_id=NULL),不新建空壳 person

    拆出语义:"这个别名不属于此人"。回待归并队列等人工/HR 归并到正确的真人;
    确需独立成 person 走 merge_new。避免业务表名字反向污染 persons 主表。
    """
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")
    aliases = list(db.scalars(select(PersonAlias).where(PersonAlias.id.in_(payload.alias_ids))))
    split_count = 0
    for a in aliases:
        if a.person_id != person_id:
            continue
        a.person_id = None
        split_count += 1
    db.commit()
    return R(data={"split_count": split_count})


@router.post("/refresh-aliases", response_model=R, summary="触发别名重新提取(幂等)")
def refresh_aliases(db: DBSession, _: AdminUser):
    stats = extract_person_aliases(db)
    return R(data=stats)


@router.post("/rebuild-aliases", response_model=R, summary="全量重建未归并别名+重算归一化")
def rebuild_aliases(db: DBSession, _: AdminUser):
    """清空待归并别名(person_id=NULL,含历史脏残留)→重算全部归一化→从当前数据重新提取。

    用于: 归一化规则变更 / 清理过期脏别名(时间戳/列错位旧值) / 字段语义调整后重建。
    已归并到 HR 的别名不动,仅更新其 alias_normalized。
    """
    stats = rebuild_unclaimed_aliases(db)
    return R(data=stats)


@router.post("/batch-merge", response_model=R, summary="批量按建议归并别名到对应 HR 人员")
def batch_merge_aliases(payload: BatchMergeRequest, db: DBSession, _: AdminUser):
    """对选中(或全部)待归并别名,按系统建议(suggested_hr_person)批量归并到各自 HR 人员。

    - 仅归并 initial_match_type='unique' 的别名(normalized 精确匹配 / 首字母匹配)
    - 歧义/无建议的跳过,留人工
    - alias_ids 为空 = 全部待归并别名
    返回 {merged_count, skipped_count, merged:[{alias,target_name,match_source}], skipped:[{alias,reason}]}
    """
    stmt = select(PersonAlias).where(PersonAlias.is_system.is_(False), PersonAlias.person_id.is_(None))
    if payload.alias_ids:
        stmt = stmt.where(PersonAlias.id.in_(payload.alias_ids))
    aliases = list(db.scalars(stmt.order_by(PersonAlias.id)))
    _attach_alias_labels(db, aliases)

    merged: list[dict] = []
    skipped: list[dict] = []
    target_ids: set[int] = set()
    for a in aliases:
        if a.initial_match_type == "unique" and a.suggested_hr_person_id:
            a.person_id = a.suggested_hr_person_id
            target_ids.add(a.suggested_hr_person_id)
            merged.append({
                "alias_id": a.id,
                "alias": a.alias,
                "target_id": a.suggested_hr_person_id,
                "target_name": a.suggested_hr_person_name,
                "match_source": a.match_source,
            })
        else:
            skipped.append({
                "alias_id": a.id,
                "alias": a.alias,
                "reason": a.initial_match_type or "no_match",
            })
    if target_ids:
        db.execute(update(Person).where(Person.id.in_(target_ids)).values(is_confirmed=True))
    db.commit()
    return R(data={
        "merged_count": len(merged),
        "skipped_count": len(skipped),
        "merged": merged,
        "skipped": skipped,
    })


@router.post("/batch-create-by-normalized", response_model=R, summary="批量按归一名(alias_normalized)新建人员")
def batch_create_by_normalized(payload: BatchMergeRequest, db: DBSession, _: AdminUser):
    """对选中(或全部)待归并别名,按 alias_normalized 分组新建 Person。

    用途: 无系统建议目标的别名(无法批量按建议归并)批量归一 — 直接以当前归一名建 Person。
    - display_name = alias_normalized, department = null, role = 'agent', is_confirmed = True
    - 按 alias_normalized 分组,同组多个别名归并到同一 Person
    - 若已有同名 Person(忽略大小写),归并到该 Person 而非新建重复
    - "用户提现"等系统操作类型 alias_normalized == alias 原值,建 Person 名也是原名(用户原意"使用原先的")
    - alias_ids 为空 = 全部待归并别名(慎用,建议先选中)
    返回 {created, merged_to_existing, aliases_assigned, created_detail, merged_detail}
    """
    stmt = select(PersonAlias).where(PersonAlias.is_system.is_(False), PersonAlias.person_id.is_(None))
    if payload.alias_ids:
        stmt = stmt.where(PersonAlias.id.in_(payload.alias_ids))
    aliases = list(db.scalars(stmt.order_by(PersonAlias.id)))

    groups: dict[str, list[PersonAlias]] = {}
    for a in aliases:
        key = (a.alias_normalized or a.alias or "").strip()
        if not key:
            continue
        groups.setdefault(key, []).append(a)

    created: list[dict] = []
    merged: list[dict] = []
    total_assigned = 0
    for norm, group in groups.items():
        existing = db.scalar(
            select(Person).where(func.lower(Person.display_name) == norm.lower(), Person.is_active.is_(True))
        )
        if existing:
            for a in group:
                a.person_id = existing.id
            total_assigned += len(group)
            merged.append({"name": norm, "person_id": existing.id, "aliases": len(group)})
            continue
        p = Person(display_name=norm, role="agent", department=None, is_confirmed=True)
        db.add(p)
        db.flush()
        for a in group:
            a.person_id = p.id
        total_assigned += len(group)
        created.append({"name": norm, "person_id": p.id, "aliases": len(group)})
    db.commit()
    return R(data={
        "created": len(created),
        "merged_to_existing": len(merged),
        "aliases_assigned": total_assigned,
        "created_detail": created,
        "merged_detail": merged,
    })


@router.post("/batch-import-by-prefix", response_model=R, summary="批量按前缀纳入人员(前缀-XXX → 建 Person,指定部门)")
def batch_import_by_prefix(payload: BatchImportByPrefixRequest, db: DBSession, _: AdminUser):
    """扫描待归并的 {prefix}-XXX 别名,按 alias_normalized 分组建 Person(部门=department)。

    通用版:店铺/企微/随身WiFi 等前缀都可调用,指定归入部门。
    - 同 normalized 多别名只建一个 Person,归并全部
    - 若姓名已命中现有 HR:归并到该 Person,并补 department(若为空)
    - 否则新建 Person(display_name=normalized, department, role='agent', is_confirmed=True)
    返回 {created, merged_to_existing, aliases_assigned, created_detail, merged_detail}
    """
    prefix = payload.prefix.strip()
    department = payload.department.strip()
    if not prefix or not department:
        raise HTTPException(status_code=400, detail="prefix 和 department 必填")
    # 转义 LIKE 通配符防 prefix 含 % _
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"{escaped}%"
    aliases = list(db.scalars(
        select(PersonAlias)
        .where(PersonAlias.is_system.is_(False), PersonAlias.person_id.is_(None))
        .where(PersonAlias.alias.ilike(pattern, escape="\\"))
        .order_by(PersonAlias.id)
    ))
    if not aliases:
        return R(data={"created": 0, "merged_to_existing": 0, "aliases_assigned": 0,
                       "created_detail": [], "merged_detail": []})

    groups: dict[str, list[PersonAlias]] = {}
    for a in aliases:
        groups.setdefault(a.alias_normalized, []).append(a)

    created: list[dict] = []
    merged: list[dict] = []
    total_assigned = 0
    for norm, group in groups.items():
        existing = db.scalar(
            select(Person).where(func.lower(Person.display_name) == norm.lower(), Person.is_active.is_(True))
        )
        if existing:
            for a in group:
                a.person_id = existing.id
            total_assigned += len(group)
            if not existing.department:
                existing.department = department
            merged.append({"name": norm, "person_id": existing.id, "aliases": len(group)})
            continue
        p = Person(display_name=norm, role="agent", department=department, is_confirmed=True)
        db.add(p)
        db.flush()
        for a in group:
            a.person_id = p.id
        total_assigned += len(group)
        created.append({"name": norm, "person_id": p.id, "aliases": len(group)})
    db.commit()
    return R(data={
        "created": len(created),
        "merged_to_existing": len(merged),
        "aliases_assigned": total_assigned,
        "created_detail": created,
        "merged_detail": merged,
    })


@router.post("/batch-update", response_model=R, summary="批量更新人员字段(部门/角色/在职状态等+分组/专项建任职)")
def batch_update_persons(payload: BatchUpdateRequest, db: DBSession, _: AdminUser):
    """批量更新人员字段。未提供的字段(None)跳过不改。

    - Person 原生字段(department/role/employment_status/is_active/is_confirmed/hire_date):UPDATE persons
    - group_name/specialty 任一非空:为每个 id 调 create_assignment 批量建任职(effective_from=今天, source=manual)
    返回 {updated, assignments_created, skipped}
    """
    if not payload.ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    ids = payload.ids
    # Person 原生字段
    person_fields = {
        "department": payload.department,
        "role": payload.role,
        "employment_status": payload.employment_status,
        "is_active": payload.is_active,
        "is_confirmed": payload.is_confirmed,
        "hire_date": payload.hire_date,
    }
    updates = {k: v for k, v in person_fields.items() if v is not None}
    updated = 0
    if updates:
        result = db.execute(update(Person).where(Person.id.in_(ids)).values(**updates))
        updated = result.rowcount or 0
    # 分组/专项 → 批量建任职
    assignments_created = 0
    if payload.group_name is not None or payload.specialty is not None:
        from datetime import date as date_cls

        from app.schemas.staff_assignment import StaffAssignmentCreate
        from app.services.staff_assignment_service import create_assignment

        for pid in ids:
            p = db.get(Person, pid)
            if not p:
                continue
            create_assignment(
                db,
                pid,
                StaffAssignmentCreate(
                    effective_from=date_cls.today(),
                    position=None,
                    specialty=payload.specialty,
                    group_name=payload.group_name,
                    source="manual",
                    change_reason="批量编辑",
                ),
            )
            assignments_created += 1
    db.commit()
    return R(data={"updated": updated, "assignments_created": assignments_created, "total": len(ids)})


@router.delete("/{person_id}", response_model=R, summary="停用人员(不物理删除)")
def deactivate_person(person_id: int, db: DBSession, _: AdminUser):
    p = db.get(Person, person_id)
    if not p:
        raise HTTPException(status_code=404, detail="人员不存在")
    p.is_active = False
    db.commit()
    return R(data={"id": person_id, "is_active": False})
