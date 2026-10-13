"""订单管理模型 - BMS 支付管理"订单管理"(订单台账)

来源:BMS 订单管理导出(列见页面)/ HTTP API: GET /bms-portal/order/findPageList
时间锚点: 创建时间 createDate(实测 startDate/endDate 过滤 createDate)
金额单位: 元(2位小数)。
幂等键: order_no(订单号, 如 WS2026.../SP2026... 前缀)
用途: 订单级台账; 补全设备-商户-订单上下文, 佐证 A 路退费单来源订单。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class BmsOrder(TimestampMixin, Base):
    """订单管理 - BMS 订单台账"""

    __tablename__ = "bms_orders"
    __table_args__ = {"schema": "ods"}

    order_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="订单号")
    order_type: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="订单类型(预存/储值卡充值等)")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备SN"
    )
    combo_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐名称")
    combo_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="套餐价格(元)")
    payable_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="应付价格(元)")
    actual_pay_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实际支付价格(元)")
    balance_pay_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="余额支付(元)")
    wx_order_no: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="微信订单号")
    pay_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True, comment="支付时间")
    pay_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="支付类型(1微信支付等)")
    order_status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="订单状态(0未支付/1已支付)")
    order_result: Mapped[str | None] = mapped_column(Text, nullable=True, comment="订单结果/下单返回")
    order_status_raw: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="订单状态原始码")
    create_user: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人(WeChat/Wallet/SYS)")
    create_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="创建时间(同步锚点)"
    )
    business_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="商户名称")
    business_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="内部商户ID")
    device_type_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备类型")
    refund_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="退款状态")
    active_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="激活时间")
    last_stored_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最后储值时间")
    mch_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="微信商户Id")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="微信商户名称")
    before_serve_valid_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="续费前服务有效期")
    after_serve_valid_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="续费后服务有效期")
    days: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="有效期天数")
    raw_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="原始数据冗余")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="同步采集时间")
