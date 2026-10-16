"""退款五路**标准物理表** ORM 模型 (#332 退款五路数据标准层)

设计说明（Design Rationale）:
- 目的(替谁/答何决策): 替全部退款统计分析(analytics / 导出 / AI 报告)提供**唯一真身
  数据源**。五路 ods 原始表只同步不治理, 经采纳作业(app.services.governance.
  refund_adoption)判定为 adopted 的行才进入对应 std_* 表; 统计只读 std_*,
  口径不再散落于各条 SQL 的 WHERE。
- 逻辑(为何这样组织): 五表列 = public.*_clean 薄视图(app.models.ods_clean)业务列的
  物理化, 类型/精度逐一对齐, 另加采纳溯源列; 由作业 TRUNCATE + INSERT…SELECT
  全量幂等重建(范式同 dim_device_combo_service)。
  - std_wallet_flow 额外列 flow_dir: inflow(订单退费转入, 蓄水池进水) /
    outflow(系统扣费、成功用户提现, 出水);
  - std_refund_manual 额外列 source_grade='manual': 手动登记可信度最低的固定标识;
  - 五表统一 adopt_rule_ver / adopted_at 记录"被哪个版本规则采纳、何时"。
- 口径: docs/standards/退款五路数据标准与采纳规则.md(META, 权威)。
- 取舍(不做什么及原因): ① std 表不存 adopt_state/adopt_reason(能进表即 adopted,
  机器规则码留在 ods 原表供对照); ② 不物理化 synced_at/raw_extra 等内部溯源列
  (沿用 clean 视图的暴露边界); ③ 采纳行不含人工补值, 原列什么值就物化什么值。
- 工单: #332 · 前置: #319(ods 分层) / #322(clean 薄视图)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class _StdAdoptColumns:
    """五张 std 表共用的采纳溯源列 (与 ods 侧四列区分: std 只留版本/时间)。"""

    adopt_rule_ver: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="采纳规则版本(首版 r20261016_v1)"
    )
    adopted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="被采纳入表的作业判定时间"
    )


class StdRefundOnline(_StdAdoptColumns, Base):
    """`public.std_refund_online` — A 路线上直退采纳行 (over ods.refunds)。

    采纳口径: status='已退款' AND (refund_method='线上' OR refund_method IS NULL)。
    列与 refund_clean 视图 21 业务列逐一对齐。
    """

    __tablename__ = "std_refund_online"
    __table_args__ = (
        Index("ix_std_refund_online_artime", "actual_refund_time"),
        Index("ix_std_refund_online_sn", "device_sn"),
        Index("ix_std_refund_online_status", "status"),
    )

    refund_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="退费单号 RF...")
    platform_order_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="平台订单号")
    third_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="三方订单号")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备SN")
    refund_reason: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="退费原因")
    package_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐名称")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户名称(运营标签)")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退费金额(元)")
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实退金额(元)")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    status: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="状态")
    refund_method: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="退款方式")
    audit_remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="审核备注")
    auditor: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="审核人")
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="操作人")
    created_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原始创建时间")
    updated_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原始更新时间")
    actual_refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际扣款时间(对账归日)"
    )
    quality_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据质量状态")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")


class StdWalletFlow(_StdAdoptColumns, Base):
    """`public.std_wallet_flow` — B 路钱包流水采纳行 (over ods.wallet_withdrawals)。

    采纳口径: 订单退费转入(inflow 全采纳) / 系统扣费(outflow) /
    用户提现且 remark/quality_note 不含「失败」「冻结」(outflow);
    失败/冻结提现与空 op_type 不进表。
    """

    __tablename__ = "std_wallet_flow"
    __table_args__ = (
        Index("ix_std_wallet_flow_optime", "op_time"),
        Index("ix_std_wallet_flow_sn", "device_sn"),
        Index("ix_std_wallet_flow_dir", "flow_dir"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="源行 id")
    account_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="账户ID")
    wallet_balance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="钱包余额")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="SN")
    op_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="操作类型")
    op_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="操作金额(元)")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    op_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="操作时间")
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="操作人员")
    actual_refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际扣款时间(对账归日)"
    )
    quality_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据质量状态")
    quality_note: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="质量说明")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")
    # ── 采纳作业派生列 ──
    flow_dir: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="资金方向: inflow=进水(订单退费转入) / outflow=出水(扣费/成功提现)"
    )


class StdRefundShanmao(_StdAdoptColumns, Base):
    """`public.std_refund_shanmao` — C 路闪猫退款采纳行 (over ods.shanmao_refunds)。

    采纳口径: status='已退款'; 审核中/已驳回等不进表。
    """

    __tablename__ = "std_refund_shanmao"
    __table_args__ = (
        Index("ix_std_refund_shanmao_rtime", "refund_time"),
        Index("ix_std_refund_shanmao_sn", "device_sn"),
        Index("ix_std_refund_shanmao_status", "status"),
    )

    refund_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="闪猫退款单号(TF开头)")
    order_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联订单号")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备SN")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="手机号码")
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实际支付金额(元)")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="申请退款金额(元)")
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实际退款金额(元)")
    package_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐/商品名称")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户名称")
    refund_reason: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="退款原因")
    refund_method: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="退款方式")
    status: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="状态")
    applicant: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="申请人")
    auditor: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="审核人")
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="操作人(打款人)")
    apply_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="申请时间")
    audit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="审核时间")
    refund_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="实际退款完成时间")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    audit_remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="审核备注")
    quality_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据质量状态")
    quality_note: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="质量说明")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")


class StdRefundManual(_StdAdoptColumns, Base):
    """`public.std_refund_manual` — D 路手动退款登记采纳行 (over ods.manual_refunds)。

    采纳口径: is_refunded='是' 且 quality_status NOT IN ('dup_residual','sn_unresolvable')。
    source_grade 固定 'manual': 提示本路为人工登记、可信度最低, 统计消费方需知晓。
    """

    __tablename__ = "std_refund_manual"
    __table_args__ = (
        Index("ix_std_refund_manual_artime", "actual_refund_time"),
        Index("ix_std_refund_manual_sn", "device_sn"),
        Index("ix_std_refund_manual_refunded", "is_refunded"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="源行 id")
    register_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="登记日期")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备号")
    order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="订单号")
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="商户ID(微信商户号)")
    order_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="订单金额(元)")
    registrant: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="登记人")
    is_refunded: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="是否已退款(是/否)")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退款金额(元)")
    refund_operator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="退款操作人")
    op_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="操作时间")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    actual_refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际扣款时间(对账归日)"
    )
    quality_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据质量状态")
    quality_note: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="质量说明")
    register_time_raw: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="登记日期原始值(纠错前保留)"
    )
    backfill_refund_record_report_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="op_time 回填来源 refund_record_reports.id"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")
    # ── 采纳作业派生列 ──
    source_grade: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'manual'"),
        comment="来源可信度等级: manual=人工登记(可信度最低, 一期唯一值)",
    )


class StdRefundRecord(_StdAdoptColumns, Base):
    """`public.std_refund_record` — E 路企微商户号退款水单采纳行。

    采纳口径: 全量(当前数据全部退款成功); refund_status 原样保留,
    未来若出现非成功状态由统计侧按 refund_status 防御性过滤。
    over ods.refund_record_reports, 列与 refund_record_report_clean 15 业务列对齐。
    """

    __tablename__ = "std_refund_record"
    __table_args__ = (
        Index("ix_std_refund_record_rtime", "refund_time"),
        Index("ix_std_refund_record_sn", "device_sn"),
        Index("ix_std_refund_record_mid", "merchant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="源行 id")
    platform_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="平台订单号")
    wx_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="微信订单号")
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="商户号(企业维度锚点)")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户名(企业真名)")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备号")
    product_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商品名称")
    refund_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="退款时间")
    apply_refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="申请退款金额(元)")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退款金额(元)")
    refund_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="退款状态(原样保留)")
    backfill_op_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="回填到 D 路 op_time 的时间"
    )
    backfill_manual_refund_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="已回填的 manual_refunds.id"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="源行系统导入时间")


class AdoptionOverride(Base):
    """`public.adoption_overrides` — 采纳结论人工覆盖留痕 (#332 FR-6)。

    唯一键 (route, src_pk): 同一原始行**保留最新生效覆盖**(提交 API 走
    ON CONFLICT DO UPDATE); 采纳作业全量重算机器规则码仍写 ods.adopt_reason,
    最终 state 以本表 target_state 为准(reason 加 manual: 前缀)。

    取舍: 一期不保留覆盖历史版本(规则码机器结论 + operation_log 审计已可对照);
    若未来要求覆盖历史可追溯, 再改为 (route, src_pk, created_at) 历史表设计。
    """

    __tablename__ = "adoption_overrides"
    __table_args__ = (
        UniqueConstraint("route", "src_pk", name="uq_adoption_overrides_route_pk"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    route: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="路别: A/B/C/D/E(或 online/wallet 等代码常量)"
    )
    src_pk: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="原始行主键值(refund_no / id 等字符串化)"
    )
    target_state: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="人工目标状态: adopted/excluded/pending"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, comment="人工覆盖理由(必填)")
    operator: Mapped[str] = mapped_column(String(64), nullable=False, comment="操作人")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False, comment="提交时间"
    )
