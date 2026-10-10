"""create cash_flow_wide table

Revision ID: c9d8e7f6a5b4
Revises: 8a7b6c5d4e3f
Create Date: 2026-09-08 18:50:00.000000

背景(A-20260908-004 LOCKED):
现金流水统一宽表 cash_flow_wide —— ABCD 经营四路每笔出款统一物化, 路别列 route,
服务对账(A+B+C+D vs E)/快速响应/运营多维分析。E(refund_record_reports)不进本表。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c9d8e7f6a5b4'
down_revision: Union[str, None] = '8a7b6c5d4e3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'cash_flow_wide',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('route', sa.String(length=1), nullable=False, comment='路线: A=线上直退/B=钱包提现/C=闪猫退款/D=手动退款'),
        sa.Column('biz_no', sa.String(length=128), nullable=True, comment='业务单号(各表最强键,尽力而为)'),
        sa.Column('src_id', sa.String(length=128), nullable=False, comment='来源表主键(精确定位回源)'),
        sa.Column('pay_time', sa.DateTime(timezone=True), nullable=True, comment='出款时间(各路口径归一)'),
        sa.Column('pay_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='出款金额(各路口径归一)'),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备SN'),
        sa.Column('operator', sa.String(length=64), nullable=True, comment='操作人(归一别名)'),
        sa.Column('e_key', sa.String(length=128), nullable=True, comment='对账键(A=platform_order_no等)'),
        sa.Column('e_matched', sa.Boolean(), nullable=True, comment='该笔是否在E有匹配(True/False/None弱关联)'),
        sa.Column('reason', sa.String(length=255), nullable=True, comment='退款原因'),
        sa.Column('merchant', sa.String(length=128), nullable=True, comment='商户名称'),
        sa.Column('package', sa.String(length=128), nullable=True, comment='套餐名称'),
        sa.Column('has_external_complaint', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='该SN是否有外部投诉'),
        sa.Column('auditor', sa.String(length=64), nullable=True, comment='审核人/登记人'),
        sa.Column('matched_at', sa.DateTime(timezone=True), nullable=False, comment='物化构建时间'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cash_flow_wide_route', 'cash_flow_wide', ['route'])
    op.create_index('ix_cash_flow_wide_pay_time', 'cash_flow_wide', ['pay_time'])
    op.create_index('ix_cash_flow_wide_device_sn', 'cash_flow_wide', ['device_sn'])
    op.create_index('ix_cash_flow_wide_src_id', 'cash_flow_wide', ['src_id'])
    op.create_index('ix_cash_flow_wide_biz_no', 'cash_flow_wide', ['biz_no'])
    op.create_index('ix_cash_flow_wide_e_key', 'cash_flow_wide', ['e_key'])
    op.create_index('ix_cash_flow_wide_operator', 'cash_flow_wide', ['operator'])
    op.create_index('ix_cash_flow_wide_merchant', 'cash_flow_wide', ['merchant'])
    op.create_index('ix_cash_flow_wide_has_external_complaint', 'cash_flow_wide', ['has_external_complaint'])


def downgrade() -> None:
    op.drop_index('ix_cash_flow_wide_has_external_complaint', table_name='cash_flow_wide')
    op.drop_index('ix_cash_flow_wide_merchant', table_name='cash_flow_wide')
    op.drop_index('ix_cash_flow_wide_operator', table_name='cash_flow_wide')
    op.drop_index('ix_cash_flow_wide_e_key', table_name='cash_flow_wide')
    op.drop_index('ix_cash_flow_wide_biz_no', table_name='cash_flow_wide')
    op.drop_index('ix_cash_flow_wide_src_id', table_name='cash_flow_wide')
    op.drop_index('ix_cash_flow_wide_device_sn', table_name='cash_flow_wide')
    op.drop_index('ix_cash_flow_wide_pay_time', table_name='cash_flow_wide')
    op.drop_index('ix_cash_flow_wide_route', table_name='cash_flow_wide')
    op.drop_table('cash_flow_wide')
