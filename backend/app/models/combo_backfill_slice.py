"""设备套餐历史回补切片台账 (#299)

为什么需要这张表:
  BMS 设备套餐页导出有 10 万行硬上限(实测 100005 数据行, 按创建时间倒序截断),
  每日定时同步又因日期面板拦截搜索按钮长期只导出默认「近 7 天」→ 2026-07-28
  之前的历史全部缺失。回补服务把缺失日期切成小窗口逐片导出, 本表记录每片状态,
  使「分夜执行」可断点续跑、可审计。

状态机:
  pending → running → success  (窗口内有数据并已 upsert)
                     → empty    (窗口确认 0 行, 同样视为该片完成)
                     → failed   (当夜重试上限耗尽, 次夜重新置 pending 由游标续跑)
  pending/running → split      (导出触顶 rows≥100000, 自动生成两个半片子切片;
                                父片已入库的尾部数据会被子片 upsert 幂等覆盖, 无害)

口径:
  start_date/end_date 为**北京业务日**(device_combos.created_at_raw AT TIME ZONE
  'Asia/Shanghai' 的日期), 与回补缺口计算同一时区口径。
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin

# 切片状态枚举(服务层单一来源, 此处注释同步)
SLICE_PENDING = "pending"
SLICE_RUNNING = "running"
SLICE_SUCCESS = "success"
SLICE_EMPTY = "empty"
SLICE_FAILED = "failed"
SLICE_SPLIT = "split"

SLICE_DONE_STATUSES = (SLICE_SUCCESS, SLICE_EMPTY, SLICE_SPLIT)


class ComboBackfillSlice(TimestampMixin, Base):
    """设备套餐历史回补: 单个时间切片的执行台账"""

    __tablename__ = "combo_backfill_slices"
    __table_args__ = (
        UniqueConstraint("start_date", "end_date", name="uq_combo_backfill_slice_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True, comment="切片起始北京业务日")
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True, comment="切片结束北京业务日(含)")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SLICE_PENDING, index=True,
                                        comment="pending/running/success/empty/failed/split")
    total_rows: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="导出总行数(触顶判据)")
    inserted_rows: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="upsert 新增行数")
    updated_rows: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="upsert 更新行数")
    failed_rows: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="解析失败行数")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="累计尝试次数")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最近一次失败原因(截断)")
    triggered_by: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="scheduled/manual")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
