"""专项执行工作流模型 - 项目式(第二期,MVP预留表结构)

一个专项 = 名称 + 一批设备SN + 负责人 + 状态流转 + 跟进记录
状态用 String 存储,Pydantic 层做枚举校验
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class TaskStatus(str, enum.Enum):
    PENDING_START = "pending_start"  # 待启动
    EXECUTING = "executing"  # 执行中
    FOLLOWING = "following"  # 跟进中
    DONE = "done"  # 已完成
    ARCHIVED = "archived"  # 已归档


class SpecialTask(IDMixin, TimestampMixin, Base):
    """专项任务(项目式)"""

    __tablename__ = "special_tasks"

    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="专项名称")
    task_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="类型:清退/涨价/限速/其他")
    operator_tag: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联设备标签")
    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="负责人")
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.PENDING_START.value, nullable=False, index=True, comment="状态")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="截止时间")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TaskDevice(IDMixin, TimestampMixin, Base):
    """专项-设备批次(批量设备SN)"""

    __tablename__ = "task_devices"

    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("special_tasks.id"), index=True, nullable=False)
    device_sn: Mapped[str] = mapped_column(String(64), ForeignKey("devices.sn"), index=True, nullable=False)
    device_status: Mapped[str] = mapped_column(String(32), default="pending", comment="待执行/执行中/已完成/异常")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskFollowup(IDMixin, TimestampMixin, Base):
    """专项跟进记录"""

    __tablename__ = "task_followups"

    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("special_tasks.id"), index=True, nullable=False)
    author_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status_change: Mapped[str | None] = mapped_column(String(64), nullable=True)
