"""钱包提现操作模型 - 财务数据

会员钱包流水:只有'用户提现'才是真实现金流出,且提现不受控
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin
from app.models.quality_mixin import QualityMixin


class WalletWithdrawal(QualityMixin, IDMixin, TimestampMixin, Base):
    """钱包提现操作 - 会员钱包流水"""

    __tablename__ = "wallet_withdrawals"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "op_type", "op_time", "operator", "op_amount", "device_sn",
            name="uq_wallet_withdrawals_business"
        ),
    )

    account_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="账户ID")
    wallet_balance: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="钱包余额")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="SN"
    )
    op_type: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True, comment="操作类型:用户提现/订单退费转入")
    op_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="操作金额")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注(含退费单号 RF...)")
    op_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True, comment="操作时间")
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="操作人员:SYS/用户提现")

    # ── 实际扣款时间(2026-09-09 对账时间对齐) ──────────────────
    # E 路(refund_record_reports.refund_time)按 SN+金额+时间窗 唯一匹配回填的
    # "实际扣款时间", 对账/现金流统计按此归日(消除 op_time 操作日 vs E 扣款日错位)。
    # E 滞后未回填时退化用 op_time。op_time 保留运营口径。
    actual_refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="实际扣款时间(E路refund_time回填, 对账用)"
    )
