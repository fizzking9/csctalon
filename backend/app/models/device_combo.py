"""设备套餐模型 - 设备已购套餐与流量使用记录,主键:充值订单号

来源:BMS 设备套餐页导出。流量字段单位为字节,-1 表示无限流量(哨兵值)。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class DeviceCombo(TimestampMixin, Base):
    """设备套餐 - 设备已购套餐记录"""

    __tablename__ = "device_combos"

    recharge_order_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="充值订单号")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备SN"
    )
    package_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐名称")
    package_type: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="套餐类型:续费套餐")
    traffic_type: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="流量类型:无限/固定流量")
    merchant_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="商户名称")
    actual_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实际支付金额")
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="开始日期")
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="结束日期")
    updated_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原始更新时间")
    created_at_raw: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="原始创建时间"
    )
    valid_days: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="套餐有效天数")
    creator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人:WeChat/SYS/Wallet")
    available_traffic: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="可用流量(字节,-1=无限)")
    used_traffic: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="已用流量(字节)")
    remaining_traffic: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="剩余流量(字节)")
    status: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="当前状态:流量正常")
