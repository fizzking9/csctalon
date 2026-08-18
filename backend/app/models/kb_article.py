"""知识库 / 话术库模型(规划设计 §七)

政策=管理发布(policies); 话术/知识=一线检索(kb_articles); 两者通过 related_policy_id 打通。
首版纯文字话术。
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class KbArticle(IDMixin, TimestampMixin, Base):
    """知识/话术条目"""

    __tablename__ = "kb_articles"

    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="条目标题")
    category: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="分类:退款/投诉/套餐/网络/通用")
    question: Mapped[str | None] = mapped_column(Text, nullable=True, comment="常见问题/触发场景")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True, comment="标准处理流程/话术(Markdown)")
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="标签")
    related_policy_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("policies.id"), nullable=True, comment="关联政策"
    )
    status: Mapped[str] = mapped_column(
        String(32), default="草稿", nullable=False, index=True, comment="草稿/已发布/已归档"
    )
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="检索次数")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
