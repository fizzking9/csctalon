"""create reconcile_matches

对账复核逐笔勾稽结果表 reconcile_matches (A-20260906-002 v1.1 LOCKED / #22):
- E 水单(refund_record_reports)每笔 → 自动归类(auto_route) + 候选经营单(matched_ref)
  + 人工确认状态(confirm_status), 仅留痕不改任何源表
- check_id FK → reconcile_checks.id(批次, 可空=草稿); (check_id, report_id) 唯一
- report_id FK → refund_record_reports.id

Revision ID: c0a1b2c3d4e7
Revises: c0a1b2c3d4e6
Create Date: 2026-09-06 18:40:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'c0a1b2c3d4e7'
down_revision: Union[str, None] = 'c0a1b2c3d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'reconcile_matches',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('check_id', sa.Integer(), nullable=True, comment='关联复核批次(reconcile_checks.id, 可空=草稿未成批)'),
        sa.Column('report_id', sa.Integer(), nullable=False, comment='E 退款记录报表 id(勾稽基准)'),
        sa.Column('platform_order_no', sa.String(length=128), nullable=True, comment='E 笔平台订单号快照'),
        sa.Column('refund_amount', sa.Numeric(12, 2), nullable=True, comment='E 笔退款金额快照(元)'),
        sa.Column('refund_time', sa.DateTime(timezone=True), nullable=True, comment='E 笔退款时间快照'),
        sa.Column('auto_route', sa.String(length=16), nullable=True, comment='自动归类 A/B/C/D/unmatched(_classify_report)'),
        sa.Column('matched_ref', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='候选匹配经营单 [{route,source,source_id,ref_no,amount}]'),
        sa.Column('confirm_status', sa.String(length=16), nullable=True, comment='人工确认状态: 已核对一致/差异已确认/存疑待查/已排除重复/待核对'),
        sa.Column('confirm_note', sa.Text(), nullable=True, comment='人工备注(差异原因等)'),
        sa.Column('confirmed_by', sa.Integer(), nullable=True, comment='确认人 user id'),
        sa.Column('confirmed_name', sa.String(length=64), nullable=True, comment='确认人姓名'),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True, comment='确认时间(UTC aware)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['check_id'], ['reconcile_checks.id'], name='fk_reconcile_matches_check'),
        sa.ForeignKeyConstraint(['report_id'], ['refund_record_reports.id'], name='fk_reconcile_matches_report'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('check_id', 'report_id', name='uq_reconcile_matches_check_report'),
        comment='对账复核逐笔勾稽(E→ABCD 单笔对应, 仅留痕不改源)',
    )
    op.create_index('ix_reconcile_matches_check_id', 'reconcile_matches', ['check_id'])
    op.create_index('ix_reconcile_matches_report_id', 'reconcile_matches', ['report_id'])
    op.create_index('ix_reconcile_matches_auto_route', 'reconcile_matches', ['auto_route'])
    op.create_index('ix_reconcile_matches_confirm_status', 'reconcile_matches', ['confirm_status'])


def downgrade() -> None:
    op.drop_index('ix_reconcile_matches_confirm_status', table_name='reconcile_matches')
    op.drop_index('ix_reconcile_matches_auto_route', table_name='reconcile_matches')
    op.drop_index('ix_reconcile_matches_report_id', table_name='reconcile_matches')
    op.drop_index('ix_reconcile_matches_check_id', table_name='reconcile_matches')
    op.drop_table('reconcile_matches')
