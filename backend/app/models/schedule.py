"""排班 / 班次 / 考勤模型(规划设计 §4.1)

排班源头为企业微信(对接同步), 系统内仅做展示 + 顶班/请假微调标记 + 与接待/工单关联对照。
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ShiftTemplate(IDMixin, TimestampMixin, Base):
    """班次模板(字典)"""

    __tablename__ = "shift_templates"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="班次名:早班/晚班")
    start_time: Mapped[str] = mapped_column(String(5), nullable=False, comment="开始时间 HH:MM")
    end_time: Mapped[str] = mapped_column(String(5), nullable=False, comment="结束时间 HH:MM")
    is_overnight: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否跨天")
    color: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="颜色")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")


class Schedule(IDMixin, TimestampMixin, Base):
    """排班事实(人×日)"""

    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("person_id", "schedule_date", name="uq_schedules_person_date"),)

    person_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("persons.id", ondelete="CASCADE"), index=True, nullable=False, comment="人员"
    )
    schedule_date: Mapped[date] = mapped_column(Date, index=True, nullable=False, comment="排班日期")
    shift_template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("shift_templates.id"), nullable=True, comment="班次模板"
    )
    start_time: Mapped[str | None] = mapped_column(String(5), nullable=True, comment="实际开始(冗余,支持临时调班)")
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True, comment="实际结束")
    status: Mapped[str] = mapped_column(
        String(16), default="正常", nullable=False, comment="正常/顶班/请假/调班"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    source: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, comment="来源:wecom_api/wecom_export/manual"
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")


class Attendance(IDMixin, TimestampMixin, Base):
    """考勤打卡记录(企业微信同步)"""

    __tablename__ = "attendance"
    # #319 数据分层: 信源表(企微同步打卡原始数据)归入 ods schema
    # 注意: 本文件内 ShiftTemplate/Schedule 为系统内业务表, 留 public 不动
    __table_args__ = {"schema": "ods"}

    person_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("persons.id", ondelete="CASCADE"), index=True, nullable=False, comment="人员"
    )
    attendance_date: Mapped[date] = mapped_column(Date, index=True, nullable=False, comment="考勤日期")
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="上班打卡")
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="下班打卡")
    source: Mapped[str] = mapped_column(String(32), default="wecom", nullable=False, comment="来源")
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="原始打卡数据")
