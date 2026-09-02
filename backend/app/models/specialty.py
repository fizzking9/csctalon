"""业务专项模型 - Specialty体系

设计原则:
- 专项属于任务属性,包含咨询接待、投诉接待、套餐专项、退款挽回等
- 专项负责人会轮岗(不同于分组组长相对固定)
- 支持临时专项(如双十一专项、618专项等)
- 一个人可以同时承担多个专项(主专项+多个辅助专项)
- 每个专项有难度系数,用于工作量加权计算
"""

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.person import Person


# 预定义的标准专项 (2026-08-10 按业务细分调整)
STANDARD_SPECIALTIES = {
    "consulting": {"name": "咨询接待", "difficulty_weight": 1.0, "is_temporary": False, "description": "常规咨询接待工作"},
    "package": {"name": "套餐专项", "difficulty_weight": 1.8, "is_temporary": False, "description": "套餐相关问题处理"},
    "refund_processing": {"name": "退款处理", "difficulty_weight": 2.0, "is_temporary": False, "description": "日常退款操作处理(唐彩慧等)"},
    "refund_recovery": {"name": "退款挽回", "difficulty_weight": 2.2, "is_temporary": False, "description": "退款客户挽回工作(朱海洋组)"},
    "internal_complaint": {"name": "内部投诉", "difficulty_weight": 3.0, "is_temporary": False, "description": "私域/企微内部客户投诉处理(刘景山/苏紫依等)"},
    "external_complaint": {"name": "外部投诉", "difficulty_weight": 5.0, "is_temporary": False, "description": "平台/12315等外部投诉处理(洪佳/陈玉红等)"},
}


class Specialty(IDMixin, TimestampMixin, Base):
    """业务专项 (如: 咨询接待、投诉接待、套餐专项、退款挽回等)

    专项是任务分配属性,负责人可能轮岗,支持临时专项。
    一个人同一时间可以分配到多个专项(有主/辅之分)。
    """
    __tablename__ = "specialties"

    code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True, index=True, comment="编码(consulting/complaint等,可空,临时专项可能无编码)")
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="专项名称,如 咨询接待")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="专项描述")
    difficulty_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False, comment="难度系数(用于工作量加权:咨询×1.0,套餐×1.8,退款×2.2,投诉×3.0)")
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否临时专项")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否启用")
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="显示排序")
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True, comment="专项生效日期(临时专项用)")
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="专项结束日期(临时专项用)")

    # 当前负责人镜像(由历史表同步,便于查询)
    current_lead_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True, comment="当前专项负责人ID(支持轮岗)")
    current_deputy_lead_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True, comment="当前副负责人ID")

    # 关系
    current_lead: Mapped["Person | None"] = relationship("Person", foreign_keys=[current_lead_id], lazy="joined")
    current_deputy_lead: Mapped["Person | None"] = relationship("Person", foreign_keys=[current_deputy_lead_id], lazy="joined")
    leadership_history: Mapped[list["SpecialtyLeadershipHistory"]] = relationship(back_populates="specialty", order_by="SpecialtyLeadershipHistory.effective_from.desc()")
    assignments: Mapped[list["PersonSpecialtyAssignment"]] = relationship(back_populates="specialty", order_by="PersonSpecialtyAssignment.effective_from.desc()")


class SpecialtyLeadershipHistory(IDMixin, TimestampMixin, Base):
    """专项负责人变更历史 (SCD Type 2)

    记录专项负责人的任免历史,支持轮岗。
    专项负责人轮岗相对频繁,需要完整追溯。
    """
    __tablename__ = "specialty_leadership_history"

    specialty_id: Mapped[int] = mapped_column(ForeignKey("specialties.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False, index=True, comment="被任命人")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="角色:lead=负责人,deputy=副负责人")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="生效日期")
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="失效日期(None=至今)")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True, comment="是否当前任职")
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="变更原因(轮岗/临时负责等)")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源")

    __table_args__ = (
        Index("ix_slh_spec_role_current", "specialty_id", "role", "is_current"),
    )

    specialty: Mapped["Specialty"] = relationship(back_populates="leadership_history")
    person: Mapped["Person"] = relationship(foreign_keys=[person_id], overlaps="specialty_leadership")


class PersonSpecialtyAssignment(IDMixin, TimestampMixin, Base):
    """人员专项分配历史 (SCD Type 2)

    记录人员被分配到哪个专项,支持:
    - 一个人同时承担多个专项(主专项+辅助专项)
    - 专项之间轮岗
    - 临时专项加入/退出
    - 主/辅专项区分(is_primary: True=主专项, False=辅助支援)
    """
    __tablename__ = "person_specialty_assignments"

    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    specialty_id: Mapped[int] = mapped_column(ForeignKey("specialties.id", ondelete="CASCADE"), nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="是否主专项(True=主,False=辅助支援)")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, comment="分配生效日期")
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, comment="分配结束日期(None=至今)")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True, comment="是否当前分配")
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="变更原因(分配/轮岗/临时支援/退出)")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="来源")
    workload_ratio: Mapped[float] = mapped_column(Float, default=1.0, nullable=False, comment="工作量占比(辅助专项可<1.0,用于多专项分摊)")

    __table_args__ = (
        Index("ix_psa_person_current", "person_id", "is_current"),
        Index("ix_psa_spec_current", "specialty_id", "is_current"),
    )

    person: Mapped["Person"] = relationship(back_populates="specialty_assignments")
    specialty: Mapped["Specialty"] = relationship(back_populates="assignments")
