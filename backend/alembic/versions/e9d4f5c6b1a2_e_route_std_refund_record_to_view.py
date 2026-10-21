"""E 路 std_refund_record 物理表 → 视图 (全量采纳 + SN 清洗)

Revision ID: e9d4f5c6b1a2
Revises: e8b9c4d1f2a3
Create Date: 2026-10-21

#338 E 路采纳率 100%, 物理化零收益; 改为视图保留唯一差异化价值
  (ltrim(device_sn, chr(39)) 去 Excel 文本前导单引号清洗)。

  std_refund_record_all → ods.refund_record_reports (不变, 全量镜像)
  std_refund_record    → 视图, 嵌入 SN 清洗, 等价 E 路物理表的业务消费面

视图列与原物理表严格对齐 (含 adopt_rule_ver / adopted_at, 视图侧透传 ods 原值)。
消费方 StdRefundRecord ORM 直接 SELECT 视图, 代码零改动。
写入侧 refund_adoption.py 的 E 路 TRUNCATE + INSERT 改为跳过。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9d4f5c6b1a2"
down_revision: Union[str, None] = "e8b9c4d1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_VIEW_DDL = """\
CREATE VIEW std_refund_record AS
SELECT
    id,
    platform_order_no,
    wx_order_no,
    merchant_id,
    merchant_name,
    ltrim(device_sn, chr(39)) AS device_sn,
    product_name,
    refund_time,
    apply_refund_amount,
    refund_amount,
    refund_status,
    backfill_op_time,
    backfill_manual_refund_id,
    created_at,
    updated_at,
    adopt_rule_ver,
    adopted_at
FROM ods.refund_record_reports
WHERE adopt_state = 'adopted'
"""


def upgrade() -> None:
    # 1) 保留物理表数据备份 (万一 rollback 或审计需要)
    op.execute("CREATE TABLE std_refund_record_dropped_20261021 AS TABLE std_refund_record WITH DATA")
    # 2) 原表建的索引对视图无用, 全部删除
    op.drop_index("ix_std_refund_record_rtime", table_name="std_refund_record")
    op.drop_index("ix_std_refund_record_sn", table_name="std_refund_record")
    op.drop_index("ix_std_refund_record_mid", table_name="std_refund_record")
    # 3) 物理表 → 视图
    op.drop_table("std_refund_record")
    op.execute(_VIEW_DDL)


def downgrade() -> None:
    # 视图 → 物理表 (从备份恢复)
    op.execute("DROP VIEW IF EXISTS std_refund_record")
    op.execute("CREATE TABLE std_refund_record AS TABLE std_refund_record_dropped_20261021 WITH DATA")
    op.create_index("ix_std_refund_record_rtime", "std_refund_record", ["refund_time"])
    op.create_index("ix_std_refund_record_sn", "std_refund_record", ["device_sn"])
    op.create_index("ix_std_refund_record_mid", "std_refund_record", ["merchant_id"])
    # 备份表保留到下次人工清理 (rollback 幂等)
