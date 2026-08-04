"""SCRM 同步日志(独立于 BmsSyncLog,字段更贴合 OpenAPI 分页调用)

差异点 vs BmsSyncLog:
  - api_call_count: 本轮调用了多少次 HTTP(分页)
  - data_type: scrm_operator / scrm_session_private / scrm_session_group / scrm_session_virtual
  - fetched_total / api_total 对账:平台返回总数 vs 实际入库总数(偏差>0.5%告警)
  - sync_scope: 本次查询窗口 start_time/end_time 字符串原文(兼容 SCRM 要求的格式)
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ScrmSyncLog(IDMixin, TimestampMixin, Base):
    """SCRM OpenAPI 同步运行日志"""

    __tablename__ = "scrm_sync_logs"

    data_type: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False,
        comment="scrm_operator / scrm_session_private / scrm_session_group / scrm_session_virtual"
    )
    sync_scope: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="本次查询窗口: startTime→endTime 的人类可读摘要"
    )
    start_time: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="查询的起始时间(本地东八区)"
    )
    end_time: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="查询的结束时间(本地东八区)"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="running", nullable=False, index=True,
        comment="running/success/failed/reconciled_mismatch"
    )
    trigger_type: Mapped[str] = mapped_column(
        String(16), default="manual", nullable=False, comment="manual/scheduled/catch_up"
    )
    triggered_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="触发者用户名")

    # 调用量
    api_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="本轮 HTTP 调用次数(含分页)")
    api_total_reported: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="接口返回的 total 之和(跨页相加的总数)"
    )
    fetched_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="实际收到的记录数")

    # 入库三分类(对齐经验教训:created/updated/failed 拆分明细,不再误导"更新算成功")
    inserted_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="新写入行数")
    updated_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="已存在并覆盖更新行数")
    failed_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="入库失败行(解析错/主键错)")

    # 对账结果
    reconcile_delta: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="api_total_reported − (inserted+updated);正值=可能漏采"
    )

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="本轮结束时间"
    )
