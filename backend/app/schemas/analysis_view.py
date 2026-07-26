"""分析视图与分类规则 Pydantic 模型"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# ---------- 分类规则 ----------


class ClassificationRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    rules: list[dict[str, Any]]
    default_label: str
    is_active: bool
    owner_id: int | None = None
    created_at: datetime
    updated_at: datetime


class ClassificationRuleCreate(BaseModel):
    name: str
    description: str | None = None
    rules: list[dict[str, Any]]
    default_label: str = "其他"
    is_active: bool = True


class ClassificationRuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    rules: list[dict[str, Any]] | None = None
    default_label: str | None = None
    is_active: bool | None = None


class ClassificationRulePreviewRequest(BaseModel):
    """预览规则效果 - 传规则或规则ID + 目标"""

    rule_id: int | None = None
    rule: ClassificationRuleCreate | None = None
    source_code: str
    target_field: str
    limit: int = 100


# ---------- 分析视图 ----------


class AnalysisViewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str | None = None
    name: str
    description: str | None = None
    chart_type: str
    segments: list[dict[str, Any]]
    merge_strategy: str
    classification_rule_ids: list[int] = []
    time_range_default: dict | None = None
    is_preset: bool
    status: str
    owner_id: int | None = None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class AnalysisViewCreate(BaseModel):
    code: str | None = None
    name: str
    description: str | None = None
    chart_type: str
    segments: list[dict[str, Any]]
    merge_strategy: str = "add"
    classification_rule_ids: list[int] = []
    time_range_default: dict | None = None
    sort_order: int = 0


class AnalysisViewUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    chart_type: str | None = None
    segments: list[dict[str, Any]] | None = None
    merge_strategy: str | None = None
    classification_rule_ids: list[int] | None = None
    time_range_default: dict | None = None
    status: str | None = None
    sort_order: int | None = None


class AnalysisViewInlineConfig(BaseModel):
    """inline 配置(ViewBuilder 实时预览用,不需要 name)"""

    chart_type: str
    segments: list[dict[str, Any]]
    merge_strategy: str = "add"
    classification_rule_ids: list[int] = []
    time_range_default: dict | None = None


class AnalysisViewExecuteRequest(BaseModel):
    """执行视图 - 支持已存视图或 inline 配置"""

    view_id: int | None = None
    inline_config: AnalysisViewInlineConfig | None = None
    params: dict[str, Any] = {}

    def to_view_config(self) -> dict:
        """统一返回视图配置字典"""
        if self.inline_config:
            return {
                "chart_type": self.inline_config.chart_type,
                "segments": self.inline_config.segments,
                "merge_strategy": self.inline_config.merge_strategy,
                "classification_rule_ids": self.inline_config.classification_rule_ids,
                "time_range_default": self.inline_config.time_range_default,
            }
        return {}


class ExecuteResult(BaseModel):
    """执行结果 - 动态字段，用 dict 承载"""

    chart_type: str
    data: dict[str, Any]
