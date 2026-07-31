"""分析视图与分类规则模型 - 自助分析引擎核心表

AnalysisView 存储用户自定义/预设视图配置（segments JSONB），
ClassificationRule 存储动态分类规则（查询时转 CASE WHEN 注入 SQL，不污染业务表）。

引擎是「哑执行器」：只看视图配置生成 SQL，不理解业务含义。
DataSourceField.semantic_role 仅作 ViewBuilder UI 分组提示，字段实际用途由配置位置决定。
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class AnalysisView(IDMixin, TimestampMixin, Base):
    """分析视图 - 一行 = 一个用户自定义/预设分析视图

    segments 配置示例:
    [
      {
        "source_code": "refund",
        "label": "直接退款",
        "dimensions": [{"field":"created_at_raw","alias":"日期","role":"time","time_grain":"day"}],
        "measures": [{"field":"refund_amount","agg":"sum","alias":"退款金额"}],
        "filters": [{"field":"refund_method","op":"eq","value":"线上"}],
        "classification_target": null,
        "limit": null,
        "order_by": null
      }
    ]
    """

    __tablename__ = "analysis_views"

    code: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True,
        comment="预设视图代码（preset 才有，如 preset_cash_trend）",
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="视图名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="line/bar/pie/table")
    segments: Mapped[list] = mapped_column(JSONB, nullable=False, comment="数据源片段配置数组")
    merge_strategy: Mapped[str] = mapped_column(
        String(16), default="add", nullable=False, comment="add=分表聚合后相加",
    )
    classification_rule_ids: Mapped[list] = mapped_column(
        JSONB, default=list, nullable=False, comment="引用的 ClassificationRule ID 列表",
    )
    time_range_default: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment='默认时间范围 {"days":30}',
    )
    ratios: Mapped[list] = mapped_column(
        JSONB, default=list, nullable=False, comment='比率度量数组 [{alias,numerator,denominator,as_percent}]'
    )
    cumulative: Mapped[list] = mapped_column(
        JSONB, default=list, nullable=False, comment='累计差额(池子水位)数组 [{alias,inflow,outflow}]'
    )
    is_preset: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="预设视图（迁移的4大看板）",
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, comment="active/archived")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ClassificationRule(IDMixin, TimestampMixin, Base):
    """分类规则 - 把维度值动态归类为业务标签（如清退/涨价/限速 → 专项类型）

    rules 配置示例:
    [
      {
        "label": "清退专项",
        "match": "any",  # any=OR, all=AND
        "conditions": [
          {"field":"refund_reason","op":"contains","value":"清退"},
          {"field":"operator","op":"in","value":["企微-张三"]}
        ]
      }
    ]

    支持 op: eq/ne/in/notin/contains/not_contains/gt/lt/gte/lte/between/is_null/is_not_null
    """

    __tablename__ = "classification_rules"

    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="规则名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules: Mapped[list] = mapped_column(JSONB, nullable=False, comment="规则定义数组")
    default_label: Mapped[str] = mapped_column(
        String(64), default="其他", nullable=False, comment="不匹配时的默认标签",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
