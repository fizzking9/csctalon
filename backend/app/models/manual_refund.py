"""手动退款登记模型 - 商户号手机退款处理登记表

客服在商户号(微信商户)后台手动操作的退款登记台账:
- 登记日期 + 设备号 + 订单号 + 退款金额 为业务去重键(同一订单可分期多次退款)
- 是否已退款(是/否): 已退款行构成 D 路(手动退款)经营出款; 未退款行 = 登记蓄水(水位)
- 对应数据源: 01样本数据/商户号手机退款处理登记.xlsx
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ManualRefund(IDMixin, TimestampMixin, Base):
    """手动退款登记 - 客服在商户号后台手动操作的退款台账"""

    __tablename__ = "manual_refunds"

    # ── 登记信息 ────────────────────────────────────────────────
    register_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="登记日期(客服登记退款时间)"
    )
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备号"
    )
    order_no: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="订单号"
    )
    merchant_id: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="商户ID(微信商户号)"
    )
    order_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="订单金额"
    )
    registrant: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="登记人"
    )

    # ── 退款执行信息 ────────────────────────────────────────────
    is_refunded: Mapped[str | None] = mapped_column(
        String(8), index=True, nullable=True, comment="是否已退款(是/否)"
    )
    refund_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="退款金额"
    )
    refund_operator: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="退款操作人(实际执行退款的人)"
    )
    op_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="操作时间(实际退款操作时间)"
    )

    # ── 备注 ────────────────────────────────────────────────────
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")

    # ── 数据质量(导入时校验/纠错) ────────────────────────────────
    quality_status: Mapped[str | None] = mapped_column(
        String(16), index=True, nullable=True, comment="数据质量状态: ok=正常 / auto_fixed=已自动纠错 / pending=待人工确认"
    )
    quality_note: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="质量说明(自动纠错或待确认原因, 含原始值)"
    )

    # created_at/updated_at 来自 TimestampMixin(系统导入时间)
