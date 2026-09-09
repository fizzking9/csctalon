"""add manual_refunds actual_refund_time (实际扣款时间, E路回填, 对账用; 不改 op_time 口径)

Revision ID: a5b6c7d8e9f0
Revises: 0f822d39bb6e
Create Date: 2026-09-09 15:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'a5b6c7d8e9f0'
down_revision: Union[str, None] = '0f822d39bb6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """manual_refunds 新增 actual_refund_time(实际扣款时间) — E(refund_record_reports.refund_time)逐单回填。

    用途(2026-09-09 对账时间对齐): 对账按"实际扣款时间"逐日匹配, 消除 D 路
    op_time(客服操作/登记时间)与 E 扣款时间的错位。op_time 保留运营口径不变。
    """
    op.add_column('manual_refunds', sa.Column(
        'actual_refund_time', sa.DateTime(timezone=True), nullable=True,
        comment='实际扣款时间(E路refund_record_reports.refund_time回填, 对账用; 客服操作时间=op_time不变)'
    ))
    op.create_index('ix_manual_refunds_actual_refund_time', 'manual_refunds', ['actual_refund_time'])


def downgrade() -> None:
    """回滚: 删列"""
    op.drop_index('ix_manual_refunds_actual_refund_time', table_name='manual_refunds')
    op.drop_column('manual_refunds', 'actual_refund_time')
