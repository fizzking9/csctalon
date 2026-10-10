"""采集缺口记账模型(A-20260904-002 LOCKED / 工单#2 3.3)

一源一日一行(UNIQUE biz_date+data_type), 记录某业务日某数据源的采集缺口:
  - 同步失败 / 部分成功(有片失败) → 记 open / partial
  - 成功同步覆盖该日 → 标 filled
  - 调度档未尝试即放弃(低频档持续忙碌) → 记 open(record_abandoned_slots)
  - 手动补导覆盖 → 标 filled(uploads 导入路径调 gap_queue_service.mark_filled_for_dates)
  - 次日 5:00 清点只补真缺口(open/partial), 补成功 → filled

status 语义:
  open    = 该源该业务日存在缺口(尚未补)
  partial = 部分成功(有成功也有失败片), 剩余失败范围待补
  filled  = 缺口已补齐(无需再补)
  ignored = 人工忽略(不再自动补, 由人工决策)
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class GapRecord(IDMixin, TimestampMixin, Base):
    """采集缺口记录(一源一日一行)"""

    __tablename__ = "gap_records"
    __table_args__ = (
        UniqueConstraint("biz_date", "data_type", name="uq_gap_records_biz_date_type"),
    )

    biz_date: Mapped[str] = mapped_column(String(10), index=True, nullable=False, comment="业务日期 YYYY-MM-DD(北京时间)")
    data_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment="数据源类型(refund/ticket/wallet/cs_record...)")
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, comment="缺口状态:open/partial/filled/ignored")
    first_gap_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="首次发现缺口时间")
    last_try_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="末次补跑时间")
    try_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False, comment="已补跑尝试次数")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最近一次失败错误摘要")
    # 缺口时间范围(源内实际缺口的起止; 整日失败 = 全天, 部分失败 = 失败片的范围)
    source_range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="缺口时间范围起点")
    source_range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="缺口时间范围终点")
    remark: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="备注(如失败片清单摘要)")
