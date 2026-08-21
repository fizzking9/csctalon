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
    """预警规则

    两种规则模式(可并存):
    - 旧模式: type 硬编码(incident_surge/ticket_backlog/refund_surge/complaint_surge) + 单一 threshold
    - 新模式: metric_code 引用指标注册表 + 多级阈值(excellent/warning/critical) + 通道/接收人/静默配置
    """

    __tablename__ = "alert_rules"

    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="规则名")
    type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="类型:incident_surge/ticket_backlog/refund_surge 或 metric"
    )
    condition: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="条件JSON")
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True, comment="阈值(旧模式单一阈值)")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")

    # ---- 指标注册表模式(2026-08-21 指标阈值化) ----
    metric_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="引用指标注册表code")
    excellent_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="优秀线(可选)")
    warning_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="预警线")
    critical_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="告警线")
    channels: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="通知通道列表(默认['inapp'])")
    recipient_roles: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="通知角色列表(默认['admin','supervisor'])")
    silence_window: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="静默时段{start:'HH:MM',end:'HH:MM'}(不推送仅入库)")


class Alert(IDMixin, TimestampMixin, Base):
    """预警记录"""

    __tablename__ = "alerts"

    rule_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("alert_rules.id"), nullable=True, comment="规则")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="预警标题")
    level: Mapped[str] = mapped_column(String(16), default="medium", nullable=False, comment="级别:warning/critical")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="详情")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True, comment="发生时间")
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True, comment="pending待确认/confirmed已确认/ignored已忽略/recovered已恢复"
    )

    # ---- 指标模式字段(2026-08-21) ----
    metric_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="指标code")
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="触发时指标值")
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="触发阈值(预警/告警线)")
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="统计周期起(UTC)")
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="统计周期止(UTC)")
    data_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="数据是否完整(False=数据异常, 非业务告警)")
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="恢复时间")


class AlertDelivery(IDMixin, TimestampMixin, Base):
    """告警通知投递记录(幂等/失败降级可查)"""

    __tablename__ = "alert_deliveries"

    alert_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("alerts.id"), nullable=True, index=True, comment="告警记录")
    channel: Mapped[str] = mapped_column(String(32), nullable=False, comment="通道code")
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False, comment="success/failed/degraded")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="投递明细/错误")
