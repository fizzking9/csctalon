"""add ratios column to analysis_views

Revision ID: g9c3d5e7f9a0
Revises: f8b2c4d6e7a8
Create Date: 2026-07-26 21:00:00.000000

为分析视图增加 ratios JSONB 列,支持比率度量(挽回率/成功率/占比等),
基于已聚合的分子/分母度量在合并后计算。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'g9c3d5e7f9a0'
down_revision: Union[str, None] = 'f8b2c4d6e7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'analysis_views',
        sa.Column(
            'ratios',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment='比率度量数组 [{alias,numerator,denominator,as_percent}]',
        ),
    )


def downgrade() -> None:
    op.drop_column('analysis_views', 'ratios')
