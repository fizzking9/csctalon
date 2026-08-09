"""部门间信息同步模型 - 跨部门协作与信息共享登记

目的:客服/运营/销售/技术等部门之间重要变化(规则、流程、活动、风险)的同步登记,
避免信息差导致客户投诉或效率下降。
登记后写入操作日志,提供状态流转(未读/已读/已回复/已闭环)、回执人、优先级等字段。
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class SyncCategory(str, enum.Enum):
    """同步信息分类"""
    RULE_CHANGE = "rule_change"  # 规则变动
    PROCESS_CHANGE = "process_change"  # 流程变动
    ACTIVITY_NOTICE = "activity_notice"  # 活动通知
    RISK_ALERT = "risk_alert"  # 风险预警
    DATA_SYNC = "data_sync"  # 数据同步
    COORDINATION = "coordination"  # 协作请求
    OTHER = "other"


class SyncPriority(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class SyncStatus(str, enum.Enum):
    DRAFT = "draft"  # 草稿
    SENT = "sent"  # 已发布(待接收方阅读)
    READ = "read"  # 已读
    RESPONDED = "responded"  # 接收方已回复
    CLOSED = "closed"  # 已闭环


class DeptInfoSync(IDMixin, TimestampMixin, Base):
    """部门间信息同步登记"""

    __tablename__ = "dept_info_syncs"

    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="同步标题")
    category: Mapped[str] = mapped_column(
        String(32), default=SyncCategory.OTHER.value, nullable=False, index=True, comment="同步分类"
    )
    priority: Mapped[str] = mapped_column(
        String(16), default=SyncPriority.NORMAL.value, nullable=False, index=True, comment="优先级"
    )
    status: Mapped[str] = mapped_column(
        String(24), default=SyncStatus.DRAFT.value, nullable=False, index=True, comment="同步状态"
    )
    from_department: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="发起部门")
    to_departments: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="接收部门(多部门逗号分隔)"
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="同步正文")
    impact: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="影响范围/需要配合的动作(量化或定性)"
    )
    attachment_urls: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="附件URL列表(JSON数组)")
    related_incident_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="关联异常事件ID")
    related_measure_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="关联措施ID")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="生效时间(变化类)")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="需回复/配合截止")
    sender_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, comment="发送人"
    )
    receiver_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="接收人ID列表(JSON数组)")
    read_receipts: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="阅读回执[{user_id, username, read_at}] JSON数组")
    response: Mapped[str | None] = mapped_column(Text, nullable=True, comment="接收方回复/协调结果")
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="回复时间")
    responded_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="回复人账号")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="闭环时间")
    closed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="闭环人账号")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人账号")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注/调整记录")
