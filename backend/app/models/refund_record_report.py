"""退款记录报表模型 - BMS 财务"退款记录报表"线上退款水单(E 路补充)

来源:BMS 退款记录报表导出 CSV(实测列 10 列):
  平台订单号,微信订单号,商户号,商户名,设备号,商品名称,退款时间,申请退款金额,退款金额,退款状态
金额单位: 元(2位小数, 与 merchant_refunds 的 Numeric(12,2) 一致)。
用途: E 路线上实时退款水单(merchant_refunds 为 Excel 快照, 本表为持续采集), 对账勾稽;
      D 路 manual_refunds.op_time 缺失时按 (设备号/订单号, 金额, 时间窗) 可信匹配回填。
幂等键: (platform_order_no, refund_time, refund_amount) 唯一 — 同平台单号+同退款时间+同金额=同一笔。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class RefundRecordReport(TimestampMixin, Base):
    """退款记录报表 - BMS 线上退款水单"""

    __tablename__ = "refund_record_reports"
    __table_args__ = (
        UniqueConstraint(
            "platform_order_no", "refund_time", "refund_amount",
            name="uq_refund_record_reports_dedup",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="自增主键")
    platform_order_no: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="平台订单号(WX/WS/WN/SP前缀)"
    )
    wx_order_no: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="微信订单号")
    merchant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="商户号(对账锚点)")
    merchant_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="商户名")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备号(对账锚点)"
    )
    product_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商品名称")
    refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="退款时间(精确到秒)"
    )
    apply_refund_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="申请退款金额(元)")
    refund_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退款金额(元)")
    refund_status: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="退款状态(退款成功等)")
    raw_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="原始数据冗余")
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="同步采集时间"
    )
    backfill_op_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="回填到 D 路 manual_refunds.op_time 的时间"
    )
    backfill_manual_refund_id: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="已回填的 manual_refunds.id(溯源)"
    )
