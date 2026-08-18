"""站内信/通知模型(规划设计 §五 统一工作流)

通知中心: 到期提醒(措施回看/异常跟进)、异常登记通知、政策发布通知、审计指派等。
局域网站内信 + 前端角标, 不依赖外部推送。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class Notification(IDMixin, TimestampMixin, Base):
    """站内信/通知"""

    __tablename__ = "notifications"

    recipient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False, comment="接收人"
    )
    type: Mapped[str] = mapped_column(
        String(32), default="info", nullable=False, index=True, comment="类型:info/measure/incident/policy/task/reimbursement/alert"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="标题")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="内容")
    ref_type: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="跳转资源类型")
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="跳转资源ID")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True, comment="是否已读")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="已读时间")
