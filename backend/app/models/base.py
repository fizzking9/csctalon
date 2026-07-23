"""ORM 模型基类 - 提供 id / 创建更新时间等公共字段"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """创建/更新时间混入"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class IDMixin:
    """自增主键混入"""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
