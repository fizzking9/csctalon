"""add_manual_refund_aux_columns

手动退款登记表 manual_refunds 加辅助列(#20, 现金流回填/数据修复溯源):
- register_time_raw               登记日期原始值(纠错前保留; 正常行=NULL)
- backfill_refund_record_report_id op_time 回填来源 refund_record_reports.id(双向溯源)
全部可空(只增不改)。

Revision ID: a1b2c3d4e5f8
Revises: f7a8b9c0d1e2
Create Date: 2026-09-05 22:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'a1b2c3d4e5f8'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('manual_refunds', sa.Column(
        'register_time_raw', sa.DateTime(timezone=True), nullable=True,
        comment='登记日期原始值(纠错前保留, 正常行=NULL)',
    ))
    op.add_column('manual_refunds', sa.Column(
        'backfill_refund_record_report_id', sa.String(length=64), nullable=True,
        comment='op_time 回填来源 refund_record_reports.id',
    ))
    op.create_index('ix_manual_refunds_backfill_rrr_id', 'manual_refunds', ['backfill_refund_record_report_id'])


def downgrade() -> None:
    op.drop_index('ix_manual_refunds_backfill_rrr_id', table_name='manual_refunds')
    op.drop_column('manual_refunds', 'backfill_refund_record_report_id')
    op.drop_column('manual_refunds', 'register_time_raw')
