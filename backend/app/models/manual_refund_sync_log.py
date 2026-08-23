"""workbuddy 手动退款数据推送同步日志模型

记录每次 workbuddy 将《商户号手机退款处理登记》明细以 JSON push 到本系统的操作结果:
清洗行数、去重(新增/更新)、失败行、软警告,以及错误摘要。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ManualRefundSyncLog(IDMixin, TimestampMixin, Base):
    """手动退款同步日志 - 一次 workbuddy 推送 = 一行记录"""

    __tablename__ = "manual_refund_sync_logs"

    source_file: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="推送来源文件名")
    sync_source: Mapped[str] = mapped_column(String(32), default="workbuddy", nullable=False, comment="同步来源: workbuddy/manual")
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False, comment="success/failed/partial")

    total: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="收到总行数")
    inserted: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="新增行数")
    updated: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="更新行数")
    failed: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="失败行数(硬失败被丢弃)")
    warnings: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="软警告数(字段被清洗但行保留)")

    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误摘要")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
