"""add phone and paid_amount to shanmao_refunds

为闪猫退款表新增两个字段:
  phone          手机号码(String(32),index)
  paid_amount    实际支付金额(Numeric(12,2))

Revision ID: a1b2c3d4e5f6
Revises: b8c9d0e1f2a3
Create Date: 2026-08-03 21:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'shanmao_refunds',
        sa.Column('phone', sa.String(length=32), nullable=True, comment='手机号码')
    )
    op.add_column(
        'shanmao_refunds',
        sa.Column('paid_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='实际支付金额')
    )
    op.create_index('ix_shanmao_refunds_phone', 'shanmao_refunds', ['phone'])


def downgrade() -> None:
    op.drop_index('ix_shanmao_refunds_phone', table_name='shanmao_refunds')
    op.drop_column('shanmao_refunds', 'paid_amount')
    op.drop_column('shanmao_refunds', 'phone')
