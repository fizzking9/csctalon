"""add cumulative column to analysis_views

Revision ID: x3c4d5e6f7a9
Revises: w2b3c4d5e6f7
Create Date: 2026-07-31

为分析视图增加 cumulative JSONB 列,支持累计差额(池子水位),
基于已合并的 inflow/outflow 度量在合并后按时间维累计求和。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'x3c4d5e6f7a9'
down_revision: Union[str, None] = 'w2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'analysis_views',
        sa.Column(
            'cumulative',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment='累计差额(池子水位)数组 [{alias,inflow,outflow}]',
        ),
    )


def downgrade() -> None:
    op.drop_column('analysis_views', 'cumulative')
