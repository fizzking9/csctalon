"""人员统一 Pydantic 模型"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PersonAliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int | None = None
    alias: str
    alias_normalized: str
    source_table: str | None = None
    source_field: str | None = None
    is_system: bool
    last_seen_at: datetime | None = None


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    qw_user_id: str | None = None
    qw_name: str | None = None
    role: str
    group_name: str | None = None
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
    display_name: str | None = None
    qw_name: str | None = None
    role: str | None = None
    group_name: str | None = None
    is_active: bool | None = None
    is_confirmed: bool | None = None
    remark: str | None = None


class MergeRequest(BaseModel):
    """把多个别名归并到目标 person"""

    alias_ids: list[int]


class MergeNewRequest(BaseModel):
    """新建 person 并归并多个别名"""

    alias_ids: list[int]
    display_name: str
    role: str = "agent"
    group_name: str | None = None


class SplitRequest(BaseModel):
    """从 person 拆出别名，各自建新 person"""

    alias_ids: list[int]
