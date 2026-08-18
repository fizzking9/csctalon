"""跨部门协作派发模型(规划设计 §8.3 UpstreamTicket)

客服给上游打分(响应/解决率), 推动上游改进。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class UpstreamTicket(IDMixin, TimestampMixin, Base):
    """跨部门协作派发"""

    __tablename__ = "upstream_tickets"

    issue: Mapped[str] = mapped_column(Text, nullable=False, comment="问题描述")
    upstream_dept: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment="派发对象(上游部门)")
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="派发时间")
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="响应时间")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="解决时间")
    status: Mapped[str] = mapped_column(
        String(32), default="待响应", nullable=False, index=True, comment="待响应/处理中/已解决/挂起"
    )
    source_report_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="来源报告/周报")
    score: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="上游打分(1-5)")
    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="负责人")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
