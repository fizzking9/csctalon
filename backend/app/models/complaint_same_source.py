"""外部投诉「同源案件」分组模型（工单 #208 / 草案 20260909 §6）

设计原则（草案 §6）：
- **打标不删**：不改 external_complaints 源数据，只在组表里表达「哪些行同源」。
- 两张表：
  - ComplaintSameSourceGroup   同源案件组（同 device_sn 的连通分量）
  - ComplaintSameSourceMember  组成员（一行 external_complaints 至多属于一个组）
- 状态机：auto（自动同源）/ needs_review（内嵌前文降级，待人工）
          → confirmed（人工确认）/ rejected（人工驳回）。
- ④C 取值（#206 拍板）：组登记日=组内最早 complaint_date（first_complaint_date）；
  处理人/分类/案件状态不物化，查询时取组内「最新有效行」，避免跟进后失真。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ComplaintSameSourceGroup(IDMixin, TimestampMixin, Base):
    """外部投诉同源案件组（一组同源重复登记行）"""

    __tablename__ = "complaint_same_source_groups"

    rule_version: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True,
        comment="判重规则版本（v5）",
    )
    device_sn: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="组设备 SN（同 SN 是 v5 硬前提）",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True,
        comment="auto=自动同源 / needs_review=待人工 / confirmed=人工确认 / rejected=人工驳回",
    )
    confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True,
        comment="置信度=组内所有命中对的最低相似度（保守值）",
    )
    first_complaint_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
        comment="案件登记日=组内最早 complaint_date（④C 取最早）",
    )
    latest_complaint_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="组内最晚 complaint_date（展示跨度用）",
    )
    member_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2",
        comment="组成员数",
    )
    evidence: Mapped[dict | None] = mapped_column(
        JSONB(astext_type=Text()), nullable=True,
        comment="命中对证据 [{a,b,sim,daydiff,clues,embed,cross_cluster,first_date,last_date}]",
    )
    review_note: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="人工复核备注"
    )
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="复核人 user id")
    reviewed_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="复核人姓名")
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="复核时间(UTC aware)"
    )


class ComplaintSameSourceMember(IDMixin, TimestampMixin, Base):
    """同源组成员（external_complaints 行打标，不改源数据）"""

    __tablename__ = "complaint_same_source_members"
    __table_args__ = (
        UniqueConstraint("group_id", "complaint_id", name="uq_same_source_group_complaint"),
        # 一行投诉至多属于一个有效组（重跑先解散后重建，人工组保留期间其成员不参与重排）
        UniqueConstraint("complaint_id", name="uq_same_source_complaint_once"),
    )

    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("complaint_same_source_groups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    complaint_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("external_complaints.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
