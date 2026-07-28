"""看板 Pydantic 模型"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DashboardItem(BaseModel):
    """看板项 - 引用一个 AnalysisView 并承载布局"""

    view_id: int
    view_code: str | None = None
    title: str | None = None  # 空则回退用 view.name
    span: int = 12  # 1-24 栅格宽度
    visible: bool = True


class DashboardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str | None = None
    name: str
    description: str | None = None
    category: str
    items: list[dict[str, Any]]
    time_range_default: dict | None = None
    is_preset: bool
    status: str
    visibility: str
    owner_id: int | None = None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class DashboardCreate(BaseModel):
    code: str | None = None
    name: str
    description: str | None = None
    category: str = "自定义"
    items: list[dict[str, Any]] = []
    time_range_default: dict | None = None
    visibility: str = "manager"
    sort_order: int = 0


class DashboardUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    items: list[dict[str, Any]] | None = None
    time_range_default: dict | None = None
    status: str | None = None
    visibility: str | None = None
    sort_order: int | None = None
