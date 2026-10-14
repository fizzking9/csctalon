"""退费单模型 - 经营数据,主键:退费单号"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.quality_mixin import QualityMixin


class Refund(QualityMixin, TimestampMixin, Base):
    """退费单 - 退费行为记录

    关键业务规则:operator 为 sys/system 表示退到会员钱包(非真实现金流出)
    """

    __tablename__ = "refunds"
    __table_args__ = {"schema": "ods"}

    refund_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="退费单号 RF...")
    platform_order_no: Mapped[str | None] = mapped_column(String(64), index=True, comment="平台订单号 SP/WS/WX...")
    third_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="三方订单号")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备SN"
    )
    refund_reason: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="退费原因")
    package_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐名称")
    merchant_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="商户名称")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退费金额")
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实退金额")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    status: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="状态:申请中/已退款/无需退款")
    refund_method: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="退款方式:线上/线下(导出数据可能含描述性文本)")
    audit_remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="审核备注")
    auditor: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="审核人")
    operator: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="操作人:企微-XXX/sys/system")
    created_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True, comment="原始创建时间")
    updated_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原始更新时间")
    # ── 实际扣款时间(2026-09-09 对账时间对齐) ──────────────────
    # E 路(refund_record_reports.refund_time)按 platform_order_no 唯一匹配回填的
    # "实际扣款时间", 对账/现金流统计按此归日(消除 created_at_raw 跨日边界错位)。
    # E 滞后未回填时退化用 created_at_raw。created_at_raw 保留运营口径。
    actual_refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="实际扣款时间(E路refund_time回填, 对账用)"
    )
    package_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="套餐价格")
    raw_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="原始冗余字段")

    # ── 同步溯源 & 更新人拆分 (2026-08-06 P1-T4) ──
    # 注释同 ServiceTicket.synced_at / updated_by_raw
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
        comment="最后一次被导入/同步任务写入时间(每次 upsert 强制 now())",
    )
    updated_by_raw: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
        comment="上游(BMS/SCRM)原始更新人/最后操作人,原样导入不参与业务逻辑",
    )

    # 覆盖 TimestampMixin 的 created_at/updated_at 含义:这里指系统导入时间
    # created_at/updated_at 来自 TimestampMixin(系统导入时间)

# 注意: `public.refund_clean` 的**只读**映射 `RefundClean` 已迁至
# `app/models/ods_clean.py`(#322 方案A: 全部干净层视图模型集中一处, 便于
# 迁移/conftest 复用同一列清单 `CLEAN_VIEWS`)。本文件仅保留可写的 ods `Refund`。
