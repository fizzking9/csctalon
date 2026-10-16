"""#332 退款五路数据标准层: 采纳治理列 + 5 张 std 标准表 + E 路企业商户维表

内容:
1. 五路 ods 表(refunds/wallet_withdrawals/shanmao_refunds/manual_refunds/
   refund_record_reports)加采纳治理四列 adopt_state/adopt_reason/adopt_rule_ver/
   adopted_at, 并建 adopted/excluded 部分索引(采纳作业按状态批量过滤用,
   ORM 不镜像这两组纯性能索引)。
2. 建 5 张 public.std_* 物理标准表: 列 = 对应 public.*_clean 视图业务列的物理化
   (类型/精度逐一对齐); std_wallet_flow +flow_dir(inflow/outflow),
   std_refund_manual +source_grade(默认 'manual'), 五表统一 +adopt_rule_ver/adopted_at。
3. 建 dim_merchant_group(商户号→集团, 仅人工维护)并随迁移写入云客猫系 3 个商户号
   (业务 2026-10-16 确认); 建 dim_device_merchant(E 路 SN→企业商户时间分段)。
4. 建 adoption_overrides 人工覆盖留痕表, 唯一 (route, src_pk) 保留最新生效覆盖。

权威口径: docs/standards/退款五路数据标准与采纳规则.md
ORM: app.models.refund_standard / dim_merchant / adoption_mixin

Revision ID: f332a01b2c3d
Revises: c3d4e5f6a7b8
Create Date: 2026-10-16 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.dim_merchant import DimMerchantGroup

# revision identifiers, used by Alembic.
revision: str = "f332a01b2c3d"
down_revision: str = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 五路 ods 表
_ODS_TABLES = (
    "refunds",
    "wallet_withdrawals",
    "shanmao_refunds",
    "manual_refunds",
    "refund_record_reports",
)


def _adopt_columns() -> tuple[sa.Column, ...]:
    """每次返回全新 Column 实例(同一列定义要加给 5 张表, 不可共享对象)。"""
    return (
        sa.Column("adopt_state", sa.String(length=16), nullable=True,
                  comment="采纳三态: adopted/excluded/pending; NULL=未跑过采纳作业"),
        sa.Column("adopt_reason", sa.String(length=32), nullable=True,
                  comment="规则码(机器结论)"),
        sa.Column("adopt_rule_ver", sa.String(length=32), nullable=True,
                  comment="采纳规则版本(首版 r20261016_v1)"),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True,
                  comment="本次采纳判定时间"),
    )


# 云客猫系商户号 → 集团(业务 2026-10-16 确认, 普查: 3 ID 共用 SN 249 个)
_YUNKE_MAO_SEED = [
    {"merchant_id": "1611876437", "merchant_name": "深圳市云客猫科技有限公司",
     "group_name": "云客猫", "mapping_basis": "business_confirmed_20261016", "updated_by": "system"},
    {"merchant_id": "1675918225", "merchant_name": "大秦WiFi管家",
     "group_name": "云客猫", "mapping_basis": "business_confirmed_20261016", "updated_by": "system"},
    {"merchant_id": "1683343734", "merchant_name": "云客猫",
     "group_name": "云客猫", "mapping_basis": "business_confirmed_20261016", "updated_by": "system"},
]


def upgrade() -> None:
    # ── 1. 五路 ods 原表加采纳治理四列 + adopted/excluded 部分索引 ──────────
    for table in _ODS_TABLES:
        for col in _adopt_columns():
            op.add_column(table, col, schema="ods")
        op.create_index(
            f"ix_{table}_adopt_adopted", table, ["adopt_state"], schema="ods",
            postgresql_where=sa.text("adopt_state = 'adopted'"),
        )
        op.create_index(
            f"ix_{table}_adopt_excluded", table, ["adopt_state"], schema="ods",
            postgresql_where=sa.text("adopt_state = 'excluded'"),
        )

    # ── 2a. A 路 std_refund_online (over ods.refunds, 对齐 refund_clean 21 列) ──
    op.create_table(
        "std_refund_online",
        sa.Column("refund_no", sa.String(length=64), nullable=False),
        sa.Column("platform_order_no", sa.String(length=64), nullable=True),
        sa.Column("third_order_no", sa.String(length=128), nullable=True),
        sa.Column("device_sn", sa.String(length=64), nullable=True),
        sa.Column("refund_reason", sa.String(length=128), nullable=True),
        sa.Column("package_name", sa.String(length=128), nullable=True),
        sa.Column("merchant_name", sa.String(length=128), nullable=True),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("actual_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("refund_method", sa.String(length=255), nullable=True),
        sa.Column("audit_remark", sa.Text(), nullable=True),
        sa.Column("auditor", sa.String(length=64), nullable=True),
        sa.Column("operator", sa.String(length=64), nullable=True),
        sa.Column("created_at_raw", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at_raw", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_refund_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_status", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adopt_rule_ver", sa.String(length=32), nullable=True),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("refund_no"),
    )
    op.create_index("ix_std_refund_online_artime", "std_refund_online", ["actual_refund_time"])
    op.create_index("ix_std_refund_online_sn", "std_refund_online", ["device_sn"])
    op.create_index("ix_std_refund_online_status", "std_refund_online", ["status"])

    # ── 2b. B 路 std_wallet_flow (over ods.wallet_withdrawals) + flow_dir ─────
    op.create_table(
        "std_wallet_flow",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=True),
        sa.Column("wallet_balance", sa.Numeric(12, 2), nullable=True),
        sa.Column("device_sn", sa.String(length=64), nullable=True),
        sa.Column("op_type", sa.String(length=32), nullable=True),
        sa.Column("op_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("op_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operator", sa.String(length=64), nullable=True),
        sa.Column("actual_refund_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_status", sa.String(length=16), nullable=True),
        sa.Column("quality_note", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("flow_dir", sa.String(length=8), nullable=False,
                  comment="inflow=进水(订单退费转入) / outflow=出水(扣费/成功提现)"),
        sa.Column("adopt_rule_ver", sa.String(length=32), nullable=True),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_std_wallet_flow_optime", "std_wallet_flow", ["op_time"])
    op.create_index("ix_std_wallet_flow_sn", "std_wallet_flow", ["device_sn"])
    op.create_index("ix_std_wallet_flow_dir", "std_wallet_flow", ["flow_dir"])

    # ── 2c. C 路 std_refund_shanmao (over ods.shanmao_refunds) ────────────────
    op.create_table(
        "std_refund_shanmao",
        sa.Column("refund_no", sa.String(length=64), nullable=False),
        sa.Column("order_no", sa.String(length=64), nullable=True),
        sa.Column("device_sn", sa.String(length=64), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("paid_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("actual_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("package_name", sa.String(length=128), nullable=True),
        sa.Column("merchant_name", sa.String(length=128), nullable=True),
        sa.Column("refund_reason", sa.String(length=255), nullable=True),
        sa.Column("refund_method", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("applicant", sa.String(length=64), nullable=True),
        sa.Column("auditor", sa.String(length=64), nullable=True),
        sa.Column("operator", sa.String(length=64), nullable=True),
        sa.Column("apply_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("audit_remark", sa.Text(), nullable=True),
        sa.Column("quality_status", sa.String(length=16), nullable=True),
        sa.Column("quality_note", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adopt_rule_ver", sa.String(length=32), nullable=True),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("refund_no"),
    )
    op.create_index("ix_std_refund_shanmao_rtime", "std_refund_shanmao", ["refund_time"])
    op.create_index("ix_std_refund_shanmao_sn", "std_refund_shanmao", ["device_sn"])
    op.create_index("ix_std_refund_shanmao_status", "std_refund_shanmao", ["status"])

    # ── 2d. D 路 std_refund_manual (over ods.manual_refunds) + source_grade ───
    op.create_table(
        "std_refund_manual",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("register_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_sn", sa.String(length=64), nullable=True),
        sa.Column("order_no", sa.String(length=128), nullable=True),
        sa.Column("merchant_id", sa.String(length=64), nullable=True),
        sa.Column("order_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("registrant", sa.String(length=64), nullable=True),
        sa.Column("is_refunded", sa.String(length=8), nullable=True),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("refund_operator", sa.String(length=64), nullable=True),
        sa.Column("op_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("actual_refund_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_status", sa.String(length=16), nullable=True),
        sa.Column("quality_note", sa.String(length=512), nullable=True),
        sa.Column("register_time_raw", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_refund_record_report_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_grade", sa.String(length=16), nullable=False,
                  server_default=sa.text("'manual'"), comment="来源可信度: manual=人工登记"),
        sa.Column("adopt_rule_ver", sa.String(length=32), nullable=True),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_std_refund_manual_artime", "std_refund_manual", ["actual_refund_time"])
    op.create_index("ix_std_refund_manual_sn", "std_refund_manual", ["device_sn"])
    op.create_index("ix_std_refund_manual_refunded", "std_refund_manual", ["is_refunded"])

    # ── 2e. E 路 std_refund_record (over ods.refund_record_reports, 全量采纳) ─
    op.create_table(
        "std_refund_record",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform_order_no", sa.String(length=128), nullable=True),
        sa.Column("wx_order_no", sa.String(length=128), nullable=True),
        sa.Column("merchant_id", sa.String(length=64), nullable=True),
        sa.Column("merchant_name", sa.String(length=128), nullable=True),
        sa.Column("device_sn", sa.String(length=64), nullable=True),
        sa.Column("product_name", sa.String(length=128), nullable=True),
        sa.Column("refund_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apply_refund_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("refund_status", sa.String(length=32), nullable=True),
        sa.Column("backfill_op_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_manual_refund_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adopt_rule_ver", sa.String(length=32), nullable=True),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_std_refund_record_rtime", "std_refund_record", ["refund_time"])
    op.create_index("ix_std_refund_record_sn", "std_refund_record", ["device_sn"])
    op.create_index("ix_std_refund_record_mid", "std_refund_record", ["merchant_id"])

    # ── 3a. 企业集团人工映射 + 云客猫种子 ─────────────────────────────────────
    op.create_table(
        "dim_merchant_group",
        sa.Column("merchant_id", sa.String(length=64), nullable=False),
        sa.Column("merchant_name", sa.String(length=128), nullable=False),
        sa.Column("group_name", sa.String(length=128), nullable=False),
        sa.Column("mapping_basis", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("merchant_id"),
    )
    op.bulk_insert(DimMerchantGroup.__table__, _YUNKE_MAO_SEED)

    # ── 3b. SN→企业商户 时间分段(E 路全量重建, fake SN 不入表) ───────────────
    op.create_table(
        "dim_device_merchant",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_sn", sa.String(length=64), nullable=False),
        sa.Column("merchant_id", sa.String(length=64), nullable=True),
        sa.Column("merchant_name", sa.String(length=128), nullable=True),
        sa.Column("merchant_group", sa.String(length=128), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conflict_flag", sa.String(length=8), nullable=False,
                  server_default=sa.text("'none'"), comment="none/overlap(fake 不产出分段)"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_sn", "valid_from", name="uq_dim_device_merchant_sn_from"),
    )
    op.create_index(
        "ix_dim_device_merchant_sn_range", "dim_device_merchant", ["device_sn", "valid_to"]
    )
    op.create_index("ix_dim_device_merchant_conflict", "dim_device_merchant", ["conflict_flag"])

    # ── 4. 人工覆盖留痕(唯一 route+src_pk 保留最新生效覆盖) ──────────────────
    op.create_table(
        "adoption_overrides",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("route", sa.String(length=16), nullable=False),
        sa.Column("src_pk", sa.String(length=128), nullable=False),
        sa.Column("target_state", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("operator", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("route", "src_pk", name="uq_adoption_overrides_route_pk"),
    )


def downgrade() -> None:
    # 新建表(逆序)
    op.drop_table("adoption_overrides")
    op.drop_index("ix_dim_device_merchant_conflict", table_name="dim_device_merchant")
    op.drop_index("ix_dim_device_merchant_sn_range", table_name="dim_device_merchant")
    op.drop_table("dim_device_merchant")
    op.drop_table("dim_merchant_group")
    for std_table, idxs in (
        ("std_refund_record", ("rtime", "sn", "mid")),
        ("std_refund_manual", ("artime", "sn", "refunded")),
        ("std_refund_shanmao", ("rtime", "sn", "status")),
        ("std_wallet_flow", ("optime", "sn", "dir")),
        ("std_refund_online", ("artime", "sn", "status")),
    ):
        for suf in idxs:
            op.drop_index(f"ix_{std_table}_{suf}", table_name=std_table)
        op.drop_table(std_table)
    # ods 治理列(先删依赖列的部分索引)
    for table in _ODS_TABLES:
        op.drop_index(f"ix_{table}_adopt_excluded", table_name=table, schema="ods")
        op.drop_index(f"ix_{table}_adopt_adopted", table_name=table, schema="ods")
        for col_name in ("adopted_at", "adopt_rule_ver", "adopt_reason", "adopt_state"):
            op.drop_column(table, col_name, schema="ods")
