"""create reconcile_checks

对账复核批次表 reconcile_checks (A-20260906-002 LOCKED / #22):
- ABCD/E 五路手动对账复核留痕, 按时间窗批次存时点快照(JSONB)
- 仅留痕存档, 不参与任何统计聚合
- 唯一 check_no; reviewed_by FK users.id(可空)

Revision ID: c0a1b2c3d4e6
Revises: a1b2c3d4e5f8
Create Date: 2026-09-06 17:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'c0a1b2c3d4e6'
down_revision: Union[str, None] = 'a1b2c3d4e5f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'reconcile_checks',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('check_no', sa.String(length=32), nullable=False, comment='复核单号 RC+YYYYMMDD+序号'),
        sa.Column('window_start', sa.DateTime(timezone=True), nullable=False, comment='对账时间窗开始(UTC aware 存)'),
        sa.Column('window_end', sa.DateTime(timezone=True), nullable=False, comment='对账时间窗结束(UTC aware 存)'),
        sa.Column('grain', sa.String(length=8), nullable=False, comment='粒度 day/week'),
        sa.Column('route_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='五路合计+差异时点快照'),
        sa.Column('by_route_of_e', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='E 水单勾稽归属'),
        sa.Column('dupe_detect', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='D 路重复/脏数据检出'),
        sa.Column('conclusion', sa.String(length=16), nullable=True, comment='结论: 一致/差异已确认/存疑待查'),
        sa.Column('note', sa.Text(), nullable=True, comment='复核备注(差异原因等)'),
        sa.Column('reviewed_by', sa.Integer(), nullable=True, comment='复核人 user id'),
        sa.Column('reviewed_name', sa.String(length=64), nullable=True, comment='复核人姓名'),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True, comment='复核时间(UTC aware 存)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name='fk_reconcile_checks_reviewed_by'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('check_no', name='uq_reconcile_checks_check_no'),
        comment='对账复核批次(ABCD/E 五路, 仅留痕不参与统计)',
    )
    op.create_index('ix_reconcile_checks_check_no', 'reconcile_checks', ['check_no'])
    op.create_index('ix_reconcile_checks_window_start', 'reconcile_checks', ['window_start'])


def downgrade() -> None:
    op.drop_index('ix_reconcile_checks_window_start', table_name='reconcile_checks')
    op.drop_index('ix_reconcile_checks_check_no', table_name='reconcile_checks')
    op.drop_table('reconcile_checks')
