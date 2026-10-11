"""combo_backfill_slices: device_combo 历史回补断点台账 (#299)

Revision ID: f299combo
Revises: e1_renew_probe_at
Create Date: 2026-10-11

设备套餐历史切片回补(#299): 按北京业务日记录每个导出切片的执行状态,
支持分夜断点续跑 / 触顶折半(split) / 失败次夜重试。纯新增台账表,
不触碰 device_combos 业务表, 零风险。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'f299combo'
down_revision: Union[str, None] = 'e1_renew_probe_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'combo_backfill_slices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False, comment='切片起始北京业务日'),
        sa.Column('end_date', sa.Date(), nullable=False, comment='切片结束北京业务日(含)'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='pending/running/success/empty/failed/split'),
        sa.Column('total_rows', sa.Integer(), nullable=True, comment='导出总行数(触顶判据)'),
        sa.Column('inserted_rows', sa.Integer(), nullable=True, comment='upsert 新增行数'),
        sa.Column('updated_rows', sa.Integer(), nullable=True, comment='upsert 更新行数'),
        sa.Column('failed_rows', sa.Integer(), nullable=True, comment='解析失败行数'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0', comment='累计尝试次数'),
        sa.Column('last_error', sa.Text(), nullable=True, comment='最近一次失败原因(截断)'),
        sa.Column('triggered_by', sa.String(length=32), nullable=True, comment='scheduled/manual'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('combo_backfill_slices_pkey')),
        sa.UniqueConstraint('start_date', 'end_date', name='uq_combo_backfill_slice_range'),
    )
    op.create_index(op.f('ix_combo_backfill_slices_start_date'), 'combo_backfill_slices', ['start_date'])
    op.create_index(op.f('ix_combo_backfill_slices_end_date'), 'combo_backfill_slices', ['end_date'])
    op.create_index(op.f('ix_combo_backfill_slices_status'), 'combo_backfill_slices', ['status'])


def downgrade() -> None:
    op.drop_index(op.f('ix_combo_backfill_slices_status'), table_name='combo_backfill_slices')
    op.drop_index(op.f('ix_combo_backfill_slices_end_date'), table_name='combo_backfill_slices')
    op.drop_index(op.f('ix_combo_backfill_slices_start_date'), table_name='combo_backfill_slices')
    op.drop_table('combo_backfill_slices')
