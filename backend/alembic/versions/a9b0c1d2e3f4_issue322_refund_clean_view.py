"""#322 方案A: 新建只读精简视图 public.refund_clean (over ods.refunds)

背景(#319/#322 数据分层):
- #319 已把退款信源表移入 ods schema; C2 契约要求 services/analytics/** 不得直读 ods 源表。
- 本迁移为 refund 域落地「方案A」: 建只读视图把 analytics 对 refunds 的读取全部切到
  public.refund_clean。**口径不变** —— 逐列取自 ods.refunds, 不新增任何加工。

暴露列(21 列, = analytics 实际消费集合):
  refund_no, platform_order_no, third_order_no, device_sn, refund_reason, package_name,
  merchant_name, refund_amount, actual_amount, remark, status, refund_method,
  audit_remark, auditor, operator, created_at_raw, updated_at_raw, actual_refund_time,
  quality_status, created_at, updated_at
不暴露的 5 列(明确无用/内部溯源, 不为切换扩表): package_price, raw_extra, synced_at,
  updated_by_raw, quality_note

写入不受影响: upsert(_refunds.py) 与 actual_refund_time 回填仍直写 ods.refunds(经 Refund 模型),
视图仅给 analytics 读。

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-10-14 00:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 21 列 DDL 片段(与 conftest 视图重建逐字一致, 改一处须同步另一处)
_REFUND_CLEAN_COLUMNS = (
    "refund_no, platform_order_no, third_order_no, device_sn, refund_reason, package_name, "
    "merchant_name, refund_amount, actual_amount, remark, status, refund_method, "
    "audit_remark, auditor, operator, created_at_raw, updated_at_raw, actual_refund_time, "
    "quality_status, created_at, updated_at"
)


def upgrade() -> None:
    op.execute(
        f"CREATE VIEW public.refund_clean AS SELECT {_REFUND_CLEAN_COLUMNS} FROM ods.refunds"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.refund_clean")
