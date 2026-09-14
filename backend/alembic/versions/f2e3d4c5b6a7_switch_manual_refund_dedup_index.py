"""switch manual_refunds dedup index to COALESCE expression

原部分唯一索引 (register_time, order_no, refund_amount) 对 refund_amount=NULL 的行
不生效(PG 唯一索引 NULL 互不相等), 同一订单同日退款金额为空的行重复导入会插入重复行。
改为表达式索引 (register_time, order_no, COALESCE(refund_amount, 0)), 空金额归一为 0 参与去重。

Revision ID: f2e3d4c5b6a7
Revises: f1e2d3c4b5a6
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f2e3d4c5b6a7'
down_revision: Union[str, None] = 'f1e2d3c4b5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('uq_manual_refunds_register_order_amount', table_name='manual_refunds')
    op.create_index(
        'uq_manual_refunds_register_order_amount',
        'manual_refunds',
        [sa.text('register_time'), sa.text('order_no'), sa.text('COALESCE(refund_amount, 0)')],
        unique=True,
        postgresql_where=sa.text('order_no IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_manual_refunds_register_order_amount', table_name='manual_refunds')
    op.create_index(
        'uq_manual_refunds_register_order_amount',
        'manual_refunds',
        ['register_time', 'order_no', 'refund_amount'],
        unique=True,
        postgresql_where=sa.text('order_no IS NOT NULL'),
    )
