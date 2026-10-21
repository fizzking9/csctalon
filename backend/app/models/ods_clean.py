"""public 干净层视图的**只读** ORM 映射 (#322 方案A, 仅供 analytics 读)。

背景(#319/#322 数据分层):
- #319 把 21 张信源表移入 `ods` schema 后, C2 契约要求 `services/analytics/**`
  **不得直读 ods 源表**, 须改读 public 干净层(视图/宽表/字典)。
- 本模块集中登记 analytics 需要的「方案A 薄视图」: 一表一视图, **逐列取自 ods 源表,
  不做任何加工**(不改变任何口径/过滤/时间换算), 仅把「读法」换成 public 视图。

单一列清单来源: `CLEAN_VIEWS` —— 键=视图名, 值=(源表全限定名, 列清单)。
列清单由**对应 Clean 模型的 `__table__.columns`** 生成(见 `_cols`), 保证不漏列,
且与迁移 / conftest 视图重建逐字一致(改模型即自动同步)。

暴露列口径: 除「内部溯源列」`synced_at` / `raw_extra` / `updated_by_raw` 外全部保留;
`quality_status` / `quality_note` **必须保留**(analytics 质检清单/统计排除会读)。

⚠️ 只读: 视图不可写。analytics 侧的任何**写入**(如手动退款质检回写)必须落在
ods 源表(经对应写入模型, 见 `app.services.quality.manual_refund_review`),
切勿在这些 Clean 模型上 INSERT/UPDATE/DELETE。
⚠️ 不继承 QualityMixin / TimestampMixin: 避免引入未暴露字段; created_at / updated_at
按视图列显式声明为普通列。不声明 ForeignKey / relationship(视图无约束)。
⚠️ `__table_args__` 一律留空 → 落 public schema(视图位于 public.<view>)。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RefundClean(Base):
    """`public.refund_clean` 视图 (#322 方案A, over ods.refunds)。

    只暴露 analytics 实际消费的 21 列(其余 5 列 package_price / raw_extra /
    synced_at / updated_by_raw / quality_note 不对外), 口径与 refunds 逐字一致。
    """

    __tablename__ = "refund_clean"
    # 无 __table_args__: 默认 public schema

    refund_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="退费单号 RF...")
    platform_order_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="平台订单号 SP/WS/WX...")
    third_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="三方订单号")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备SN")
    refund_reason: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="退费原因")
    package_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐名称")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户名称")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退费金额")
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实退金额")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    status: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="状态:申请中/已退款/无需退款")
    refund_method: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="退款方式:线上/线下(导出数据可能含描述性文本)"
    )
    audit_remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="审核备注")
    auditor: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="审核人")
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="操作人:企微-XXX/sys/system")
    created_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原始创建时间")
    updated_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原始更新时间")
    actual_refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际扣款时间(E路refund_time回填, 对账用)"
    )
    quality_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="数据质量状态: None=正常 / ok / auto_fixed / pending / dup_residual(排除) / ignored(排除)",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class BmsOrderClean(Base):
    """`public.bms_order_clean` 视图 (#322 方案A → #339 Phase③ 改挂 std_bms_order)。

    #339 Phase③: 源由 ods.bms_orders(31列) 改为 std_bms_order(精简物理表 19 列,
    每日 03:40 batch job rebuild_std_big 重建)。列审计(2026-10-21): 被删的
    payable_price / wx_order_no / order_result / order_status_raw / last_stored_time /
    mch_id / business_id / before_serve_valid_date / after_serve_valid_date / days
    在 analytics 侧零消费, 采集/元数据/质量探针仍走 ods 源表不受影响。
    """

    __tablename__ = "bms_order_clean"

    order_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="订单号")
    order_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="订单类型(预存/储值卡充值等)")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备SN")
    combo_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐名称")
    combo_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="套餐价格(元)")
    actual_pay_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实际支付价格(元)")
    balance_pay_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="余额支付(元)")
    pay_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="支付时间")
    pay_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="支付类型(1微信支付等)")
    order_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="订单状态(0未支付/1已支付)")
    create_user: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人(WeChat/Wallet/SYS)")
    create_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="创建时间(同步锚点)")
    business_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户名称")
    device_type_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备类型")
    refund_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="退款状态")
    active_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="激活时间")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="微信商户名称")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class DeviceComboClean(Base):
    """`public.device_combo_clean` 视图 (#322 方案A, over ods.device_combos)。"""

    __tablename__ = "device_combo_clean"

    recharge_order_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="充值订单号")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备SN")
    package_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐名称")
    package_type: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="套餐类型:续费套餐")
    traffic_type: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="流量类型:无限/固定流量")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户名称")
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实际支付金额")
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="开始日期")
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="结束日期")
    updated_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原始更新时间")
    created_at_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="原始创建时间")
    valid_days: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="套餐有效天数")
    creator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人:WeChat/SYS/Wallet")
    available_traffic: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="可用流量(字节,-1=无限)")
    used_traffic: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="已用流量(字节)")
    remaining_traffic: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="剩余流量(字节)")
    status: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="当前状态:流量正常")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class CardComplaintClean(Base):
    """`public.card_complaint_clean` 视图 (#322 方案A, over ods.card_complaints)。"""

    __tablename__ = "card_complaint_clean"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    complaint_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="投诉日期(主时间字段)")
    complaint_group: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="投诉群")
    complainant: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="投诉人")
    complainant_phone: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="投诉手机号")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备号 SN")
    problem: Mapped[str | None] = mapped_column(Text, nullable=True, comment="投诉问题")
    problem_category: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="投诉问题分类(二级)")
    result: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="结果")
    solution: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="解决方案")
    handler: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="处理人(人员别名来源)")
    handle_result: Mapped[str | None] = mapped_column(Text, nullable=True, comment="处理结果")
    matched_ticket_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联服务工单号")
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="关联工单时间")
    dedup_hash: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="业务去重指纹(md5)")
    quality_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据质量状态")
    quality_note: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="质量说明")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class ExternalComplaintClean(Base):
    """`public.external_complaint_clean` 视图 (#322 方案A, over ods.external_complaints)。"""

    __tablename__ = "external_complaint_clean"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    complaint_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="投诉编号")
    complaint_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="投诉日期(主时间字段)")
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="平台")
    complaint_account: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="投诉账号")
    company_entity: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="公司主体")
    complaint_shop: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="投诉店铺")
    purchase_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="购买订单编号")
    complainant: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="投诉人")
    complainant_phone: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="投诉手机号")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="投诉设备号 SN")
    wechat_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="微信账户id")
    problem: Mapped[str | None] = mapped_column(Text, nullable=True, comment="投诉问题")
    keyword: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="投诉关键词")
    complaint_category: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="投诉分类")
    solution: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="解决方案")
    result: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="结果")
    follower: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="跟进人")
    handle_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="处理时间")
    handle_detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="处理明细")
    matched_ticket_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联服务工单号")
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="关联工单时间")
    dedup_hash: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="业务去重指纹(md5)")
    quality_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据质量状态")
    quality_note: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="质量说明")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class CsRecordClean(Base):
    """`public.cs_record_clean` 视图 (#322 方案A → #339 Phase③ 改挂 std_cs_record)。

    #339 Phase③: 源由 ods.cs_records(16列) 改为 std_cs_record(精简物理表 12 列,
    每日 03:40 batch job rebuild_std_big 重建)。列审计(2026-10-21): 被删的
    send_content / problem_create_time / last_consult_time / updated_at 在
    analytics 侧零消费, 采集/元数据/质量探针仍走 ods 源表不受影响。
    """

    __tablename__ = "cs_record_clean"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    problem_category: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="问题大类")
    problem_type: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="问题类型")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备号 SN")
    solution: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="方案")
    operation_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="操作时间(主时间字段)")
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="操作人:企微-XXX")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="说明")
    execution_result: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="执行结果说明")
    user_accepted: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="用户是否接受")
    consult_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="咨询时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class WalletWithdrawalClean(Base):
    """`public.wallet_withdrawal_clean` 视图 (#322 方案A, over ods.wallet_withdrawals)。"""

    __tablename__ = "wallet_withdrawal_clean"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    account_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="账户ID")
    wallet_balance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="钱包余额")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="SN")
    op_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="操作类型:用户提现/订单退费转入")
    op_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="操作金额")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注(含退费单号 RF...)")
    op_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="操作时间")
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="操作人员:SYS/用户提现")
    actual_refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际扣款时间(E路refund_time回填, 对账用)"
    )
    quality_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据质量状态")
    quality_note: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="质量说明")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class DeviceFlowUsageClean(Base):
    """`public.device_flow_usage_clean` 视图 (#322 方案A, over ods.device_flow_usage)。"""

    __tablename__ = "device_flow_usage_clean"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备SN")
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="商户ID(businessId)")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户名称")
    device_type_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备类型")
    device_group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备组")
    device_combo_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="当前套餐")
    stat_month: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="统计月份 YYYY-MM")
    flow_gb: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True, comment="该月流量(GB)")
    month_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True, comment="是否完整月")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="本次抓取时间")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class ManualRefundClean(Base):
    """`public.manual_refund_clean` 视图 (#322 方案A, over ods.manual_refunds)。

    ⚠️ 只读: 手动退款**质检回写**(quality_status/quality_note 及字段修正)不可写视图,
    必须经 ods 源表(见 `app.services.quality.manual_refund_review`)。
    """

    __tablename__ = "manual_refund_clean"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    register_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="登记日期")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备号")
    order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="订单号")
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="商户ID(微信商户号)")
    order_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="订单金额")
    registrant: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="登记人")
    is_refunded: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="是否已退款(是/否)")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退款金额")
    refund_operator: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="退款操作人")
    op_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="操作时间")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    actual_refund_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="实际扣款时间(E路refund_time回填, 对账用)"
    )
    quality_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据质量状态")
    quality_note: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="质量说明")
    register_time_raw: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="登记日期原始值")
    backfill_refund_record_report_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="op_time 回填来源 refund_record_reports.id")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class RefundRecordReportClean(Base):
    """`public.refund_record_report_clean` 视图 (#322 方案A, over ods.refund_record_reports)。

    排除内部溯源列 raw_extra / synced_at。
    """

    __tablename__ = "refund_record_report_clean"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    platform_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="平台订单号")
    wx_order_no: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="微信订单号")
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="商户号(对账锚点)")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户名")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备号(对账锚点)")
    product_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商品名称")
    refund_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="退款时间")
    apply_refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="申请退款金额(元)")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="退款金额(元)")
    refund_status: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="退款状态")
    backfill_op_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="回填到 D 路 op_time 的时间")
    backfill_manual_refund_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="已回填的 manual_refunds.id")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


class ShanmaoRefundClean(Base):
    """`public.shanmao_refund_clean` 视图 (#322 方案A, over ods.shanmao_refunds)。

    排除内部溯源列 raw_extra。
    """

    __tablename__ = "shanmao_refund_clean"

    refund_no: Mapped[str] = mapped_column(String(64), primary_key=True, comment="闪猫退款单号(TF开头)")
    order_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联订单号")
    device_sn: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="设备SN")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="手机号码")
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实际支付金额")
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="申请退款金额")
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实际退款金额")
    package_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="套餐/商品名称")
    merchant_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="商户名称")
    refund_reason: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="退款原因")
    refund_method: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="退款方式")
    status: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="状态:已退款/审核中/已驳回等")
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="系统导入时间")


def _cols(model: type[Base]) -> str:
    """由只读模型 `__table__.columns` 生成列名单(逗号分隔, 保证不漏列)。

    视图 DDL 与 conftest 视图重建共用此清单, 与迁移逐字一致。
    """
    return ", ".join(c.name for c in model.__table__.columns)


# 视图名 → (源表全限定名, 列清单)。refund_clean 亦纳入, 便于 conftest 统一处理
# (其迁移/模型本次不改, 列清单由 RefundClean 模型自动得出, 与旧迁移逐字一致)。
CLEAN_VIEWS: dict[str, tuple[str, str]] = {
    "refund_clean": ("ods.refunds", _cols(RefundClean)),
    "bms_order_clean": ("std_bms_order", _cols(BmsOrderClean)),  # #339 Phase③: 挂精简物理表
    "device_combo_clean": ("ods.device_combos", _cols(DeviceComboClean)),
    "card_complaint_clean": ("ods.card_complaints", _cols(CardComplaintClean)),
    "external_complaint_clean": ("ods.external_complaints", _cols(ExternalComplaintClean)),
    "cs_record_clean": ("std_cs_record", _cols(CsRecordClean)),  # #339 Phase③: 挂精简物理表
    "wallet_withdrawal_clean": ("ods.wallet_withdrawals", _cols(WalletWithdrawalClean)),
    "device_flow_usage_clean": ("ods.device_flow_usage", _cols(DeviceFlowUsageClean)),
    "manual_refund_clean": ("ods.manual_refunds", _cols(ManualRefundClean)),
    "refund_record_report_clean": ("ods.refund_record_reports", _cols(RefundRecordReportClean)),
    "shanmao_refund_clean": ("ods.shanmao_refunds", _cols(ShanmaoRefundClean)),
}
