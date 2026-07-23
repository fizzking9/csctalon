"""用户与角色模型"""

import enum

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class RoleEnum(str, enum.Enum):
    """三级角色"""

    ADMIN = "admin"  # 管理员:全量
    SUPERVISOR = "supervisor"  # 主管:本组
    AGENT = "agent"  # 客服:仅本人


class User(IDMixin, TimestampMixin, Base):
    """用户表 - Web 账密登录"""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    """登录账号"""
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示姓名")
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default=RoleEnum.AGENT.value, nullable=False, comment="角色:admin/supervisor/agent")
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="所属组(主管用)")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 绑定企微姓名(用于匹配数据中的"企微-XXX"操作人)
    qw_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="企业微信姓名")

