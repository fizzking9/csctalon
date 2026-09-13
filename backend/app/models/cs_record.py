"""智能助手处理记录模型 - 客服接待与操作流水

记录客服接待执行明细:接待量、工作负荷、退款挽回成功率等分析的源头数据。
采用业务复合唯一键(device_sn, operation_time, operator, problem_category, problem_type, solution, consult_time, problem_create_time)
防止重复同步导致数据膨胀,冲突时 ON CONFLICT DO NOTHING。

⚠️ #77 (2026-09-13) 唯一约束必须加 NULLS NOT DISTINCT:
   8 列唯一键中 problem_create_time 大量为 NULL,而 PostgreSQL 唯一约束默认
   NULLS DISTINCT —— 任一列为 NULL 该行就不参与去重,重复行可无限插入。
   实测 2026-09 重复率 47%(单秒同一 SN 最多 64 行),见工单 #77。
   迁移: alembic/versions/a1c2e3f4d5b6_cs_record_unique_nulls_not_distinct.py
   ⚠️ 修改 __table_args__ 里的唯一键时,必须同步该迁移,否则约束语义会静默退化。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class CsRecord(IDMixin, TimestampMixin, Base):
    """智能助手处理记录 - 客服接待/操作流水"""

    __tablename__ = "cs_records"
    __table_args__ = (
        UniqueConstraint(
            "device_sn", "operation_time", "operator",
            "problem_category", "problem_type", "solution",
            "consult_time", "problem_create_time",
            name="uq_cs_record_business"
        ),
    )

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
