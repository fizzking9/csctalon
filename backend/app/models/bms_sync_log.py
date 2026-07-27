"""BMS 数据同步日志模型

记录每次从 BMS 平台同步数据的操作日志,
用于追踪同步状态、计算下次同步的时间范围。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class BmsSyncLog(IDMixin, TimestampMixin, Base):
    """BMS 同步日志 - 一次同步 = 一行记录"""

    __tablename__ = "bms_sync_logs"

    data_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False, comment="refund/ticket/wallet")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="查询的起始时间")
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="查询的结束时间")
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False, comment="running/success/failed")
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual", nullable=False, comment="manual/scheduled")
    triggered_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="触发者用户名")

    file_path: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="下载文件相对路径")
    filename: Mapped[str | None] = mapped_column(String(128), nullable=True)

    total_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inserted_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
