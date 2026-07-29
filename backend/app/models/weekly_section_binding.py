"""周报分项默认绑定配置 - 管理员配一次, 一键生成本周周报看板时自动填充

每个 section_key 绑定默认的 view_ids + owner_id。
一键生成时: framework 各 section 从此配置取默认值, 草稿态仍可临时增删(软覆盖本次)。
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class WeeklySectionBinding(IDMixin, TimestampMixin, Base):
    """周报分项默认绑定(section_key → view_ids + owner_id)"""

    __tablename__ = "weekly_section_bindings"

    section_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True, comment="对应 WEEKLY_FRAMEWORK_SECTIONS 的 key"
    )
    view_ids: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="默认绑定的分析视图ID列表 [1,2,...]"
    )
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, comment="默认负责人"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
