"""管理制度模型 - 运营制度/服务规范/流程SOP等管理发布物

带「分类/发布状态/生效期/附件/版本」,支持发布、审批、调整等操作并落操作日志。
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class PolicyCategory(str, enum.Enum):
    """制度分类 - 可根据业务扩展,字符串存储便于 Pydantic 校验"""
    SERVICE_STANDARD = "service_standard"  # 服务规范
    PROCESS_SOP = "process_sop"  # 流程SOP
    MANAGEMENT_RULE = "management_rule"  # 管理规定
    COMMUNICATION_SCRIPT = "communication_script"  # 沟通话术
    INCIDENT_RESPONSE = "incident_response"  # 应急预案
    OTHER = "other"  # 其他


class PolicyStatus(str, enum.Enum):
    DRAFT = "draft"  # 草稿
    PENDING_APPROVAL = "pending_approval"  # 待审批
    PUBLISHED = "published"  # 已发布
    ARCHIVED = "archived"  # 已归档


class Policy(IDMixin, TimestampMixin, Base):
    """管理制度(含SOP/规范/话术/预案等)"""

    __tablename__ = "policies"

    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="制度标题")
    category: Mapped[str] = mapped_column(
        String(32), default=PolicyCategory.OTHER.value, nullable=False, index=True, comment="分类"
    )
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="摘要/关键词")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="正文内容(Markdown/纯文本)")
    attachment_urls: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="附件URL列表(JSON数组)")
    version: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="版本号")
    status: Mapped[str] = mapped_column(
        String(32), default=PolicyStatus.DRAFT.value, nullable=False, index=True, comment="发布状态"
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="生效起始")
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="生效结束")
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, comment="制度负责人"
    )
    approver_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, comment="审批人"
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="审批时间")
    approver_note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="审批意见")
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="查阅次数")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注/调整记录")
    # ---- v0.5 增强(规划设计 §4.4) ----
    topic_tags: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="主题/关联内容标签")
    replaces_policy_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="替代的政策ID(self-FK,政策演进链)"
    )
    replaced_by_policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="被哪条新政策替代(冗余)")
    diagram_urls: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="示意图URL列表")
