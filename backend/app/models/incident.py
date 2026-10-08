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


class IncidentEventType(str, enum.Enum):
    """事件类型(#232 B1.5-1):
    - ABNORMAL: 异常事故(默认, 原语义) - 未通知的非预期事项导致客服负荷激增
    - BUSINESS: 经营事件 - 推文/广告/活动/定期维护等可预期事件, 用于时间轴
      标注与数据对照(在 B1.5-3 趋势曲线叠加中消费)
    """

    ABNORMAL = "异常事故"
    BUSINESS = "经营事件"


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
    # ---- v0.5 增强字段(规划设计 §4.2) ----
    category: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, comment="来源/类型:生产/运营/商务部/店铺/系统平台开发/其他服务商"
    )
    department: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="涉及部门(首版单选,JSONB预留多选扩展)"
    )
    severity: Mapped[str | None] = mapped_column(
        String(16), nullable=True, default="中", comment="严重程度:高/中/低"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="解决时间"
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="闭环时间"
    )
    is_closed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否闭环(与状态解耦)"
    )
    # ---- 周期性事件(2026-08-19): 广告推送/定期维护等阶段性影响事件 ----
    is_recurring: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否周期性事件(广告推送/定期维护等阶段性影响)"
    )
    recurrence_pattern: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="周期模式: daily/weekly/weekday/monthly(is_recurring=True时生效)"
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True, comment="周期生效起始(含)"
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="周期生效结束(含, NULL=无限期)"
    )
    # ---- 经营事件扩展字段(#232 B1.5-1, 2026-10-08) ----
    # event_type 与 occurred_at/duration_minutes 解耦: 「经营事件」可用 start_at/end_at
    # 表达持续区间; 「异常事故」保留原 occurred_at+duration_minutes 语义。
    # 用 String 而非 PG ENUM, 与 status/severity/category 保持一致(避免 ENUM 修改成本)。
    event_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=IncidentEventType.ABNORMAL.value,
        server_default=IncidentEventType.ABNORMAL.value,
        index=True,
        comment="事件类型: 经营事件/异常事故(默认)",
    )
    target_audience: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="目标群体(经营事件用: 推文/活动受众描述)"
    )
    start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="经营事件生效起始(含, 与 end_at 共同表达持续时间区间)",
    )
    end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="经营事件生效结束(含, NULL=点事件或无限期)",
    )
    impact_metrics: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="量化影响指标 {key: value} (用于事件与数据对照)",
    )
    # #235 B1.5-4 排班调整建议(仅经营事件用, 可空; 排班页关联展示后置于 B3-8)
    scheduling_suggestion: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment='排班调整建议(经营事件用, 如"推文后加早班1人")',
    )


class IncidentFollowup(IDMixin, TimestampMixin, Base):
    """异常事故跟进时间线(规划设计 §4.2.2)"""

    __tablename__ = "incident_followups"

    incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False, comment="关联异常事故"
    )
    author_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, comment="跟进人(用户)"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="跟进内容")
    status_change: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="状态变更记录")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="跟进人姓名")
