"""SCRM 平台侧客服账号(操作员)快照模型

每次同步全量 upsert 一份快照,用于:
  - person 别名归一的 SCRM 侧对照 (scrm_operator_id → Person.id)
  - 部门/在线状态/接待能力 等展示信息
"""

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ScrmOperator(IDMixin, TimestampMixin, Base):
    """SCRM 客服账号快照 - 以 operator_id(PK) 为业务主键,每次同步覆盖更新"""

    __tablename__ = "scrm_operators"

    operator_id: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False, comment="SCRM 平台客服 ID(业务唯一键)"
    )
    operator_name: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, comment="客服显示名(原始名,如 企微-张三)"
    )
    # 镜像回 Person: 同步服务成功归并后回填 person_id
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), index=True, nullable=True,
        comment="归一后归属 Person；null 表示待归并"
    )
    department: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="SCRM 侧部门名称")
    position: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="岗位/角色原文")
    status: Mapped[str | None] = mapped_column(
        String(16), index=True, nullable=True, comment="在线状态:online/offline/busy(原文保留)"
    )
    last_active_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True, comment="SCRM 上报的最后活跃时间"
    )
    receive_count_today: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="当日接待会话数(如接口有,则每次同步覆盖)"
    )
    # 冗余原始响应(扩展字段不丢)
    raw_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="原始响应 JSON 冗余")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
