"""报告模型 - 数据快照,审计的前提

核心原则(需求说明):
- 只有报告才能审计(动态数据不能审,数据会变结论就失效)
- 报告相对固化数据视图(快照一旦固化不再随源数据变)
- 报告要保存,审计结果也要保存

快照方案(已确认): 不做全表数据快照,固化为「视图配置+执行结果(关键数据) JSONB+可选PDF归档」。
理由: 大部分源数据在库中不常变,关键数据固化即可满足审计要求。
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ReportType(str, enum.Enum):
    WEEKLY = "weekly"  # 周报
    SPECIAL_AUDIT = "special_audit"  # 专项审计
    CUSTOM = "custom"  # 自定义


class ReportStatus(str, enum.Enum):
    DRAFT = "draft"  # 草稿(可编辑)
    CONFIRMED = "confirmed"  # 已确认(快照已固化,可审计)
    ARCHIVED = "archived"  # 已归档


class Report(IDMixin, TimestampMixin, Base):
    """报告(数据快照,审计的前提)"""

    __tablename__ = "reports"

    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="报告名(如 2026-W30 退款专项审计报告)")
    report_type: Mapped[str] = mapped_column(
        String(32), default=ReportType.CUSTOM.value, nullable=False, index=True, comment="类型:周报/专项审计/自定义"
    )
    time_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="锁定时间起")
    time_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="锁定时间止")
    filters: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="锁定的筛选条件")
    view_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="包含的视图引用列表 [{view_id,title},...]")
    snapshot: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="视图配置+执行结果快照(关键数据),confirm时固化"
    )
    pdf_archive: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="归档PDF路径(可选)")
    status: Mapped[str] = mapped_column(
        String(32), default=ReportStatus.DRAFT.value, nullable=False, index=True, comment="状态:草稿/已确认/已归档"
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="确认(固化)时间")
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="确认人")
    assignee_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, comment="指派负责人(专项下钻: 安排谁审查)"
    )
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="要求提交时间(专项下钻: 期限)"
    )
    source_weekly_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="来源周报ID(从周报某项下钻创建时回链)"
    )
    source_section_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="来源周报分项key(从周报某项下钻创建时回链)"
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
