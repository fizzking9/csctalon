"""设备月流量使用模型 - 设备审计核心(单 SN × 月汇总)

来源:BMS 设备流量页 HTTP API(/bms-portal/deviceFlow/sn/pageList, timeType=2 按月)。
flowDate 留空 = 返回该 SN 所有有流量月份(每行一月); 指定月只查该月。
流量单位: GB(上游 flowValue 即 GB)。幂等键 (device_sn, stat_month):
  - 当月未完整月份可反复重取覆盖(用户需求: 9/5 取 9 月 9G, 10 月可能更新为更全值)
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class DeviceFlowUsage(TimestampMixin, Base):
    """设备月流量使用 - 按月汇总(GB)"""

    __tablename__ = "device_flow_usage"
    __table_args__ = (
        UniqueConstraint("device_sn", "stat_month", name="uq_device_flow_usage_sn_month"),
        # #319 数据分层: 信源表(BMS 月流量抓取)归入 ods schema
        {"schema": "ods"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="自增主键")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备SN"
    )
    merchant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="商户ID(businessId)")
    merchant_name: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="商户名称")
    device_type_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备类型")
    device_group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备组")
    device_combo_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="当前套餐")
    stat_month: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True, comment="统计月份 YYYY-MM")
    flow_gb: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True, comment="该月流量(GB)")
    month_complete: Mapped[bool | None] = mapped_column(nullable=True, default=False, comment="是否完整月(当前月=false可重取)")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="本次抓取时间")
