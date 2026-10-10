"""add refunds actual_refund_time (A路实际扣款时间, E路回填, 对账用)

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-09-09 15:55:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'b6c7d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """refunds 新增 actual_refund_time(线上直退实际扣款时间)。

    2026-09-09 对账时间对齐: A(线上直退)按 platform_order_no 在 E(refund_record_reports)
    唯一匹配回填其 refund_time; 对账/现金流按此归日消除 created_at_raw 跨日边界错位。
    """
    op.add_column('refunds', sa.Column(
        'actual_refund_time', sa.DateTime(timezone=True), nullable=True,
        comment='实际扣款时间(E路refund_time按platform_order_no回填, 对账用; 创建时间=created_at_raw不变)'
    ))
    op.create_index('ix_refunds_actual_refund_time', 'refunds', ['actual_refund_time'])


def downgrade() -> None:
    """回滚: 删列"""
    op.drop_index('ix_refunds_actual_refund_time', table_name='refunds')
    op.drop_column('refunds', 'actual_refund_time')
