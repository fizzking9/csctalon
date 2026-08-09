"""人员统一模型 - 跨业务表的人员实体 + 别名

各业务表中的 operator/auditor/handler/registrant 等人名字段视为别名，
映射到统一的 Person。sys/system 标记为系统自动操作，不归并到真人。

架构说明 (Group/Specialty 双体系):
- 行政分组 (Group): 行政管理单位,组长相对固定,调整频率低
- 业务专项 (Specialty): 任务属性,负责人可能轮岗,支持临时专项,一人可多专项
- group_name/position/specialty 为兼容镜像字段,由服务层从新表同步,保持向后兼容
"""

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.group import Group, PersonGroupMembership, GroupLeadershipHistory
    from app.models.specialty import Specialty, PersonSpecialtyAssignment, SpecialtyLeadershipHistory


class Person(IDMixin, TimestampMixin, Base):
    """统一人员 - 归并后的最终人员实体"""

    __tablename__ = "persons"

    display_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示姓名（归并后的最终名）")
    qw_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, comment="企微 userid（待对接企微后填）")
    qw_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="企微姓名")
    scrm_operator_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, comment="SCRM平台客服ID(对接后回填,聚合平台内部ID≠企微ID)")
    scrm_operator_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="SCRM平台客服显示名原文")
    role: Mapped[str] = mapped_column(String(32), default="agent", nullable=False, comment="admin/supervisor/agent")
    department: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="部门:企微/店铺/研发")
    
    # ========== 新架构: Group/Specialty 独立体系 ==========
    # 当前行政分组ID (外键,指向 groups 表)
    current_group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True, index=True, comment="当前行政分组ID")
    
    # 兼容镜像字段(由服务层同步,保持现有代码读取路径不变):
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="当前组名(镜像,从groups表同步)")
    position: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="当前岗位(客服/组长/副主管/主管)")
    primary_specialty: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="当前主专项(镜像,从person_specialty_assignments同步)")
    # specialty 字段保留兼容,指向 primary_specialty
    specialty: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="当前专项(兼容字段,同primary_specialty)")
    
    # 生命周期(离散单点事件,每人至多一次)——派生但存储,便于过滤查询
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="入职日期")
    probation_end_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="试用期到期日")
    regularization_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="转正日期")
    resignation_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="离职日期")
    employment_status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, index=True, 
        comment="在职状态:active(待入职)/probation(试用)/regular(正式)/transferred(调岗)/resigned(离职)/inactive(非活跃/占位)"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否已人工确认归并")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="绑定登录账号（可选）")

    # ========== 关系 ==========
    # 行政分组关系
    current_group: Mapped["Group | None"] = relationship("Group", foreign_keys=[current_group_id], lazy="joined", post_update=True)
    group_memberships: Mapped[list["PersonGroupMembership"]] = relationship(back_populates="person", order_by="PersonGroupMembership.effective_from.desc()")
    group_leadership: Mapped[list["GroupLeadershipHistory"]] = relationship(
        "GroupLeadershipHistory", 
        foreign_keys="GroupLeadershipHistory.person_id",
        order_by="GroupLeadershipHistory.effective_from.desc()"
    )
    
    # 业务专项关系
    specialty_assignments: Mapped[list["PersonSpecialtyAssignment"]] = relationship(back_populates="person", order_by="PersonSpecialtyAssignment.effective_from.desc()")
    specialty_leadership: Mapped[list["SpecialtyLeadershipHistory"]] = relationship(
        "SpecialtyLeadershipHistory",
        foreign_keys="SpecialtyLeadershipHistory.person_id", 
        order_by="SpecialtyLeadershipHistory.effective_from.desc()"
    )


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
