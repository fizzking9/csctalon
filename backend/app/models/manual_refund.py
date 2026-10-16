"""手动退款登记模型 - 商户号手机退款处理登记表

客服在商户号(微信商户)后台手动操作的退款登记台账:
- 登记日期 + 设备号 + 订单号 + 登记人 为业务去重键(设备号+订单号+登记日期+登记人 确定一笔退款单,
  退款金额/状态修改走 UPDATE, 不再因金额变化产生新行)
- 是否已退款(是/否): 已退款行构成 D 路(手动退款)经营出款; 未退款行 = 登记蓄水(水位)
- 对应数据源: 01样本数据/商户号手机退款处理登记.xlsx
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.adoption_mixin import AdoptionColumnsMixin
from app.models.base import IDMixin, TimestampMixin


class ManualRefund(AdoptionColumnsMixin, IDMixin, TimestampMixin, Base):
    """手动退款登记 - 客服在商户号后台手动操作的退款台账"""

    __tablename__ = "manual_refunds"
    __table_args__ = {"schema": "ods"}

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
    order_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, comment="订单金额"
    )
    registrant: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="登记人"
    )

    # ── 退款执行信息 ────────────────────────────────────────────
    is_refunded: Mapped[str | None] = mapped_column(
        String(8), index=True, nullable=True, comment="是否已退款(是/否)"
    )
    refund_amount: Mapped[Decimal | None] = mapped_column(
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

    # ── 实际扣款时间(2026-09-09 对账时间对齐) ──────────────────
    # E 路(refund_record_reports.refund_time)逐单回填的"实际扣款时间", 对账按此逐日
    # 匹配, 消除 op_time(客服操作/登记时间)与 E 扣款时间的错位。op_time 保留运营口径。
    actual_refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="实际扣款时间(E路refund_time回填, 对账用)"
    )

    # ── 数据质量(导入时校验/纠错) ────────────────────────────────
    quality_status: Mapped[str | None] = mapped_column(
        String(16), index=True, nullable=True, comment="数据质量状态: ok=正常 / auto_fixed=已自动纠错 / pending=待人工确认 / auto_filled=E回填 / dup_residual=已治理重复(排除) / ignored=人工彻底忽略(排除)"
    )
    quality_note: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="质量说明(自动纠错或待确认原因, 含原始值)"
    )
    # ── 辅助列(2026-09-05, #20): 数据修复/回填溯源 ──────────────
    # 登记日期原始值(纠错前): 导入时若 register_time 被自动纠错(auto_fixed), 此处保留
    # 原始值(如 Excel 里误填的未来年份), 供审计/回滚; 正常行 = NULL(与 register_time 相同)
    register_time_raw: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="登记日期原始值(纠错前保留, 正常行=NULL)"
    )
    # 回填来源: op_time 由退款记录报表(refund_record_reports)回填时, 记录来源现金流行 id,
    # 与 refund_record_reports.backfill_manual_refund_id 构成双向溯源(替代解析 quality_note 文本)
    backfill_refund_record_report_id: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True, comment="op_time 回填来源 refund_record_reports.id"
    )

    # created_at/updated_at 来自 TimestampMixin(系统导入时间)
