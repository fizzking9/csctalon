"""操作日志模型 - 审计跟踪(I47:操作日志要落库可查)

设计:
- 中间件自动记录所有变更请求(POST/PUT/DELETE),无需各端点显式埋点
- 登录等无 JWT 的事件由 auth 处理器显式记录
- user_id 可空(未登录/系统触发),username 冗余存储便于用户删除后仍可审计
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class OperationLog(IDMixin, TimestampMixin, Base):
    """操作日志 - created_at 即操作发生时间"""

    __tablename__ = "operation_logs"

    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True, comment="操作人ID"
    )
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="操作人账号(冗余,用户删除后仍可审计)")
    method: Mapped[str] = mapped_column(String(8), nullable=False, comment="HTTP方法")
    path: Mapped[str] = mapped_column(String(255), nullable=False, comment="请求路径")
    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment="操作标签(如 策略.发布)")
    target_type: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="操作对象类型")
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="操作对象ID")
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="响应状态码")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="详情(JSON或文本)")
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="客户端IP")
