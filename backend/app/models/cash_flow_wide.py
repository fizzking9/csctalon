"""现金流水统一宽表 - ABCD 经营四路每笔出款统一物化(A-20260908-004)

一行 = 一笔出款(A.线上直退/B.钱包提现/C.闪猫退款/D.手动退款), 用 route 列区分。
服务 ①对账(A+B+C+D 经营估算 vs E 实际现金流基准) ②快速响应 ③运营多维分析。
E(refund_record_reports) 不进本表(独立对账基准); e_matched 仅作经营侧对账标记。

构建逻辑见 cash_flow_wide_builder.build_cash_flow_wide。
口径与 analytics._cash_routes_total 保持一致(核心正确性要求, 需回归测试锁定)。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class CashFlowWide(TimestampMixin, Base):
    """现金流水宽表 - 经营四路每笔出款统一流水"""

    __tablename__ = "cash_flow_wide"

    # 自增主键(跨路无自然唯一键, 全量保序插入)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="自增主键")

    # ── 路别 & 业务标识 ─────────────────────────────────────────
    route: Mapped[str] = mapped_column(String(1), index=True, comment="路线: A=线上直退/B=钱包提现/C=闪猫退款/D=手动退款")
    # 来源表业务单号(A=refund_no / C=shanmao.refund_no / D=manual.order_no / B=wallet.remark解析RF或空)
    biz_no: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="业务单号(各表最强键,尽力而为)")
    # 来源表主键(精确定位回源): A=refund_no / B=wallet.id / C=shanmao.refund_no / D=manual.id
    src_id: Mapped[str] = mapped_column(String(128), index=True, comment="来源表主键(精确定位回源)")

    # ── 统一出款字段 ────────────────────────────────────────────
    pay_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True,
        comment="出款时间(A=created_at_raw/B=op_time/C=audit_time回退created_at/D=op_time回退register_time)",
    )
    pay_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True,
        comment="出款金额(A=refund_amount/B=op_amount/C=actual_amount/D=refund_amount)",
    )
    device_sn: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True, comment="设备SN")
    operator: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True,
        comment="操作人(A=operator/B=operator/C=operator或auditor/D=refund_operator或registrant, 归一别名)",
    )

    # ── 对账键 & 标记 ───────────────────────────────────────────
    e_key: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True,
        comment="对账键(A=platform_order_no直接对E / C,D=order_no / B=弱关联或空)",
    )
    e_matched: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="该笔是否在E(refund_record_reports)有匹配: True=matched/False=unmatched/None=弱关联未判定",
    )

    # ── 运营维度(尽力而为, 能填则填) ────────────────────────────
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="退款原因(A/C为主,B/D通常空)")
    merchant: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True, comment="商户名称(A/C为主)")
    package: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐名称(A/C为主)")
    has_external_complaint: Mapped[bool] = mapped_column(
        Boolean, index=True, default=False, nullable=False, comment="该SN是否有外部投诉(按device_sn聚合)"
    )
    auditor: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="审核人(A/C)或登记人(D=registrant)")

    # 物化元信息
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="本行物化构建时间(每次重建刷新)"
    )
