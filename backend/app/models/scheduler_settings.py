"""调度配置模型 - 高频采集节拍(时段 + 间隔)可配

背景(#51, A-20260910-006 LOCKED v1.0):
  高频档原为代码常量 `bms_scheduler.DAYTIME_HOURS = {8..23, 0}`(每小时),
  管理员无法在界面调整。本表把「起止时段 + 间隔」DB 化, 支持页面自助修改且无需重启。

设计:
- **单行表**(id 恒为 1): 避免"多行配置哪行生效"的歧义。
- 默认值 = 目标态: 8:00~24:00 每 30 分钟(32 槽/天)。
- 低频档(6:00)/5:00 补跑/8:00 全量 **不受本表影响**(见架构 §五 硬约束清单)。
"""

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# 单行表固定主键
SINGLETON_ID = 1

# 默认值(= 目标态; 也是 migration 灌入值)
DEFAULT_START_HOUR = 8
DEFAULT_END_HOUR = 24
DEFAULT_INTERVAL_MINUTES = 30

# 校验边界(与 API 层共用, 单一事实来源)
MIN_INTERVAL_MINUTES = 5    # 低于每轮实测耗时(~4.2min)必然重叠
MAX_INTERVAL_MINUTES = 180
MIN_HOUR = 0
MAX_HOUR = 24


class SchedulerSettings(IDMixin, TimestampMixin, Base):
    """高频采集节拍配置(单行)"""

    __tablename__ = "scheduler_settings"
    __table_args__ = (
        # 应用层已有 400 校验; DB 约束作为最后防线(防手工改库写入非法值)
        CheckConstraint("high_freq_start_hour >= 0 AND high_freq_start_hour <= 23",
                        name="ck_scheduler_settings_start_hour"),
        CheckConstraint("high_freq_end_hour >= 1 AND high_freq_end_hour <= 24",
                        name="ck_scheduler_settings_end_hour"),
        CheckConstraint(
            f"high_freq_interval_minutes >= {MIN_INTERVAL_MINUTES} "
            f"AND high_freq_interval_minutes <= {MAX_INTERVAL_MINUTES}",
            name="ck_scheduler_settings_interval",
        ),
    )

    high_freq_start_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_START_HOUR,
        comment="高频时段起(含), 北京时区小时 0~23",
    )
    high_freq_end_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_END_HOUR,
        comment="高频时段止(不含), 1~24; 24 表示至当日 24:00",
    )
    high_freq_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_INTERVAL_MINUTES,
        comment=f"采集间隔(分钟), {MIN_INTERVAL_MINUTES}~{MAX_INTERVAL_MINUTES}",
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="最后修改人账号",
    )
