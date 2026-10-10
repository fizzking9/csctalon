"""wallet_withdrawals 部分唯一索引(device_sn NULL 场景) (工单 #161)

背景（C-P1-7 / #160 事故的治理）：
原唯一约束 `uq_wallet_withdrawals_business` 覆盖 6 列
(account_id, op_type, op_time, operator, op_amount, device_sn)，但 **PostgreSQL 唯一约束对
含 NULL 的行不去重**（NULL ≠ NULL）→ device_sn 为 NULL 的提现记录完全不受约束保护。

后果（#160 实测）：高频同步（每 30 分钟一轮）对同一笔 device_sn 为空的提现反复插入，
累积 **40603 行重复**（其中 38458 行是「提现申请-冻结」的失败提现），全表从 28852 膨胀到 69455。

本迁移补一个**部分唯一索引**覆盖该场景（device_sn IS NULL 时用 5 列去重）：
- `NULLS NOT DISTINCT`（PG15+）保证即便 account_id/op_time/operator/op_amount 中也有 NULL，
  仍按「NULL 相等」去重（只对部分索引内的行生效）；
- device_sn 非 NULL 的行继续由原 6 列约束保护，两条路径互不干扰。

⚠️ 执行前置条件：表中不能已存在重复（否则建索引失败）。#160 已完成清理（重复组 = 0）。
   若在其它环境执行遇到 duplicate key，请先按 #160 的去重 SQL 清理。

Revision ID: h3i4j5k6l7m8
Revises: g2h3i4j5k6l7
Create Date: 2026-09-19
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'h3i4j5k6l7m8'
down_revision: Union[str, None] = 'g2h3i4j5k6l7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 部分唯一索引：只覆盖 device_sn IS NULL 的行，5 列 + NULLS NOT DISTINCT
    op.execute(
        """
        CREATE UNIQUE INDEX uq_wallet_withdrawals_null_sn
        ON wallet_withdrawals (account_id, op_type, op_time, operator, op_amount)
        NULLS NOT DISTINCT
        WHERE device_sn IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_wallet_withdrawals_null_sn")
