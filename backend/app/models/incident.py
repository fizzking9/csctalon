"""异常事件登记模型 - 未通知的非预期事项,造成客服工作负荷突然激增

异常事件定义:未通知而做的操作或发生的非预期事项,导致客服工作负荷突然激增。
是周报"异常事故记录"、时间序列气泡图、跨部门协作的共同数据源。

转化链路:异常发生→登记→复盘→转预期事项→下次提前部署(异常→预防的闭环)。
铁律:异常登记用于改进不用于问责,否则员工会瞒报,数据彻底失真。
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class IncidentStatus(str, enum.Enum):
    REGISTERED = "registered"  # 登记
    PROCESSING = "processing"  # 处理中
    CALMED = "calmed"  # 已平息
    REVIEWED = "reviewed"  # 已复盘


class Incident(IDMixin, TimestampMixin, Base):
    """异常事件登记"""

    __tablename__ = "incidents"

    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="事项名")
    subject: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="主体(涉及的业务/设备/系统)"
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="明细描述")
    registrar_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, comment="登记人"
    )
    impact: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="影响(量化:工单增量/涉及金额/影响人数)"
    )
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True, comment="发生时间"
    )
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="持续时间(分钟)")
    related_tickets: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="关联工单号列表")
    status: Mapped[str] = mapped_column(
        String(32), default=IncidentStatus.REGISTERED.value, nullable=False, index=True, comment="状态"
    )
    converted_to_expected: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否转为预期事项(复盘后纳入提前部署)"
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="复盘结论/备注")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
