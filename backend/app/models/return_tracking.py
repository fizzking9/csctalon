"""退货跟踪模型 - BMS 售后"退货跟踪"

来源:BMS 退货跟踪页(导出/API: POST /api/customer-return-tracking/page)
页面列: 设备类型/店铺/商户/实付金额/退货类型/退款金额/退货签收状态/退货快递单号/
        退货原因/下单时间/退货时间/签收时间/创建人/更新人/创建时间/更新时间/退货备注
时间锚点: 创建时间(推断, 实现以真实导出样本最终确认)
幂等键: 需依真实导出样本定(倾向 order_no/device_sn + 退货类型 复合)。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class ReturnTracking(TimestampMixin, Base):
    """退货跟踪 - 设备退货/换货全流程"""

    __tablename__ = "return_trackings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="自增主键")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备SN"
    )
    device_type: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备类型")
    shop: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="店铺")
    merchant: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="商户")
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实付金额(元)")
    return_type: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="退货类型(已激活退货/未签收退货/换货等)")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退款金额(元)")
    return_sign_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="退货签收状态(未收到等)")
    return_express_no: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="退货快递单号")
    return_reason: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="退货原因")
    order_no: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="销售订单号")
    order_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="下单时间")
    return_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="退货时间")
    sign_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="签收时间")
    creator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
    updater: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="更新人")
    created_at_raw: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="原始创建时间(同步锚点)"
    )
    updated_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原始更新时间")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="退货备注")
    raw_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="原始数据冗余")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="同步采集时间")
