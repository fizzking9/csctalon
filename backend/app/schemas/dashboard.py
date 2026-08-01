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


class DashboardExecuteRequest(BaseModel):
    """看板批量执行请求 - start/end 为空则各视图回退自身 time_range_default

    time_grain 为可选的时间粒度覆盖(day/week/month),仅对含 time 维度的趋势项生效;
    饼图/表格/明细无 time 维度,自动忽略,无副作用。
    """

    start: str | None = None
    end: str | None = None
    time_grain: str | None = None


class DashboardItemResult(BaseModel):
    """看板单项执行结果 - 错误隔离,失败项填 error。

    含视图元数据(view_name/ratios/is_preset),前端卡片无需再单独 GET 视图详情,
    整个看板渲染只需 1 次 /dashboards/{id}/execute 请求。
    """

    view_id: int
    view_code: str | None = None
    title: str | None = None
    view_name: str | None = None
    ratios: list[dict[str, Any]] | None = None
    is_preset: bool | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
