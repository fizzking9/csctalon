"""策略文档模型 - 发布页核心

架构定位(H41~43):客服中心策略/规范统一发布,保证全员一致。
政策经常变化,需版本管理 + 已读回执确认 CS 都已查看。

版本管理:
- 草稿(draft)可编辑;发布(published)后只读;归档(archived)历史版本可查
- 同 title 只能有一个 published;发布新版本时旧 published 自动 archived
- CS 只看 published 的最新版本
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class StrategyDoc(IDMixin, TimestampMixin, Base):
    """策略文档"""

    __tablename__ = "strategy_docs"

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True, comment="文档标题")
    category: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="分类:退款策略/清退策略/服务规范/通用")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="内容(Markdown)")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="版本号")
    status: Mapped[str] = mapped_column(
        String(16), default="draft", nullable=False, index=True, comment="draft草稿/published已发布/archived已归档"
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="发布时间")
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="发布人")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")


class StrategyReadReceipt(IDMixin, TimestampMixin, Base):
    """策略文档已读回执

    CS 查看已发布文档时自动记录;管理层可查看每篇文档的已读情况。
    unique(doc_id, user_id) 保证每用户每文档只记一次。
    """

    __tablename__ = "strategy_read_receipts"

    doc_id: Mapped[int] = mapped_column(Integer, ForeignKey("strategy_docs.id"), index=True, nullable=False, comment="文档ID")
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False, comment="用户ID")
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="阅读时间")

    __table_args__ = (UniqueConstraint("doc_id", "user_id", name="uq_strategy_read_doc_user"),)
