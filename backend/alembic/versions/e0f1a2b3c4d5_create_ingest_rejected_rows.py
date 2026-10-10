"""create ingest_rejected_rows table

Revision ID: e0f1a2b3c4d5
Revises: c9d8e7f6a5b4
Create Date: 2026-09-08 19:15:00.000000

背景(A-20260908-003 LOCKED):
外部数据统一质量治理 —— 承接所有入口"从未进业务表"的未采纳行,
按行显示 + 人工判定(open/keep/discard), 未判定/不处理默认不纳入统计。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'e0f1a2b3c4d5'
down_revision: Union[str, None] = 'c9d8e7f6a5b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ingest_rejected_rows',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('data_type', sa.String(length=64), nullable=False, comment='目标业务表类型'),
        sa.Column('source', sa.String(length=32), nullable=False, comment='入口标识'),
        sa.Column('source_ref', sa.String(length=128), nullable=True, comment='溯源'),
        sa.Column('source_row_no', sa.Integer(), nullable=True, comment='源文件/批次内行号'),
        sa.Column('reject_category', sa.String(length=32), nullable=False, comment='拒因分类'),
        sa.Column('reject_reason', sa.Text(), nullable=True, comment='人读原因'),
        sa.Column('raw_row', sa.dialects.postgresql.JSONB(), nullable=True, comment='原始整行'),
        sa.Column('business_key', sa.String(length=128), nullable=True, comment='业务键'),
        sa.Column('status', sa.String(length=16), server_default='open', nullable=False, comment='人工状态: open/keep/discard'),
        sa.Column('reviewed_by', sa.String(length=64), nullable=True, comment='判定人'),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True, comment='判定时间'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ingest_rejected_rows_data_type', 'ingest_rejected_rows', ['data_type'])
    op.create_index('ix_ingest_rejected_rows_source', 'ingest_rejected_rows', ['source'])
    op.create_index('ix_ingest_rejected_rows_reject_category', 'ingest_rejected_rows', ['reject_category'])
    op.create_index('ix_ingest_rejected_rows_status', 'ingest_rejected_rows', ['status'])
    op.create_index('ix_ingest_rejected_rows_business_key', 'ingest_rejected_rows', ['business_key'])


def downgrade() -> None:
    op.drop_index('ix_ingest_rejected_rows_business_key', table_name='ingest_rejected_rows')
    op.drop_index('ix_ingest_rejected_rows_status', table_name='ingest_rejected_rows')
    op.drop_index('ix_ingest_rejected_rows_reject_category', table_name='ingest_rejected_rows')
    op.drop_index('ix_ingest_rejected_rows_source', table_name='ingest_rejected_rows')
    op.drop_index('ix_ingest_rejected_rows_data_type', table_name='ingest_rejected_rows')
    op.drop_table('ingest_rejected_rows')
