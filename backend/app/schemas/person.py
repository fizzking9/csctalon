"""人员统一 Pydantic 模型"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class PersonAliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int | None = None
    alias: str
    alias_normalized: str
    source_table: str | None = None
    source_field: str | None = None
    # 中文名称(运行时由 persons.py 从 data_sources.name_cn + 字段映射补全)
    source_table_label: str | None = None
    source_field_label: str | None = None
    is_system: bool
    last_seen_at: datetime | None = None
    # 建议归并目标(系统自动匹配):
    # initial_match_type: unique(唯一匹配,可批量归并)/ambiguous(歧义,留人工)/None
    # match_source: 'normalized'(alias_normalized 与 HR display_name 精确相等,最可靠)
    #               / 'initial'(拼音首字母匹配,纯拉丁别名)
    suggested_hr_person_id: int | None = None
    suggested_hr_person_name: str | None = None
    initial_match_type: str | None = None
    match_source: str | None = None
    # 别名在各业务表的出现位置(仅 get_person 详情接口计算)
    # [{table, field, table_label, field_label, count}]
    occurrences: list[dict] = []


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    qw_user_id: str | None = None
    qw_name: str | None = None
    role: str
    department: str | None = None
    group_name: str | None = None
    position: str | None = None
    specialty: str | None = None
    hire_date: date | None = None
    probation_end_date: date | None = None
    regularization_date: date | None = None
    resignation_date: date | None = None
    employment_status: str = "active"
    is_active: bool
    is_confirmed: bool
    remark: str | None = None
    user_id: int | None = None
    alias_count: int = 0
    created_at: datetime
    updated_at: datetime


class PersonDetail(PersonOut):
    aliases: list[PersonAliasOut] = []


class PersonUpdate(BaseModel):
    """更新人员信息 - 岗位/专项/分组变更走 StaffAssignment API,此处不含 group_name/position/specialty"""

    display_name: str | None = None
    qw_name: str | None = None
    role: str | None = None
    department: str | None = None
    is_active: bool | None = None
    is_confirmed: bool | None = None
    remark: str | None = None
    # 生命周期(离散单点事件)
    hire_date: date | None = None
    probation_end_date: date | None = None
    regularization_date: date | None = None
    resignation_date: date | None = None
    employment_status: str | None = None


class MergeRequest(BaseModel):
    """把多个别名归并到目标 person"""

    alias_ids: list[int]


class BatchImportByPrefixRequest(BaseModel):
    """按别名前缀批量纳入人员:店铺/企微/随身WiFi 等前缀的待归并别名 → 建 Person(指定部门)"""

    prefix: str
    department: str


class BatchUpdateRequest(BaseModel):
    """批量更新人员字段。未提供的字段(None)跳过不改。
    group_name/specialty 非空时为每人批量建任职记录(effective_from=今天, source=manual)。
    """

    ids: list[int]
    department: str | None = None
    role: str | None = None
    employment_status: str | None = None
    is_active: bool | None = None
    is_confirmed: bool | None = None
    hire_date: date | None = None
    group_name: str | None = None
    specialty: str | None = None


class MergeNewRequest(BaseModel):
    """新建 person 并归并多个别名 - group_name 保留兼容前端,后端转为初始任职记录"""

    alias_ids: list[int]
    display_name: str
    role: str = "agent"
    department: str | None = None
    group_name: str | None = None


class SplitRequest(BaseModel):
    """从 person 拆出别名，各自建新 person"""

    alias_ids: list[int]


class BatchMergeRequest(BaseModel):
    """批量按建议归并别名到对应 HR 人员 - alias_ids 为空=全部待归并"""

    alias_ids: list[int] = []


class SkillTagItem(BaseModel):
    """单条技能标签(人员技能矩阵的一格)"""

    skill_tag: str
    level: int = 1  # 熟练度 1-5


class PersonSkillsUpdate(BaseModel):
    """全量替换某人员的技能标签集(以本 payload 为准,多余删除)"""

    skills: list[SkillTagItem]


class BatchSkillsUpdate(BaseModel):
    """批量打标: mode=add(追加/覆盖熟练度) | remove(删除该标签)"""

    person_ids: list[int]
    skill_tag: str
    level: int = 1
    mode: str = "add"  # add / remove
