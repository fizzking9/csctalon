# -*- coding: utf-8 -*-
"""ix_std_refund_online_created_at_raw

#344 P2b: std_refund_online.created_at_raw 缺索引, refund_decision_overview 等
多 SQL 依赖该列做窗口过滤(WHERE created_at_raw BETWEEN :ts AND :te), EXPLAIN
实测 Seq Scan 读 2220 buffer —— 4.4 万行被过滤。补索引后预期 Bitmap Index Scan。

注意: CREATE INDEX CONCURRENTLY 不能在事务块内跑(Alembic 默认 transactional DDL),
必须用 execution_options={"autocommit": True} 让 ALTER TABLE/CREATE INDEX 在
AUTOCOMMIT 模式执行。downgrade 同理。
"""

from alembic import op

revision = "b5c7d9e3f1a2"
down_revision = "0dce885e5a06"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_std_refund_online_created_at_raw "
        "ON std_refund_online (created_at_raw)",
        execution_options={"autocommit": True},
    )


def downgrade():
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS "
        "ix_std_refund_online_created_at_raw",
        execution_options={"autocommit": True},
    )
