"""create manual_refunds table

手动退款登记(商户号手机退款处理登记):
  register_time   登记日期(客服登记退款时间)
  device_sn       设备号
  order_no        订单号
  merchant_id     商户ID(微信商户号)
  order_amount    订单金额
  registrant      登记人
  is_refunded     是否已退款(是/否)
  refund_amount   退款金额
  refund_operator 退款操作人
  op_time         操作时间(实际退款操作时间)
  remark          备注

业务去重键: (register_time, order_no, refund_amount) 部分唯一索引(同一订单可分期多次退款,
按登记日期+订单号+金额定位,重复导入 on_conflict 更新)。

Revision ID: f0e1d2c3b4a5
Revises: e1f2a3b4c5d6
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f0e1d2c3b4a5'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'manual_refunds',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('register_time', sa.DateTime(timezone=True), nullable=True, comment='登记日期(客服登记退款时间)'),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备号'),
        sa.Column('order_no', sa.String(length=128), nullable=True, comment='订单号'),
        sa.Column('merchant_id', sa.String(length=64), nullable=True, comment='商户ID(微信商户号)'),
        sa.Column('order_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='订单金额'),
        sa.Column('registrant', sa.String(length=64), nullable=True, comment='登记人'),
        sa.Column('is_refunded', sa.String(length=8), nullable=True, comment='是否已退款(是/否)'),
        sa.Column('refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='退款金额'),
        sa.Column('refund_operator', sa.String(length=64), nullable=True, comment='退款操作人(实际执行退款的人)'),
        sa.Column('op_time', sa.DateTime(timezone=True), nullable=True, comment='操作时间(实际退款操作时间)'),
        sa.Column('remark', sa.Text(), nullable=True, comment='备注'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录更新时间'),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('id'),
        comment='手动退款登记(商户号手机退款处理登记) - 客服在商户号后台手动操作退款台账',
    )
    op.create_index('ix_manual_refunds_register_time', 'manual_refunds', ['register_time'])
    op.create_index('ix_manual_refunds_device_sn', 'manual_refunds', ['device_sn'])
    op.create_index('ix_manual_refunds_order_no', 'manual_refunds', ['order_no'])
    op.create_index('ix_manual_refunds_merchant_id', 'manual_refunds', ['merchant_id'])
    op.create_index('ix_manual_refunds_is_refunded', 'manual_refunds', ['is_refunded'])
    op.create_index('ix_manual_refunds_registrant', 'manual_refunds', ['registrant'])
    op.create_index('ix_manual_refunds_refund_operator', 'manual_refunds', ['refund_operator'])
    op.create_index('ix_manual_refunds_op_time', 'manual_refunds', ['op_time'])
    # 业务去重键: 登记日期+订单号+退款金额(同一订单可分期多次退款)
    op.create_index(
        'uq_manual_refunds_register_order_amount',
        'manual_refunds',
        ['register_time', 'order_no', 'refund_amount'],
        unique=True,
        postgresql_where=sa.text('order_no IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_manual_refunds_register_order_amount', table_name='manual_refunds')
    op.drop_index('ix_manual_refunds_op_time', table_name='manual_refunds')
    op.drop_index('ix_manual_refunds_refund_operator', table_name='manual_refunds')
    op.drop_index('ix_manual_refunds_registrant', table_name='manual_refunds')
    op.drop_index('ix_manual_refunds_is_refunded', table_name='manual_refunds')
    op.drop_index('ix_manual_refunds_merchant_id', table_name='manual_refunds')
    op.drop_index('ix_manual_refunds_order_no', table_name='manual_refunds')
    op.drop_index('ix_manual_refunds_device_sn', table_name='manual_refunds')
    op.drop_index('ix_manual_refunds_register_time', table_name='manual_refunds')
    op.drop_table('manual_refunds')
