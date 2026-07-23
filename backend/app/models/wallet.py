"""钱包提现操作模型 - 财务数据

会员钱包流水:只有'用户提现'才是真实现金流出,且提现不受控
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class WalletWithdrawal(IDMixin, TimestampMixin, Base):
    """钱包提现操作 - 会员钱包流水"""

    __tablename__ = "wallet_withdrawals"

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
