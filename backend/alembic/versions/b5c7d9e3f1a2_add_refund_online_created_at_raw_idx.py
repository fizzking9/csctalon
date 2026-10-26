# -*- coding: utf-8 -*-
"""ix_std_refund_online_created_at_raw

#344 P2b: std_refund_online.created_at_raw 缺索引, refund_decision_overview 等
多 SQL 依赖该列做窗口过滤(WHERE created_at_raw BETWEEN :ts AND :te), EXPLAIN
实测 Seq Scan 读 2220 buffer —— 4.4 万行被过滤。补索引后预期 Bitmap Index Scan。

注意: CREATE INDEX CONCURRENTLY 不能在事务块内跑(Alembic 默认 transactional DDL),
必须用 `op.get_context().autocommit_block()` 把外层事务提交后再执行。

⚠️ 2026-10-26 空库重放修复: 原实现用 `op.execute(..., execution_options={"autocommit": True})`,
   但该写法**不起作用** —— `autocommit` 是 SQLAlchemy 1.x 的旧执行选项, 2.0 已移除;
   且单独设 statement 级选项也无法逸出 Alembic 已开启的外层事务。
   实测报错: `CREATE INDEX CONCURRENTLY cannot run inside a transaction block`
   → 整条迁移链回滚, 空库起飞失败(CI #211 空库门禁与本地新环境同受影响)。
   `autocommit_block()` 才是 Alembic 官方支持的做法: 它会 commit 外层事务,
   执行块内语句(在 AUTOCOMMIT 连接上), 然后自动重新开启事务。downgrade 同理。
"""

from alembic import op

revision = "b5c7d9e3f1a2"
down_revision = "0dce885e5a06"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_std_refund_online_created_at_raw "
            "ON std_refund_online (created_at_raw)"
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "ix_std_refund_online_created_at_raw"
        )
