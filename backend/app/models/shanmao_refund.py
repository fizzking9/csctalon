"""闪猫退款模型 - 闪猫加速系统已退款申请数据,主键:退款单号"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class ShanmaoRefund(TimestampMixin, Base):
    """闪猫退款 - 闪猫加速系统已退款申请记录

    与 refunds(云猫BMS退费单) 数据隔离,便于独立分析两个平台的退款差异
    """

    __tablename__ = "shanmao_refunds"

    # ── 主键 & 关联 ──────────────────────────────────────────────
    refund_no: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="闪猫退款单号(唯一键,如 ALR/BRM 开头)"
    )
    order_no: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="关联订单号"
    )
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备SN"
    )

    # ── 金额 ────────────────────────────────────────────────────
    refund_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="申请退款金额"
    )
    actual_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="实际退款金额"
    )

    # ── 业务字段(列名待实际导出 CSV 确认后补全/调整) ──────────────
    package_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="套餐/商品名称"
    )
    merchant_name: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True, comment="商户名称"
    )
    refund_reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="退款原因"
    )
    refund_method: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="退款方式"
    )
    status: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="状态:已退款/审核中/已驳回等"
    )

    # ── 人员 ────────────────────────────────────────────────────
    applicant: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="申请人"
    )
    auditor: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="审核人"
    )
    operator: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="操作人(打款人)"
    )

    # ── 时间 ────────────────────────────────────────────────────
    apply_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="申请时间"
    )
    audit_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="审核时间"
    )
    refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际退款完成时间"
    )

    # ── 备注 ────────────────────────────────────────────────────
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    audit_remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="审核备注")

    # ── 扩展 ────────────────────────────────────────────────────
    raw_extra: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="原始CSV中未映射的其他字段(JSONB存储)"
    )

    # created_at/updated_at 来自 TimestampMixin(系统导入时间)
