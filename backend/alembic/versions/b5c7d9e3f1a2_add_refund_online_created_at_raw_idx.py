# -*- coding: utf-8 -*-
"""ix_std_refund_online_created_at_raw

#344 P2b: std_refund_online.created_at_raw 缺索引, refund_decision_overview 等
多 SQL 依赖该列做窗口过滤(WHERE created_at_raw BETWEEN :ts AND :te), EXPLAIN
实测 Seq Scan 读 2220 buffer —— 4.4 万行被过滤。补索引后预期走 Bitmap Index Scan。

CREATE INDEX CONCURRENTLY 避免锁表(std_refund_online ≈ 4.9 万行, 小表但保持
生产友好纪律)。down 用 DROP INDEX CONCURRENTLY, 幂等: 索引不存在时忽略。

影响: refund_decision_overview, refund_type_trend 等 analytics 域接口。
"""
from alembic import op
import sqlalchemy as sa

revision = "b5c7d9e3f1a2"
down_revision = "0dce885e5a06"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_std_refund_online_created_at_raw "
        "ON std_refund_online (created_at_raw)"
    )


def downgrade():
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_std_refund_online_created_at_raw")
