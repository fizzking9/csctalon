"""对账复核批次模型 - ABCD/E 五路手动对账复核留痕(A-20260906-002 LOCKED / #22)

用途: 财务/管理按时间窗口发起对账复核, 冻结时点快照(五路合计+差异+勾稽归属+D重复
检出)与结论备注。**仅留痕存档, 不参与任何统计聚合**。

业务约定(用户已确认):
- 批次级标识(轻量): 一个时间窗一条记录, 不逐笔打标
- 仅留痕: 不反哺/改变 A/B/C/D/E 统计口径
- 权限: 复核写操作 Admin/Supervisor; 查询 CurrentUser
- 时间窗参数按北京时间; 金额单位元(与退款报表一致)
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ReconcileCheck(IDMixin, TimestampMixin, Base):
    """对账复核批次(仅留痕, 不参与统计)"""

    __tablename__ = "reconcile_checks"
    __table_args__ = {"comment": "对账复核批次(ABCD/E 五路, 仅留痕不参与统计)"}

    check_no: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True, comment="复核单号 RC+YYYYMMDD+序号"
    )
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="对账时间窗开始(UTC aware 存)"
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="对账时间窗结束(UTC aware 存)"
    )
    grain: Mapped[str] = mapped_column(String(8), nullable=False, default="day", comment="粒度 day/week")
    # 时点快照: {A/B/C/D:{count,amount}, operational, actual_cashflow, diff}(_cash_routes_total 输出)
    route_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="五路合计+差异时点快照")
    # E 勾稽归属: {A,B,C,D,unmatched} 笔数金额(water_statement 分类)
    by_route_of_e: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="E 水单勾稽归属")
    # D 路检出: {dup_order_groups, dup_amount, no_sn_weird_rows}
    dupe_detect: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="D 路重复/脏数据检出")
    conclusion: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="结论: 一致/差异已确认/存疑待查"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="复核备注(差异原因等)")
    reviewed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, comment="复核人 user id"
    )
    reviewed_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="复核人姓名")
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="复核时间(UTC aware 存)"
    )
