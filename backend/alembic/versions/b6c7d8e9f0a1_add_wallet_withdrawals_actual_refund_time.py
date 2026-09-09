"""add wallet_withdrawals actual_refund_time (用户提现实际扣款时间, E路回填, 对账用)

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-09-09 15:40:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, None] = 'a5b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """wallet_withdrawals 新增 actual_refund_time(用户提现实际扣款时间)。

    2026-09-09 对账时间对齐: B(用户提现)按 SN+金额+时间窗 在 E(refund_record_reports)
    唯一匹配, 回填其 refund_time; 对账/现金流按此归日消除 op_time vs E 扣款错位。
    """
    op.add_column('wallet_withdrawals', sa.Column(
        'actual_refund_time', sa.DateTime(timezone=True), nullable=True,
        comment='实际扣款时间(E路refund_time按SN+金额+时间窗回填, 对账用; 操作时间=op_time不变)'
    ))
    op.create_index('ix_wallet_withdrawals_actual_refund_time', 'wallet_withdrawals', ['actual_refund_time'])


def downgrade() -> None:
    """回滚: 删列"""
    op.drop_index('ix_wallet_withdrawals_actual_refund_time', table_name='wallet_withdrawals')
    op.drop_column('wallet_withdrawals', 'actual_refund_time')
