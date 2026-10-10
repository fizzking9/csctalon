"""对账复核逐笔勾稽结果模型 - E 水单→ABCD 单笔对应留痕(A-20260906-002 v1.1 / #22)

用途: 对账复核的"明细一一对应"闭环。以 E 退款记录报表每笔为基准, 记录其
自动归类(auto_route) + 候选匹配经营单(matched_ref) + 人工确认状态(confirm_status),
**仅留痕, 不改任何源表**(refunds/manual_refunds/wallet/shanmao/refund_record_reports)。

业务约定(用户 2026-09-06 澄清):
- 粒度: E→ABCD 逐笔核对(E 每笔 → 候选 ABCD 经营单 → 人工确认对应/无对应)
- 深度: 仅打标不改源(改数据走治理流程 #21)
- 存储: 新表持久化, 批次关联 reconcile_checks
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class ReconcileMatch(IDMixin, TimestampMixin, Base):
    """对账复核逐笔勾稽结果(仅留痕, 不改源, 不参与统计)"""

    __tablename__ = "reconcile_matches"
    __table_args__ = (
        UniqueConstraint("check_id", "report_id", name="uq_reconcile_matches_check_report"),
        {"comment": "对账复核逐笔勾稽(E→ABCD 单笔对应, 仅留痕不改源)"},
    )

    check_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reconcile_checks.id"), index=True, nullable=True,
        comment="关联复核批次(reconcile_checks.id, 可空=草稿未成批)",
    )
    report_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("refund_record_reports.id"), index=True, nullable=False,
        comment="E 退款记录报表 id(勾稽基准)",
    )
    # ── E 笔快照冗余(便于直接查/列表, 不依赖 join report 表) ──
    platform_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="E 笔平台订单号快照")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="E 笔退款金额快照(元)")
    refund_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="E 笔退款时间快照")
    # ── 勾稽信息 ──
    auto_route: Mapped[str | None] = mapped_column(
        String(16), index=True, nullable=True, comment="自动归类 A/B/C/D/unmatched(_classify_report)"
    )
    matched_ref: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="候选匹配经营单 [{route,source,source_id,ref_no,amount}]"
    )
    # ── 人工确认 ──
    confirm_status: Mapped[str | None] = mapped_column(
        String(16), index=True, nullable=True,
        comment="人工确认状态: 已核对一致/差异已确认/存疑待查/已排除重复/待核对",
    )
    confirm_note: Mapped[str | None] = mapped_column(Text, nullable=True, comment="人工备注(差异原因等)")
    confirmed_by: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="确认人 user id")
    confirmed_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="确认人姓名")
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="确认时间(UTC aware)"
    )
