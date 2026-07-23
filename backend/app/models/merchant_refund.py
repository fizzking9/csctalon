"""商户号退款模型 - 财务数据,微信侧真实完整现金流

主键:微信退款单号
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class MerchantRefund(TimestampMixin, Base):
    """商户号退款 - 微信侧真实现金流"""

    __tablename__ = "merchant_refunds"

    wx_refund_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="微信退款单号")
    trade_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True, comment="交易时间")
    app_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="公众账号ID")
    merchant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="商户号")
    sub_merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="特约商户号")
    device_no: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备号"
    )
    wx_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="微信订单号")
    merchant_order_no: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="商户订单号(关联退费单.平台订单号)"
    )
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="用户标识")
    trade_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="交易类型:JSAPI")
    trade_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="交易状态:SUCCESS")
    pay_bank: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="付款银行")
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="货币种类:CNY")
    settle_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="应结订单金额")
    voucher_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="代金券金额")
    refund_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退款金额")
    recharge_voucher_refund: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="充值券退款金额")
    refund_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="退款类型")
    refund_status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="退款状态:SUCCESS")
    product_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商品名称")
    fee: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True, comment="手续费")
    rate: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True, comment="费率")
    order_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="订单金额")
    apply_refund_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="申请退款金额")
