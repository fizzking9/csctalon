"""workbuddy 投诉数据推送同步日志模型

记录每次 workbuddy 将 315投诉/卡商投诉 文件内容以 JSON push 到本系统的操作结果:
清洗行数、去重(新增/更新)、失败行、按 SN 关联服务工单数,以及错误摘要。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ComplaintSyncLog(IDMixin, TimestampMixin, Base):
    """投诉数据同步日志 - 一次 workbuddy 推送 = 一行记录"""

    __tablename__ = "complaint_sync_logs"

    source_file: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="推送来源文件名")
    sync_source: Mapped[str] = mapped_column(String(32), default="workbuddy", nullable=False, comment="同步来源: workbuddy/manual")
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False, comment="success/failed/partial")

    external_total: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="315外部投诉总行数")
    external_inserted: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="315新增")
    external_updated: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="315更新")
    external_failed: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="315失败行")
    external_matched: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="315关联工单数")

    card_total: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="卡商投诉总行数")
    card_inserted: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="卡商新增")
    card_updated: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="卡商更新")
    card_failed: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="卡商失败行")
    card_matched: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="卡商关联工单数")

    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误摘要")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
