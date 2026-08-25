"""系统事务通知模型 - 记录系统关键事务(同步/推送/导入/预警等)的执行概要

与 notifications(定向站内信)不同, system_events 是全局系统级事件流,
供系统管理员查看「系统做了什么、结果如何」(如发起同步及结果、接收推送及结果)。
独立于数据采集模块, 后续可扩展更多事务类型(category 自由扩展)。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class SystemEvent(IDMixin, TimestampMixin, Base):
    """系统事务事件"""

    __tablename__ = "system_events"

    category: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False,
        comment="类别: sync(数据同步) / push(推送接收) / import(导入) / alert(预警) / schedule(定时调度) / match(关联匹配) / auth(登录验证)",
    )
    action: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, comment="具体动作: bms_sync / complaint_push / manual_refund_push / test_login ..."
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="标题(人类可读)")
    summary: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="概要(结果数字/影响范围)")
    status: Mapped[str] = mapped_column(
        String(16), default="info", index=True, nullable=False,
        comment="结果: success / failed / partial / info",
    )
    level: Mapped[str] = mapped_column(
        String(16), default="info", index=True, nullable=False,
        comment="级别: info / warning / error (由写入方显式指定, 与 status 可不同)"
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="详情/错误信息")
    is_read: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True, nullable=False, comment="是否已读(角标/未读计数用)"
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="已读时间")
