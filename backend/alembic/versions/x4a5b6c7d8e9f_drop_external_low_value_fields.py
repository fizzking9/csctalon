"""drop low-value external_complaint fields

Revision ID: x4a5b6c7d8e9f
Revises: x3c4d5e6f7a8
Create Date: 2026-08-20 11:30:00.000000

背景(字段保留分析结论):
315外部投诉表 8 个低价值字段填充率极低且可由系统按 SN 从其他表查询,
同步后不再保留,删除列以精简表结构:
- 套餐订单号/套餐名称/套餐价格: 可由 device_combos 按设备SN查询 (填充率 10~11%)
- 实际支付金额/协商退款金额: 可由 device_combos/refunds 按设备SN查询 (填充率 9~10%)
- 内部分析/外部分析: 基本为空 (3%)
- 接待客服: 基本为空 (1%)
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'x4a5b6c7d8e9f'
down_revision: Union[str, None] = 'x3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DROP_COLS = [
    'package_order_no',
    'package_name',
    'package_price',
    'actual_pay_amount',
    'negotiate_refund_amount',
    'internal_analysis',
    'external_analysis',
    'reception_cs',
]


def upgrade() -> None:
    for col in _DROP_COLS:
        op.drop_column('external_complaints', col)


def downgrade() -> None:
    import sqlalchemy as sa

    specs = {
        'package_order_no': sa.Column('package_order_no', sa.String(length=128), nullable=True, comment='套餐订单号'),
        'package_name': sa.Column('package_name', sa.String(length=128), nullable=True, comment='套餐名称'),
        'package_price': sa.Column('package_price', sa.Numeric(precision=12, scale=2), nullable=True, comment='套餐价格'),
        'actual_pay_amount': sa.Column('actual_pay_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='实际支付金额'),
        'negotiate_refund_amount': sa.Column('negotiate_refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='协商退款金额'),
        'internal_analysis': sa.Column('internal_analysis', sa.Text(), nullable=True, comment='内部分析'),
        'external_analysis': sa.Column('external_analysis', sa.Text(), nullable=True, comment='外部分析'),
        'reception_cs': sa.Column('reception_cs', sa.String(length=64), nullable=True, comment='接待客服'),
    }
    for col in _DROP_COLS:
        op.add_column('external_complaints', specs[col])
