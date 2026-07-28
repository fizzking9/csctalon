"""数据源元数据模型 - 自助分析引擎的地基表

每张可被引擎分析的物理表注册为一行 DataSource，其字段注册为 DataSourceField。
新表加入只需：建表迁移 + 在 data_source_registry.py 加种子 + refresh，引擎自动识别。
"""

import enum

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class SourceType(str, enum.Enum):
    """数据源类型"""

    BUSINESS = "business"  # 业务原始表（运营平台导出）
    SYSTEM = "system"  # 本系统创建（事项/发布页等）


class SemanticRole(str, enum.Enum):
    """字段语义角色 - 引擎据此分类维度/度量/时间等"""

    DIMENSION = "dimension"  # 维度（用于 GROUP BY）
    MEASURE = "measure"  # 度量（count/avg，非金额）
    AMOUNT = "amount"  # 金额度量（默认 sum）
    TIME = "time"  # 时间字段
    PERSON = "person"  # 人名（关联 persons）
    SN = "sn"  # 设备SN（关联 devices）
    CATEGORY = "category"  # 业务类别（dimension 子类，UI 单独分组）


class DataSource(IDMixin, TimestampMixin, Base):
    """数据源注册 - 一行 = 一张可被引擎分析的物理表"""

    __tablename__ = "data_sources"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False, comment="数据源代码 refund/wallet/ticket/merchant_refund")
    table_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="物理表名")
    name_cn: Mapped[str] = mapped_column(String(64), nullable=False, comment="中文名")
    source_type: Mapped[str] = mapped_column(String(32), default=SourceType.BUSINESS.value, nullable=False, comment="business原始/system本系统")
    time_field: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="默认时间字段（按日/周/月聚合用）")
    sn_field: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="SN 字段名")
    pk_field: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="主键字段；wallet=null")
    pk_strategy: Mapped[str] = mapped_column(String(32), default="primary_key", nullable=False, comment="primary_key/none（影响 diff 策略）")
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, comment="active/inactive")
    sync_source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="bms=从BMS系统同步, null=手动上传")
    category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True, comment="业务分组:现金流/工单与客服/投诉登记/基础数据")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class DataSourceField(IDMixin, TimestampMixin, Base):
    """数据源字段元数据 - 引擎据此生成可选维度/度量/筛选器"""

    __tablename__ = "data_source_fields"

    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="物理字段名")
    field_name_cn: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="中文名")
    data_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="string/numeric/timestamp/bool/jsonb")
    semantic_role: Mapped[str] = mapped_column(
        String(32), default=SemanticRole.DIMENSION.value, nullable=False, index=True,
        comment="dimension/measure/amount/time/person/sn/category",
    )
    agg_func: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="sum/count/avg（仅 measure/amount 有效）")
    is_filterable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否出现在筛选器")
    is_pk: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enum_values: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="枚举值列表")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("source_id", "field_name", name="uq_source_field"),)
