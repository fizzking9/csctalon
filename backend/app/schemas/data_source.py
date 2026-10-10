"""数据源元数据 Pydantic 模型"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DataSourceFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    field_name: str
    field_name_cn: str | None = None
    data_type: str
    semantic_role: str
    agg_func: str | None = None
    is_filterable: bool
    is_pk: bool
    enum_values: list[Any] | None = None
    sort_order: int


class DataSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    table_name: str
    name_cn: str
    source_type: str
    time_field: str | None = None
    sn_field: str | None = None
    pk_field: str | None = None
    pk_strategy: str
    status: str
    sync_source: str | None = None
    category: str | None = None
    description: str | None = None
    field_count: int = 0
    created_at: datetime
    updated_at: datetime

    # ── 数据查看树排序与投影（工单 #100） ──
    # sort_order: 组内业务顺序（服务端排序用；数据源表不存该列，由种子/接口填充）
    sort_order: int | None = None
    # is_projection=True 表示这是**同表别名**（如投诉组下的 service_tickets 投影），
    # 不落 data_sources 表，只读，且必须展示真身位置以防"重复展示"误判
    is_projection: bool = False
    projection_of: str | None = None
    readonly: bool = False
    true_body_category: str | None = None
    true_body_name: str | None = None


class DataSourceDetail(DataSourceOut):
    fields: list[DataSourceFieldOut] = []


class DataSourceUpdate(BaseModel):
    """数据源可编辑字段"""

    time_field: str | None = None
    sn_field: str | None = None
    status: str | None = None
    description: str | None = None


class DataSourceFieldUpdate(BaseModel):
    """字段元数据可编辑项"""

    semantic_role: str | None = None
    agg_func: str | None = None
    is_filterable: bool | None = None
    enum_values: list[Any] | None = None
    field_name_cn: str | None = None
