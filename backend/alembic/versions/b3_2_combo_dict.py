"""B3-2 (#247): 套餐归一字典 - combo_dict + combo_name_alias

Revision ID: b3_2_combo_dict
Revises: b3_1_customer_domain
Create Date: 2026-10-09 16:30:00.000000

依据: docs/arch/A-20261007-002 §3.5 (LOCKED v1.0)
     排期 §六 B3-2 (S-P1-12)

范围:
1. combo_dict 归一套餐字典(combo_code PK, canonical_name, package_type, traffic_type, status)
2. combo_name_alias 原始名→归一码映射(SCD, 含 merchant_pk + 生效期)
   - 部分唯一索引 (raw_package_name, merchant_pk) WHERE is_current
   - NULLS NOT DISTINCT 使全局别名(merchant_pk NULL)同样受唯一约束

downgrade: 新表 DROP 即安全回退, 无业务数据损失。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b3_2_combo_dict'
down_revision: Union[str, None] = 'b3_1_customer_domain'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'combo_dict',
        sa.Column('combo_code', sa.String(length=64), primary_key=True, comment='归一套餐码(稳定键)'),
        sa.Column('canonical_name', sa.String(length=128), nullable=False, comment='归一标准名'),
        sa.Column('package_type', sa.String(length=32), nullable=True, comment='套餐类型'),
        sa.Column('traffic_type', sa.String(length=32), nullable=True, comment='流量类型'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active', comment='active/disabled'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'combo_name_alias',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('combo_code', sa.String(length=64), sa.ForeignKey('combo_dict.combo_code'), nullable=False, index=True),
        sa.Column('raw_package_name', sa.String(length=128), nullable=False),
        sa.Column('merchant_pk', sa.BigInteger(), sa.ForeignKey('merchants.id'), nullable=True, index=True),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('source', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        'ix_cna_raw_merchant_current', 'combo_name_alias',
        ['raw_package_name', 'merchant_pk'],
        unique=True,
        postgresql_where=sa.text('is_current'),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index('ix_combo_name_alias_is_current', 'combo_name_alias', ['is_current'])


def downgrade() -> None:
    op.drop_index('ix_combo_name_alias_is_current', table_name='combo_name_alias')
    op.drop_index(
        'ix_cna_raw_merchant_current', table_name='combo_name_alias',
        postgresql_where=sa.text('is_current'),
    )
    op.drop_table('combo_name_alias')
    op.drop_table('combo_dict')
