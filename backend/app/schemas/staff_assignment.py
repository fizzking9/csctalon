"""人员任职历史 Pydantic 模型"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class StaffAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int
    effective_from: date
    effective_to: date | None = None
    is_current: bool
    position: str | None = None
    specialty: str | None = None
    group_name: str | None = None
    change_reason: str | None = None
    source: str
    created_at: datetime
    updated_at: datetime


class StaffAssignmentCreate(BaseModel):
    """新增任职记录 - effective_from 必填;后端自动关闭旧 current"""

    effective_from: date
    effective_to: date | None = None
    position: str | None = None
    specialty: str | None = None
    group_name: str | None = None
    change_reason: str | None = None
    source: str = "manual"


class StaffAssignmentUpdate(BaseModel):
    """编辑任职记录 - 仅非键字段;改 current 的 position/specialty/group_name 会同步镜像"""

    effective_to: date | None = None
    position: str | None = None
    specialty: str | None = None
    group_name: str | None = None
    change_reason: str | None = None
