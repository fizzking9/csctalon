"""通知通道模型 - 站内信/群机器人/邮件等推送通道配置

预警通知分发通过 Channel 抽象(services/notification_channel.py)实现,
本表存储各通道的启用状态与配置(敏感字段加密存储)。
"""

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class NotificationChannel(IDMixin, TimestampMixin, Base):
    """通知通道配置"""

    __tablename__ = "notification_channels"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True, comment="通道code:inapp/wecom_robot/dingtalk_robot/feishu_robot/email")
    name_cn: Mapped[str] = mapped_column(String(64), nullable=False, comment="通道名")
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False, default="inapp", comment="类型:inapp/webhook/email")
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="配置(webhook URL/密钥等, 敏感字段加密)")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="说明")
