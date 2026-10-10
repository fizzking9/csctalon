"""drop refund_wide mr_* & ticket_finish_time dead fields

Revision ID: 8a7b6c5d4e3f
Revises: 47cadbf38a6f
Create Date: 2026-09-08 17:30:00.000000

背景(A-20260908-001 退费宽表精简重构, LOCKED):
全库核实 refund_wide 的 mr_*(商户退款关联)6 字段 + ticket_finish_time 零消费
(无任何 analysis_view/规则/接口引用), 属死字段, 精简删除:
- wx_refund_no / mr_trade_time / mr_refund_amount / mr_refund_status / mr_fee / merchant_id
- ticket_finish_time (89% 被 BMS 批量覆盖不可靠, 已在 UI 隐藏, 见 A-20260902-005)
同时删除唯一索引 ix_refund_wide_wx_refund_no。
注意: 不删 merchant_refunds 表 / matcher 关联(二者与宽表物化解耦, match.py 仍用)。
"""
from typing import Sequence, Union

from alembic import op

revision: str = '8a7b6c5d4e3f'
down_revision: Union[str, None] = '47cadbf38a6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DROP_COLS = [
    'wx_refund_no',
    'mr_trade_time',
    'mr_refund_amount',
    'mr_refund_status',
    'mr_fee',
    'merchant_id',
    'ticket_finish_time',
]


def upgrade() -> None:
    op.drop_index('ix_refund_wide_wx_refund_no', table_name='refund_wide')
    for col in _DROP_COLS:
        op.drop_column('refund_wide', col)


def downgrade() -> None:
    import sqlalchemy as sa

    specs = {
        'wx_refund_no': sa.Column('wx_refund_no', sa.String(length=64), nullable=True, comment='微信退款单号'),
        'mr_trade_time': sa.Column('mr_trade_time', sa.DateTime(timezone=True), nullable=True, comment='商户退款交易时间'),
        'mr_refund_amount': sa.Column('mr_refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='商户退款金额(微信侧真实退款)'),
        'mr_refund_status': sa.Column('mr_refund_status', sa.String(length=32), nullable=True, comment='商户退款状态'),
        'mr_fee': sa.Column('mr_fee', sa.Numeric(precision=12, scale=4), nullable=True, comment='商户退款手续费'),
        'merchant_id': sa.Column('merchant_id', sa.String(length=64), nullable=True, comment='商户号'),
        'ticket_finish_time': sa.Column('ticket_finish_time', sa.DateTime(timezone=True), nullable=True, comment='工单完成时间'),
    }
    for col in _DROP_COLS:
        op.add_column('refund_wide', specs[col])
    op.create_index('ix_refund_wide_wx_refund_no', 'refund_wide', ['wx_refund_no'])
