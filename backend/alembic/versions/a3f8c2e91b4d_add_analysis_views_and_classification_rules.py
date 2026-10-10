"""add analysis_views and classification_rules

Revision ID: a3f8c2e91b4d
Revises: d9b7f1b89c1d
Create Date: 2026-07-26 18:00:00.000000

新增分析引擎核心表:
- analysis_views: 存储用户自定义/预设分析视图配置(segments JSONB)
- classification_rules: 存储动态分类规则(查询时转 CASE WHEN 注入 SQL)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'a3f8c2e91b4d'
down_revision: Union[str, None] = 'd9b7f1b89c1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # analysis_views
    op.create_table('analysis_views',
        sa.Column('code', sa.String(length=64), nullable=True, comment='预设视图代码(preset 才有,如 preset_cash_trend)'),
        sa.Column('name', sa.String(length=128), nullable=False, comment='视图名称'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('chart_type', sa.String(length=32), nullable=False, comment='line/bar/pie/table'),
        sa.Column('segments', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='数据源片段配置数组'),
        sa.Column('merge_strategy', sa.String(length=16), server_default='add', nullable=False, comment='add=分表聚合后相加'),
        sa.Column('classification_rule_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False, comment='引用的 ClassificationRule ID 列表'),
        sa.Column('time_range_default', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='默认时间范围 {days:30}'),
        sa.Column('is_preset', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='预设视图(迁移的4大看板)'),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False, comment='active/archived'),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_analysis_views_code'),
    )
    op.create_index(op.f('ix_analysis_views_code'), 'analysis_views', ['code'], unique=True)

    # classification_rules
    op.create_table('classification_rules',
        sa.Column('name', sa.String(length=128), nullable=False, comment='规则名称'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('rules', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='规则定义数组'),
        sa.Column('default_label', sa.String(length=64), server_default='其他', nullable=False, comment='不匹配时的默认标签'),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('classification_rules')
    op.drop_index(op.f('ix_analysis_views_code'), table_name='analysis_views')
    op.drop_table('analysis_views')
