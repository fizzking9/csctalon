"""SCRM 会话转接明细(事件流型)

每个会话可能被转接 N 次,每次转接一行。用于:
  - 转接率统计(发生 1 次及以上的会话数 / 总会话数)
  - 工作量拆分(一个会话按时间片/比例拆到多个客服)
  - 服务过程追踪(哪个客服接不住转给谁,用于培训辅导)
"""

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ScrmSessionTransfer(IDMixin, TimestampMixin, Base):
    """SCRM 会话转接事件"""

    __tablename__ = "scrm_session_transfers"

    session_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("scrm_sessions.session_id", ondelete="CASCADE"),
        index=True, nullable=False, comment="归属会话"
    )
    from_operator_id: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="转出客服 SCRM ID"
    )
    from_operator_name: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="转出客服名原文(可用于别名归一备用,但主要从 operator 表归一)"
    )
    to_operator_id: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="转入客服 SCRM ID"
    )
    to_operator_name: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="转入客服名原文"
    )
    transfer_time: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False, comment="转接发生时间"
    )
    transfer_reason: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="转接原因(如接口给)"
    )
    raw_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
