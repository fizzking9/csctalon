"""商户号现金流模型 - 财务数据,微信侧真实完整现金流

微信商户号交易流水(支付+退款混合),无稳定唯一自然键(微信退款单号在 Excel
导出时被存成 float 丢失精度,支付行则为 0),故用自增 id 作主键、全量保序插入。
商户退款单号(RF 前缀)为可靠业务键,留作后续准确数据补全的对接键。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class MerchantRefund(IDMixin, TimestampMixin, Base):
    """商户号现金流 - 微信侧真实现金流(交易流水,含支付与退款)"""

    __tablename__ = "merchant_refunds"
    __table_args__ = {"schema": "ods"}

    wx_refund_no: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="微信退款单号(Excel存float已损,仅留存)"
    )
    merchant_refund_no: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="商户退款单号(RF前缀,可靠业务键,后续补全对接用)"
    )
    trade_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True, comment="交易时间")
    app_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="公众账号ID")
    merchant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="商户号")
    sub_merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="特约商户号")
    device_no: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备号"
    )
    wx_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="微信订单号")
    merchant_order_no: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="商户订单号(关联退费单.平台订单号)"
    )
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="用户标识")
    trade_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="交易类型:JSAPI")
    trade_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="交易状态:SUCCESS")
    pay_bank: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="付款银行")
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="货币种类:CNY")
    settle_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="应结订单金额")
    voucher_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="代金券金额")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退款金额")
    recharge_voucher_refund: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="充值券退款金额")
    refund_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="退款类型")
    refund_status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="退款状态:SUCCESS")
    product_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商品名称")
    fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True, comment="手续费")
    rate: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True, comment="费率")
    order_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="订单金额")
    apply_refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="申请退款金额")
