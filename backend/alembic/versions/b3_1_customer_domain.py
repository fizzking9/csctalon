"""B3-1 (#246): 客户域建表 - 四层主数据 + 设备→商户号 SCD 建链 + 设备类型维度

Revision ID: b3_1_customer_domain
Revises: b2_6_sync_watermark_split
Create Date: 2026-10-09 16:00:00.000000

依据: docs/arch/A-20261007-002-客户域主数据与设备360设计.md (LOCKED v1.0)
     排期 docs/reports/20261009-下一步开发计划-排期规划.md §六 B3-1

范围(只建结构, 不含回填/360 页面):
1. dim_device_type 设备类型维度表 + devices.device_type_code 可空外键
   (原始 device_type 文本保留, 只增不改不删)。
2. merchants 演进: merchant_id 业务键部分唯一索引(空值允许); 代理键沿用 id。
3. device_merchant_links: 设备→商户号 SCD Type2 建链(通路① 交易);
   部分唯一 (device_sn) WHERE is_current。
4. shops / enterprises 主数据(架构 §九-5 排期 v2 提前至 B3-1);
   shop_enterprise_history 店铺↔企业父子 SCD;
   device_shop_links 设备→店铺 SCD 建链(通路② 归属)。

铁律:
- 事实表不加外键、不回填历史; 原始文本列一律保留。
- 所有新表/列向后兼容(可空、不破坏现有采集与看板)。

downgrade: 全部为新表/可空新列, DROP 即安全回退, 无业务数据损失。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b3_1_customer_domain'
down_revision: Union[str, None] = 'b2_6_sync_watermark_split'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. 设备类型维度 + devices.device_type_code ─────────────────────
    op.create_table(
        'dim_device_type',
        sa.Column('type_code', sa.String(length=32), primary_key=True, comment='归一类型码(如 4G_CPE)'),
        sa.Column('type_name', sa.String(length=64), nullable=False, comment='类型中文名'),
        sa.Column('normalized_from', sa.JSON(), nullable=True, comment='归并来源原始文本列表(快照)'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active', comment='active/disabled'),
    )
    op.add_column(
        'devices',
        sa.Column('device_type_code', sa.String(length=32), nullable=True,
                  comment='归一设备类型码(#246 B3-1, 原始 device_type 文本保留)'),
    )
    op.create_foreign_key(
        'fk_devices_device_type_code', 'devices', 'dim_device_type',
        ['device_type_code'], ['type_code'],
    )
    op.create_index('ix_devices_device_type_code', 'devices', ['device_type_code'])

    # ── 2. merchants 演进: merchant_id 业务键部分唯一 ──────────────────
    op.create_index(
        'ix_merchants_merchant_id_unique', 'merchants', ['merchant_id'],
        unique=True, postgresql_where=sa.text('merchant_id IS NOT NULL'),
    )

    # ── 3. 设备→商户号 SCD 建链 ───────────────────────────────────────
    op.create_table(
        'device_merchant_links',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('device_sn', sa.String(length=64), sa.ForeignKey('devices.sn'), nullable=False, index=True),
        sa.Column('merchant_pk', sa.BigInteger(), sa.ForeignKey('merchants.id'), nullable=False, index=True),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('confidence', sa.Numeric(5, 4), nullable=True),
        sa.Column('match_source', sa.String(length=16), nullable=False),
        sa.Column('raw_merchant_name', sa.String(length=128), nullable=True),
        sa.Column('change_reason', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        'ix_dml_device_current', 'device_merchant_links', ['device_sn'],
        unique=True, postgresql_where=sa.text('is_current'),
    )
    op.create_index('ix_device_merchant_links_is_current', 'device_merchant_links', ['is_current'])

    # ── 4. 店铺 / 企业主数据 + 两条 SCD ────────────────────────────────
    op.create_table(
        'enterprises',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('raw_source', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'shops',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('shop_name', sa.String(length=128), nullable=False),
        sa.Column('enterprise_pk', sa.BigInteger(), sa.ForeignKey('enterprises.id'), nullable=True, index=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('raw_source', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'shop_enterprise_history',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('shop_pk', sa.BigInteger(), sa.ForeignKey('shops.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('enterprise_pk', sa.BigInteger(), sa.ForeignKey('enterprises.id'), nullable=False, index=True),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('source', sa.String(length=32), nullable=True),
        sa.Column('change_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        'ix_seh_shop_current', 'shop_enterprise_history', ['shop_pk'],
        unique=True, postgresql_where=sa.text('is_current'),
    )
    op.create_table(
        'device_shop_links',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('device_sn', sa.String(length=64), sa.ForeignKey('devices.sn'), nullable=False, index=True),
        sa.Column('shop_pk', sa.BigInteger(), sa.ForeignKey('shops.id'), nullable=False, index=True),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('match_source', sa.String(length=16), nullable=False),
        sa.Column('raw_source_text', sa.String(length=128), nullable=True),
        sa.Column('change_reason', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        'ix_dsl_device_current', 'device_shop_links', ['device_sn'],
        unique=True, postgresql_where=sa.text('is_current'),
    )
    op.create_index('ix_device_shop_links_is_current', 'device_shop_links', ['is_current'])


def downgrade() -> None:
    # 全部为新表/可空新列, DROP 即安全回退
    op.drop_index('ix_device_shop_links_is_current', table_name='device_shop_links')
    op.drop_index('ix_device_shop_links_shop_pk', table_name='device_shop_links')
    op.drop_index('ix_device_shop_links_device_sn', table_name='device_shop_links')
    op.drop_index('ix_dsl_device_current', table_name='device_shop_links', postgresql_where=sa.text('is_current'))
    op.drop_table('device_shop_links')

    op.drop_index('ix_seh_shop_current', table_name='shop_enterprise_history', postgresql_where=sa.text('is_current'))
    op.drop_table('shop_enterprise_history')

    op.drop_table('shops')
    op.drop_table('enterprises')

    op.drop_index('ix_device_merchant_links_is_current', table_name='device_merchant_links')
    op.drop_index('ix_device_merchant_links_merchant_pk', table_name='device_merchant_links')
    op.drop_index('ix_device_merchant_links_device_sn', table_name='device_merchant_links')
    op.drop_index('ix_dml_device_current', table_name='device_merchant_links', postgresql_where=sa.text('is_current'))
    op.drop_table('device_merchant_links')

    op.drop_index(
        'ix_merchants_merchant_id_unique', table_name='merchants',
        postgresql_where=sa.text('merchant_id IS NOT NULL'),
    )

    op.drop_index('ix_devices_device_type_code', table_name='devices')
    op.drop_constraint('fk_devices_device_type_code', 'devices', type_='foreignkey')
    op.drop_column('devices', 'device_type_code')
    op.drop_table('dim_device_type')
