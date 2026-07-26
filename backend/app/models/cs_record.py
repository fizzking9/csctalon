"""智能助手处理记录模型 - 客服接待与操作流水

记录客服接待执行明细:接待量、工作负荷、退款挽回成功率等分析的源头数据。
无天然唯一键(同设备同时间可多次接待),采用 IDMixin 自增主键 + pk_strategy=none,
导入时简单 insert(on_conflict_do_nothing 兜底),与 wallet 表一致。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class CsRecord(IDMixin, TimestampMixin, Base):
    """智能助手处理记录 - 客服接待/操作流水"""

    __tablename__ = "cs_records"

    problem_category: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="问题大类:退款/网络问题..."
    )
    problem_type: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="问题类型"
    )
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备号 SN"
    )
    solution: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="方案")
    operation_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="操作时间(主时间字段)"
    )
    operator: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="操作人:企微-XXX(绩效来源)"
    )
    send_content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="发送内容")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="说明")
    execution_result: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="执行结果说明"
    )
    user_accepted: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True, comment="用户是否接受:接受/未接受/不涉及"
    )
    consult_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="咨询时间"
    )
    problem_create_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="问题类型创建时间"
    )
    last_consult_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最后咨询时间"
    )
