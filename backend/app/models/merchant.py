"""商户主数据模型 - 来自'各商户号退款数据明细表-商户名称'"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class Merchant(IDMixin, TimestampMixin, Base):
    """商户主数据(子商户/主商户/法人/管理员)"""

    __tablename__ = "merchants"

    sub_name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False, comment="子商户名称")
    """子商户名称 - 与退费单.商户名称关联"""
    main_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="主商户名称")
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="商户号id")
    legal_person: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="法人")
    super_admin: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="超级管理员")
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="状态:正常/停用/无数据")
