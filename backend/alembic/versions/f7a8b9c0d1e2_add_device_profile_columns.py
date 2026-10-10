"""add_device_profile_columns

BMS 设备管理档案扩展(P4, A-20260905-001 / #18): devices 主表加列
- main_merchant_name 主商户名称
- active_flag 是否激活
- service_valid_until 服务有效期至
- current_combo_name 当前套餐
- start_iccid / virtual_iccid ICCID
- app_version / region / batch_no
全部可空(只增不改, 兼容现有引用)。

Revision ID: f7a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-05 19:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('main_merchant_name', sa.String(length=128), nullable=True, comment='主商户名称'))
    op.add_column('devices', sa.Column('active_flag', sa.String(length=16), nullable=True, comment='是否激活'))
    op.add_column('devices', sa.Column('service_valid_until', sa.DateTime(timezone=True), nullable=True, comment='服务有效期至'))
    op.add_column('devices', sa.Column('current_combo_name', sa.String(length=128), nullable=True, comment='当前套餐'))
    op.add_column('devices', sa.Column('start_iccid', sa.String(length=64), nullable=True, comment='启动卡ICCID'))
    op.create_index('ix_devices_start_iccid', 'devices', ['start_iccid'])
    op.add_column('devices', sa.Column('virtual_iccid', sa.String(length=64), nullable=True, comment='虚拟卡ICCID'))
    op.add_column('devices', sa.Column('app_version', sa.String(length=64), nullable=True, comment='应用版本'))
    op.add_column('devices', sa.Column('region', sa.String(length=128), nullable=True, comment='区域'))
    op.add_column('devices', sa.Column('batch_no', sa.String(length=64), nullable=True, comment='批次号'))
    op.create_index('ix_devices_batch_no', 'devices', ['batch_no'])


def downgrade() -> None:
    op.drop_index('ix_devices_batch_no', table_name='devices')
    op.drop_column('devices', 'batch_no')
    op.drop_column('devices', 'region')
    op.drop_column('devices', 'app_version')
    op.drop_column('devices', 'virtual_iccid')
    op.drop_index('ix_devices_start_iccid', table_name='devices')
    op.drop_column('devices', 'start_iccid')
    op.drop_column('devices', 'current_combo_name')
    op.drop_column('devices', 'service_valid_until')
    op.drop_column('devices', 'active_flag')
    op.drop_column('devices', 'main_merchant_name')
