"""人员统一模型 - 跨业务表的人员实体 + 别名

各业务表中的 operator/auditor/handler/registrant 等人名字段视为别名，
映射到统一的 Person。sys/system 标记为系统自动操作，不归并到真人。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class Person(IDMixin, TimestampMixin, Base):
    """统一人员 - 归并后的最终人员实体"""

    __tablename__ = "persons"

    display_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示姓名（归并后的最终名）")
    qw_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, comment="企微 userid（待对接企微后填）")
    qw_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="企微姓名")
    role: Mapped[str] = mapped_column(String(32), default="agent", nullable=False, comment="admin/supervisor/agent")
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="所属组")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否已人工确认归并")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="绑定登录账号（可选）")


class PersonAlias(IDMixin, TimestampMixin, Base):
    """人员别名 - 各业务表中的人名字段值

    alias 唯一约束保证幂等；person_id 可空表示待归并。
    """

    __tablename__ = "person_aliases"

    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), index=True, nullable=True, comment="归属 person；null=待归并"
    )
    alias: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False, comment="别名原值 如 '企微-张三'")
    alias_normalized: Mapped[str] = mapped_column(String(128), index=True, nullable=False, comment="归一化值（去企微-前缀+lower），用于归并建议")
    source_table: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="首次出现来源表")
    source_field: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="首次出现来源字段")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="sys/system 系统自动操作")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最后出现时间")
