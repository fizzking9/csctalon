"""create shanmao_refunds table

闪猫退款管理独立数据表:
  refund_no        闪猫退款单号(主键)
  order_no         关联订单号
  device_sn        设备SN(FK→devices.sn)
  package_name     套餐名称
  merchant_name    商户名称
  refund_amount    申请退款金额
  actual_amount    实际退款金额
  refund_reason    退款原因
  status           退款状态
  refund_method    退款方式
  applicant        申请人
  apply_time       申请时间
  auditor          审核人
  audit_time       审核时间
  audit_remark     审核备注
  operator         操作人
  refund_time      退款完成时间
  remark           备注
  raw_extra        原始数据冗余(JSONB)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-07 14:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'shanmao_refunds',
        sa.Column('refund_no', sa.String(length=64), nullable=False, comment='闪猫退款单号'),
        sa.Column('order_no', sa.String(length=64), nullable=True, comment='关联订单号'),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备SN'),
        sa.Column('package_name', sa.String(length=128), nullable=True, comment='套餐名称'),
        sa.Column('merchant_name', sa.String(length=128), nullable=True, comment='商户名称'),
        sa.Column('refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='申请退款金额'),
        sa.Column('actual_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='实际退款金额'),
        sa.Column('refund_reason', sa.String(length=256), nullable=True, comment='退款原因'),
        sa.Column('status', sa.String(length=32), nullable=True, comment='退款状态'),
        sa.Column('refund_method', sa.String(length=32), nullable=True, comment='退款方式'),
        sa.Column('applicant', sa.String(length=64), nullable=True, comment='申请人'),
        sa.Column('apply_time', sa.DateTime(timezone=True), nullable=True, comment='申请时间'),
        sa.Column('auditor', sa.String(length=64), nullable=True, comment='审核人'),
        sa.Column('audit_time', sa.DateTime(timezone=True), nullable=True, comment='审核时间'),
        sa.Column('audit_remark', sa.String(length=512), nullable=True, comment='审核备注'),
        sa.Column('operator', sa.String(length=64), nullable=True, comment='操作人'),
        sa.Column('refund_time', sa.DateTime(timezone=True), nullable=True, comment='退款完成时间'),
        sa.Column('remark', sa.String(length=1024), nullable=True, comment='备注'),
        sa.Column('raw_extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='原始数据冗余'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录更新时间'),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('refund_no'),
        comment='闪猫退款管理独立表(数据源:闪猫BMS退款导出)',
    )
    op.create_index('ix_shanmao_refunds_order_no', 'shanmao_refunds', ['order_no'])
    op.create_index('ix_shanmao_refunds_device_sn', 'shanmao_refunds', ['device_sn'])
    op.create_index('ix_shanmao_refunds_apply_time', 'shanmao_refunds', ['apply_time'])
    op.create_index('ix_shanmao_refunds_status', 'shanmao_refunds', ['status'])
    op.create_index('ix_shanmao_refunds_merchant_name', 'shanmao_refunds', ['merchant_name'])


def downgrade() -> None:
    op.drop_index('ix_shanmao_refunds_merchant_name', table_name='shanmao_refunds')
    op.drop_index('ix_shanmao_refunds_status', table_name='shanmao_refunds')
    op.drop_index('ix_shanmao_refunds_apply_time', table_name='shanmao_refunds')
    op.drop_index('ix_shanmao_refunds_device_sn', table_name='shanmao_refunds')
    op.drop_index('ix_shanmao_refunds_order_no', table_name='shanmao_refunds')
    op.drop_table('shanmao_refunds')
