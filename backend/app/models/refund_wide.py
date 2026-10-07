"""退费宽表(物化) - 跨表关联预计算结果

由 matcher.match_all 产出的 DataMatchRelation 关联关系,把 refund 主表
与 ticket/wallet/external_complaint 的关键字段物化到一张宽表,
供分析引擎直接查询(引擎禁止 JOIN)。

构建逻辑见 wide_table_builder.build_refund_wide。
- 行范围(A-20260908-001 LOCKED): 线上+已退款 ∪ 带wallet关联 ∪ 设备SN有外部投诉
- PK=refund_no(与 refunds.refund_no 对应)
- 一条退费单可能关联多条 ticket(同 SN),取 order_no 命中优先,否则最新 finish_time
- 一条退费单可能关联多条 wallet(同 SN),取最新 op_time
- 外部投诉按 device_sn 聚合为 has_external_complaint/count/latest_time 三个统计字段
- 注: merchant_refund(mr_*) 关联字段已在 A-20260908-001 删除(零消费死字段),
  merchant_refunds 表/matcher 关联保留但不再物化进宽表
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class RefundWide(TimestampMixin, Base):
    """退费宽表(物化) - 一行 = 一条退费单的跨表关联视图"""

    __tablename__ = "refund_wide"

    # 主键 = 退费单号(与 refunds.refund_no 对应,但不加 FK 约束以避免物化时锁表)
    refund_no: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="退费单号(主键,与 refunds.refund_no 对应)"
    )

    # ---------- refund 主表冗余字段(视图 3/5 筛选/分组必需) ----------
    refund_method: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True, comment="退款方式(冗余自 refunds)")
    status: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="状态(冗余自 refunds)")
    refund_reason: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="退费原因(冗余自 refunds)")
    operator: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="操作人(冗余自 refunds,未归一化别名)")
    auditor: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="审核人(冗余自 refunds)")
    device_sn: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.sn"), index=True, nullable=True, comment="设备SN(冗余自 refunds)"
    )
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退费金额(冗余自 refunds)")
    created_at_raw: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True, comment="退费单原始创建时间(冗余自 refunds)"
    )

    # ---------- ticket 关联(同 SN 多工单时取 order_no 命中,否则最新 finish_time) ----------
    ticket_no: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="关联工单号")
    service_item: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="服务项(退款原因来源,冗余自 service_tickets)")
    service_category: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="服务类别(冗余自 service_tickets)")
    ticket_handle_duration: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="工单处理时长(分钟)")
    ticket_handler: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="工单处理人(冗余别名)")

    # ---------- wallet 关联(同 SN 多笔时取最新 op_time) ----------
    wallet_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="关联钱包提现记录ID(wallet_withdrawals.id)")
    wallet_op_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="钱包操作时间(提现时点)")
    wallet_op_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="钱包操作金额(提现金额)")

    # ---------- external_complaint 关联(按 device_sn 聚合,供分类规则1判定) ----------
    has_external_complaint: Mapped[bool] = mapped_column(
        Boolean, index=True, default=False, nullable=False, comment="该 SN 是否有外部投诉记录"
    )
    external_complaint_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="该 SN 外部投诉条数")
    external_complaint_latest_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="该 SN 最近一次外部投诉时间"
    )

    # ---------- 物化元信息 ----------
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="本行物化构建时间(每次重建刷新)"
    )
