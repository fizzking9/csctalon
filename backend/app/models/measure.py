"""管理措施模型 - 审计/周报的输出物,持续跟进与到期回看的对象

与专项任务(SpecialTask=外部协调执行)、事项跟进(TodoItem=日常待办)职责分离:
Measure 是审计/周报输出的管理措施,带「预期目标+验证时间+验证方式」三字段,
到期回看(手动触发或自动定时扫描),形成 PDCA 的 C/A 闭环。

状态/类型/来源用 String 存储,Pydantic 层做枚举校验(避免 SA Enum 迁移麻烦)。
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class MeasureType(str, enum.Enum):
    INTERNAL = "internal"  # 内部管理措施
    EXTERNAL = "external"  # 外部协调对接


class MeasureSource(str, enum.Enum):
    AUDIT = "audit"  # 审计输出
    WEEKLY = "weekly"  # 周报输出
    INCIDENT = "incident"  # 异常事件转化
    MANUAL = "manual"  # 手工新建


class MeasureStatus(str, enum.Enum):
    DRAFT = "draft"  # 草稿
    EXECUTING = "executing"  # 执行中
    PENDING_REVIEW = "pending_review"  # 待回看
    EFFECTIVE = "effective"  # 已生效
    INEFFECTIVE = "ineffective"  # 未生效
    ARCHIVED = "archived"  # 已归档


class ReviewResult(str, enum.Enum):
    EFFECTIVE = "effective"  # 生效
    PARTIAL = "partial"  # 部分生效
    INEFFECTIVE = "ineffective"  # 未生效
    ADJUST = "adjust"  # 需调整(转新措施或升级)


class Measure(IDMixin, TimestampMixin, Base):
    """管理措施(审计/周报输出物,带到期回看)"""

    __tablename__ = "measures"

    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="措施标题")
    measure_type: Mapped[str] = mapped_column(
        String(32), default=MeasureType.INTERNAL.value, nullable=False, comment="类型:内部管理/外部协调"
    )
    source: Mapped[str] = mapped_column(
        String(32), default=MeasureSource.MANUAL.value, nullable=False, comment="来源:审计/周报/异常事件/手工"
    )
    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="负责人")
    related_report_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="关联报告(Report表待建,暂无FK)"
    )
    expected_goal: Mapped[str | None] = mapped_column(Text, nullable=True, comment="预期目标(量化或定性)")
    verify_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True, comment="验证时间(到期日)"
    )
    verify_method: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="验证方式(看哪个视图/指标/抽查)"
    )
    status: Mapped[str] = mapped_column(
        String(32), default=MeasureStatus.DRAFT.value, nullable=False, index=True, comment="状态"
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="生效起始")
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="生效结束")
    review_result: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="回看结论:生效/部分生效/未生效/需调整"
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="回看备注")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="回看时间")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="回看人")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
