"""事项跟进(待办)模型 - 工单式

MVP核心模块:客服中心日常事项管理
状态/优先级/来源用 String 存储,在 Pydantic 层做枚举校验(避免 SA Enum 迁移麻烦)
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class TodoStatus(str, enum.Enum):
    PENDING = "pending"  # 待办
    PROCESSING = "processing"  # 处理中
    CONFIRMING = "confirming"  # 待确认
    DONE = "done"  # 已完成


class TodoPriority(str, enum.Enum):
    URGENT = "urgent"  # 紧急
    HIGH = "high"  # 高
    MEDIUM = "medium"  # 中
    LOW = "low"  # 低


class TodoSource(str, enum.Enum):
    MANUAL = "manual"  # 手动新建
    TICKET = "ticket"  # 工单触发
    TASK = "task"  # 专项触发
    MATCH = "match"  # 人工补关联触发


class TodoItem(IDMixin, TimestampMixin, Base):
    """事项/待办(工单式)"""

    __tablename__ = "todo_items"

    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="事项标题")
    source: Mapped[str] = mapped_column(String(32), default=TodoSource.MANUAL.value, nullable=False, comment="来源")
    priority: Mapped[str] = mapped_column(String(32), default=TodoPriority.MEDIUM.value, nullable=False, comment="优先级")
    assignee_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="负责人")
    status: Mapped[str] = mapped_column(String(32), default=TodoStatus.PENDING.value, nullable=False, index=True, comment="状态")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="截止时间")
    related_device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联设备SN")
    related_task_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("special_tasks.id"), nullable=True, comment="关联专项")
    related_refund_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联退费单号")
    related_ticket_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联工单号")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="事项描述")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")


class TodoFollowup(IDMixin, TimestampMixin, Base):
    """待办跟进记录(时间线)"""

    __tablename__ = "todo_followups"

    todo_id: Mapped[int] = mapped_column(Integer, ForeignKey("todo_items.id"), index=True, nullable=False)
    author_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="跟进人姓名(冗余)")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="跟进内容")
    status_change: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="状态变更:pending->processing")
