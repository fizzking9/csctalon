"""add quality columns to manual_refunds

数据质量审核支持:
  quality_status   ok=正常 / auto_fixed=已自动纠错 / pending=待人工确认
  quality_note     质量说明(纠错或待确认原因, 含原始值)

Revision ID: f1e2d3c4b5a6
Revises: f0e1d2c3b4a5
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f1e2d3c4b5a6'
down_revision: Union[str, None] = 'f0e1d2c3b4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('manual_refunds', sa.Column(
        'quality_status', sa.String(length=16), nullable=True,
        comment='数据质量状态: ok=正常 / auto_fixed=已自动纠错 / pending=待人工确认'))
    op.add_column('manual_refunds', sa.Column(
        'quality_note', sa.String(length=512), nullable=True,
        comment='质量说明(自动纠错或待确认原因, 含原始值)'))
    op.create_index('ix_manual_refunds_quality_status', 'manual_refunds', ['quality_status'])


def downgrade() -> None:
    op.drop_index('ix_manual_refunds_quality_status', table_name='manual_refunds')
    op.drop_column('manual_refunds', 'quality_note')
    op.drop_column('manual_refunds', 'quality_status')
