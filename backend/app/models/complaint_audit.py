"""投诉审计模型(规划设计 §4.6 单个投诉审计)

单个投诉的审计产物: 设备SN / 涉及人员 / 接待过程 / 归因 / 审计结论。
与 AuditNote(报告审计)区分: 本表面向「单个投诉」的审计, 不要求先有报告。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ComplaintAudit(IDMixin, TimestampMixin, Base):
    """单个投诉审计"""

    __tablename__ = "complaint_audits"

    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="来源:internal/merchant/external"
    )
    ref_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="关联投诉标识(工单号/投诉ID)")
    device_sn: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="设备SN")
    involved_persons: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="涉及人员列表")
    attribution: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="归因:网络质量/套餐争议/退款协商失败/降成争议/其他/未关联SN")
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True, comment="审计结论")
    judgment: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="评判:通过/有问题/需改进")
    attachments: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="截图附件(聊天记录)")
    audited_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="审计人")
    audited_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="审计人姓名")
    audited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="审计时间")
