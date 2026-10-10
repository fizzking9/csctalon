"""行政分组模型 - Group体系

设计原则:
- 分组属于行政管理体系,调整频率较低
- 每个分组有组长,组长相对固定但支持变更(有历史)
- 人员分组归属支持入组/调组/离组历史记录
- 副主管也属于分组管理层,支持轮岗
"""

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.person import Person


class Group(IDMixin, TimestampMixin, Base):
    """行政分组 (如: 董梦玲组、叶珍凤组、张蕾组等)

    分组是行政管理单位,每个分组有固定的组长,调整频率较低。
    当前组长/副主管为镜像字段,通过历史表维护变更记录。
    """
    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="组名,如 董梦玲组")
    department: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True, comment="所属部门:企微/店铺/研发")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="显示排序")

    # 当前组长/副主管镜像(由历史表同步,便于查询)
    current_leader_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True, comment="当前组长ID")
    current_deputy_leader_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True, comment="当前副主管ID(可空,轮岗)")

    # 关系
    current_leader: Mapped["Person | None"] = relationship("Person", foreign_keys=[current_leader_id], lazy="joined")
    current_deputy_leader: Mapped["Person | None"] = relationship("Person", foreign_keys=[current_deputy_leader_id], lazy="joined")
    leadership_history: Mapped[list["GroupLeadershipHistory"]] = relationship(back_populates="group", order_by="GroupLeadershipHistory.effective_from.desc()")
    member_history: Mapped[list["PersonGroupMembership"]] = relationship(back_populates="group", order_by="PersonGroupMembership.effective_from.desc()")


class GroupLeadershipHistory(IDMixin, TimestampMixin, Base):
    """分组组长/副主管变更历史 (SCD Type 2)

    记录组长和副主管的任免历史,支持轮岗追溯。
    组长相对固定,副主管支持轮岗。
    """
    __tablename__ = "group_leadership_history"

    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False, index=True, comment="被任命人")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="角色:leader=组长,deputy=副主管")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="生效日期")
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="失效日期(None=至今)")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True, comment="是否当前任职")
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="变更原因")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源:manual/xlsx_import")

    __table_args__ = (
        Index("ix_glh_group_role_current", "group_id", "role", "is_current"),
    )

    group: Mapped["Group"] = relationship(back_populates="leadership_history")
    person: Mapped["Person"] = relationship(foreign_keys=[person_id], overlaps="group_leadership")


class PersonGroupMembership(IDMixin, TimestampMixin, Base):
    """人员分组归属历史 (SCD Type 2)

    记录人员在哪个分组(行政归属),支持入组/调组/离组历史。
    一个人同一时间只能属于一个行政分组。
    """
    __tablename__ = "person_group_memberships"

    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="入组日期")
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="离组日期(None=至今)")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True, comment="是否当前归属")
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="变更原因(入组/调组/离组)")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源")

    __table_args__ = (
        Index("ix_pgm_person_current", "person_id", "is_current"),
    )

    person: Mapped["Person"] = relationship(back_populates="group_memberships")
    group: Mapped["Group"] = relationship(back_populates="member_history")
