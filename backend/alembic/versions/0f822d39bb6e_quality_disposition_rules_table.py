"""quality disposition rules table (A-20260909-001)

Revision ID: 0f822d39bb6e
Revises: e0f1a2b3c4d5
Create Date: 2026-09-09 10:45:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0f822d39bb6e'
down_revision: Union[str, None] = 'e0f1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建 quality_disposition_rules 表(质量问题治理处置规则, A-20260909-001 §2.4)"""
    op.create_table(
        'quality_disposition_rules',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='自增主键'),
        sa.Column('table_code', sa.String(length=32), nullable=False,
                  comment='作用表: refund/wallet/shanmao/manual_refund/ticket'),
        sa.Column('trigger_category', sa.String(length=32), nullable=False,
                  comment='触发问题分类: time_future/time_past/time_reversed/enum_dirty/amount_invalid/missing_field/e_mismatch/residual/dup/ad_overlap_dup'),
        sa.Column('name', sa.String(length=255), nullable=False, comment='规则名(人读)'),
        sa.Column('match_pattern', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='特征匹配条件(表字段级, jsonb)'),
        sa.Column('disposition', sa.String(length=24), nullable=False,
                  comment='处置动作: fix_field/ignore/backfill_from_e/confirm_ok'),
        sa.Column('disposition_params', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='动作参数: 如 fix_field 的 {field, mode}; backfill_from_e 的匹配键'),
        sa.Column('auto_level', sa.String(length=12), nullable=False, server_default='suggest',
                  comment='自动级别: auto/suggest'),
        sa.Column('confidence', sa.Integer(), nullable=False, server_default='60',
                  comment='置信度 0-100(命中次数越多越高)'),
        sa.Column('hit_count', sa.Integer(), nullable=False, server_default='0',
                  comment='命中计数(学习信号)'),
        sa.Column('last_hit_at', sa.DateTime(timezone=True), nullable=True, comment='最后命中时间'),
        sa.Column('source_rule_id', sa.BigInteger(), nullable=True, comment='由哪条人工处置沉淀而来(溯源)'),
        sa.Column('source_ref', sa.String(length=255), nullable=True,
                  comment='沉淀来源(如 manual_refund:id=123 处置时生成)'),
        sa.Column('note', sa.Text(), nullable=True, comment='规则说明'),
        sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人'),
        sa.Column('updated_by', sa.String(length=64), nullable=True, comment='最后更新人'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true'),
                  comment='是否启用'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_quality_disposition_rules_table_code'), 'quality_disposition_rules',
                    ['table_code'], unique=False)
    op.create_index(op.f('ix_quality_disposition_rules_trigger_category'), 'quality_disposition_rules',
                    ['trigger_category'], unique=False)
    op.create_index(op.f('ix_quality_disposition_rules_disposition'), 'quality_disposition_rules',
                    ['disposition'], unique=False)
    op.create_index(op.f('ix_quality_disposition_rules_auto_level'), 'quality_disposition_rules',
                    ['auto_level'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_quality_disposition_rules_auto_level'), table_name='quality_disposition_rules')
    op.drop_index(op.f('ix_quality_disposition_rules_disposition'), table_name='quality_disposition_rules')
    op.drop_index(op.f('ix_quality_disposition_rules_trigger_category'), table_name='quality_disposition_rules')
    op.drop_index(op.f('ix_quality_disposition_rules_table_code'), table_name='quality_disposition_rules')
    op.drop_table('quality_disposition_rules')
