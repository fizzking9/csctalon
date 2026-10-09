"""工单操作流水表 - 业务级别追踪,支撑「更新时间=客服工作量基准」分析

与通用 operation_logs(HTTP 层审计)解耦,聚焦 ServiceTicket 生命周期内的每一次业务动作:
  - 工单登记、派发、转派
  - 催单(含 last_urge_by / last_urge_time)
  - 结单 / 取消 / 重开
  - 修改处理人、修改状态、修改服务项
  - 上游 BMS/SCRM 同步导致的字段变更(action_type = 上游更新)

下游使用场景:
  1) 客服每日"实际做了哪些动作"的时间基准(比 finish_time 更可靠)
  2) 某张工单的完整处理时间线
  3) 分析"结单后又被催单"的异常行为 (今天冷工单卡片会用到)
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class TicketActionLog(IDMixin, TimestampMixin, Base):
    """工单动作流水 - 一张工单生命周期内每一次业务变更都有一条"""

    __tablename__ = "ticket_action_logs"

    ticket_no: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("service_tickets.ticket_no", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联工单号",
    )
    action_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="动作类型:登记/派发/转派/催单/结单/取消/重开/改状态/改处理人/改服务项/上游更新/备注更新",
    )
    operator: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="操作人(中文姓名/企微名/系统/system)",
    )
    action_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="动作发生时间 (登记/派发/催单那一刻,不是落库时间)",
    )
    detail: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="动作细节快照, 如 {old_status:'在途', new_status:'已完成', old_handler:..., new_handler:...}",
    )
    remark: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="动作备注/原由(如催单备注文本)",
    )

    __table_args__ = (
        # 快速定位一张工单的时间线
        # Index("ix_ticket_action_logs_ticket_action", "ticket_no", "action_at"),
        # 快速按人+时间查当日动作
        # Index("ix_ticket_action_logs_operator_action", "operator", "action_at"),
    )
