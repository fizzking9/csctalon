"""人员任职历史模型 - SCD Type 2 时变维度

记录客服岗位/专项/分组的有效期区间,支持"按当时任职"关联历史业务数据
(如某天接待发生时该客服是什么岗位/专项,决定接待数据判断与绩效核算口径)。

设计要点:
- 每人至多一条 is_current=true 的记录(部分唯一索引保证,见迁移)
- 新增任职时自动关闭旧 current(effective_to = new.effective_from - 1天)
- 当前岗位/专项/分组镜像到 Person.position/specialty/group_name(由 service 层同步)
- 生命周期(入职/试用/转正/离职)是离散单点事件,挂在 Person 标量列,不进本表
"""

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class StaffAssignment(IDMixin, TimestampMixin, Base):
    """人员任职历史 - 岗位/专项/分组的 SCD Type 2 区间记录"""

    __tablename__ = "staff_assignments"

    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), index=True, nullable=False, comment="归属人员"
    )
    effective_from: Mapped[date] = mapped_column(
        Date, index=True, nullable=False, comment="生效起始日期"
    )
    effective_to: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="生效结束日期(null=至今/当前)"
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True, nullable=False, comment="是否当前任职(每人至多一条)"
    )
    position: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="岗位:客服/组长/主管"
    )
    specialty: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="专项:接待/投诉接待/主管"
    )
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="分组")
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="变更原因/备注")
    source: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False, comment="来源:manual/xlsx_import"
    )
