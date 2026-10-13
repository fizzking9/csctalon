"""#319 数据分层 M2: 21 张信源表从 public 移入 ods schema

表清单(与模型 __table_args__ {"schema": "ods"} 一一对应):
  退款/订单:  bms_orders, refunds, manual_refunds, merchant_refunds,
              shanmao_refunds, refund_record_reports, wallet_withdrawals
  设备/套餐:  device_combos, device_flow_usage
  投诉/客服:  card_complaints, external_complaints, cs_records
  SCRM:       scrm_sessions, scrm_session_transfers, scrm_operators, scrm_staff_profiles
  其他信源:   return_trackings, wechat_registrations, wechat_accounts,
              dept_info_syncs, attendance

实现要点:
- ALTER TABLE ... SET SCHEMA 是目录级元数据操作, 不搬数据, 秒级完成;
  索引/约束/序列/触发器随表一起迁移, 无需重建。
- public.<t> 上若有视图/物化视图依赖会被 PG 拒绝 —— 经清点 21 表均无视图依赖。
- 外键约束(含跨 schema 指向 ods 的 3 处: complaint_same_source_members.complaint_id /
  scrm_session_transfers.session_id / reconcile_matches.report_id)在 SET SCHEMA 后
  自动指向新位置, 无需重建。
- downgrade 反向移回 public, 逐表对称。

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-10-13 10:05:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: str = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 21 张信源表(顺序无关, SET SCHEMA 互不影响)
ODS_TABLES: tuple[str, ...] = (
    "bms_orders",
    "device_combos",
    "refunds",
    "manual_refunds",
    "merchant_refunds",
    "shanmao_refunds",
    "refund_record_reports",
    "card_complaints",
    "external_complaints",
    "cs_records",
    "scrm_sessions",
    "scrm_session_transfers",
    "scrm_operators",
    "scrm_staff_profiles",
    "wallet_withdrawals",
    "device_flow_usage",
    "return_trackings",
    "wechat_registrations",
    "wechat_accounts",
    "dept_info_syncs",
    "attendance",
)


def upgrade() -> None:
    for table in ODS_TABLES:
        op.execute(f"ALTER TABLE public.{table} SET SCHEMA ods")


def downgrade() -> None:
    for table in ODS_TABLES:
        op.execute(f"ALTER TABLE ods.{table} SET SCHEMA public")
