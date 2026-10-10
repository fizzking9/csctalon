"""governance_findings 执行层字段 (工单 #165, 设计 §六/§八)

分级执行（设计 §六）需要 finding 记住「谁确认、何时执行、执行结果」：
- `confirmed_by` / `confirmed_at` —— 人工确认（L2 红线：未确认不得执行）
- `executed_at` / `execute_result` —— 执行时刻与凭证（影响行数/前后对比/备份表名/复现 SQL）

⚠️ 不新增 `governance_backups` 表：备份是**真实存在的物理表**
（`gov_bak_<表>_<finding_id>`，CREATE TABLE AS SELECT），表名与行数记在
`execute_result.backup_table` / `backup_rows` 里即可审计，无需再建一层索引表。

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'p6q7r8s9t0u1'
down_revision: Union[str, None] = 'o5p6q7r8s9t0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("governance_findings", sa.Column(
        "confirmed_by", sa.String(length=64), nullable=True,
        comment="人工确认人（L2 执行前置，设计 §六 红线）"))
    op.add_column("governance_findings", sa.Column(
        "confirmed_at", sa.DateTime(timezone=True), nullable=True, comment="人工确认时刻"))
    op.add_column("governance_findings", sa.Column(
        "executed_at", sa.DateTime(timezone=True), nullable=True, comment="执行时刻"))
    op.add_column("governance_findings", sa.Column(
        "execute_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
        comment="{action, auto_level, affected_rows, before, after, backup_table, backup_rows, sql, cache}"))


def downgrade() -> None:
    op.drop_column("governance_findings", "execute_result")
    op.drop_column("governance_findings", "executed_at")
    op.drop_column("governance_findings", "confirmed_at")
    op.drop_column("governance_findings", "confirmed_by")
