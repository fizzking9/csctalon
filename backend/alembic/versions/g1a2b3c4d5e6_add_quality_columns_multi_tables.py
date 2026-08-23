"""add quality columns to refunds/service_tickets/wallet_withdrawals/shanmao_refunds

通用数据质量审核(L2 自动校验 / L3 人工审核)推广到核心业务表:
  quality_status   None=正常 / ok=已人工确认 / auto_fixed=已自动纠错 / pending=待人工确认
  quality_note     质量说明(自动纠错或待确认原因, 含原始值)

Revision ID: g1a2b3c4d5e6
Revises: f2e3d4c5b6a7
Create Date: 2026-08-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'g1a2b3c4d5e6'
down_revision: Union[str, None] = 'f2e3d4c5b6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "refunds",
    "service_tickets",
    "wallet_withdrawals",
    "shanmao_refunds",
)


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(t, sa.Column(
            'quality_status', sa.String(length=16), nullable=True,
            comment='数据质量状态: None=正常 / ok=已人工确认 / auto_fixed=已自动纠错 / pending=待人工确认'))
        op.add_column(t, sa.Column(
            'quality_note', sa.String(length=512), nullable=True,
            comment='质量说明(自动纠错或待确认原因, 含原始值)'))
        op.create_index(f'ix_{t}_quality_status', t, ['quality_status'])


def downgrade() -> None:
    for t in reversed(_TABLES):
        op.drop_index(f'ix_{t}_quality_status', table_name=t)
        op.drop_column(t, 'quality_note')
        op.drop_column(t, 'quality_status')
