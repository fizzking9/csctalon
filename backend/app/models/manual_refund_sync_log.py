"""workbuddy 手动退款数据推送同步日志模型

记录每次 workbuddy 将《商户号手机退款处理登记》明细以 JSON push 到本系统的操作结果:
清洗行数、去重(新增/更新)、失败行、软警告,以及错误摘要。

#42 (2026-09-10) 新增:
  rejected              未采纳且已落拒收表的行数(待人工判定)
  validation_count      入库前校验问题条数(只读 advisory)
  validation_severity   按处置级别聚合的校验计数 JSON
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
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
    deleted: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="全量对账删除行数")
    failed: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="失败行数(硬失败被丢弃)")
    warnings: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="软警告数(字段被清洗但行保留)")

    # ── #42: 拒收与入库前校验(供前端展示, 不写 error 列以免污染"错误"语义) ──
    rejected: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="未采纳已落拒收表的行数(待人工判定)"
    )
    validation_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="入库前校验问题条数(只读 advisory)"
    )
    validation_severity: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment='校验问题按处置级别聚合({"block": n, "warn": m})'
    )

    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误摘要")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
