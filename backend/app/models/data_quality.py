"""数据质量规则模型(规划设计 §8.4 占位)

异常数据模式识别规则, 先留字段后扩充。
"""

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class DataQualityRule(IDMixin, TimestampMixin, Base):
    """数据质量规则(占位)"""

    __tablename__ = "data_quality_rules"

    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="规则名")
    target: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="作用对象(表/字段)")
    condition: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="规则条件JSON")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="说明")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
