"""预警中心模型(规划设计 §八)

异常自动识别 / 工单积压 / 退款激增 等预警规则与预警记录。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class AlertRule(IDMixin, TimestampMixin, Base):
    """预警规则"""

    __tablename__ = "alert_rules"

    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="规则名")
    type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="类型:incident_surge/ticket_backlog/refund_surge"
    )
    condition: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="条件JSON")
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True, comment="阈值")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")


class Alert(IDMixin, TimestampMixin, Base):
    """预警记录"""

    __tablename__ = "alerts"

    rule_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("alert_rules.id"), nullable=True, comment="规则")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="预警标题")
    level: Mapped[str] = mapped_column(String(16), default="medium", nullable=False, comment="级别:high/medium/low")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="详情")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True, comment="发生时间")
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True, comment="pending待确认/confirmed已确认/ignored已忽略"
    )
