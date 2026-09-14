"""merchant_refund pk wx_refund_no -> autoincrement id

Revision ID: z5e6f7a8b9c0
Revises: y4d5e6f7a8b0
Create Date: 2026-08-01

商户号退款表改主键:wx_refund_no → 自增 id。
原因:微信商户号导出的 Excel 中,微信退款单号被存成 float64 丢失精度(28 位单号截断),
且支付行(交易状态=SUCCESS)的 wx_refund_no 全为 0,无法作唯一主键。
改为自增 id + 全量保序插入(仿 wallet_withdrawals),wx_refund_no 降级为普通可空字段保留原值,
新增 merchant_refund_no(商户退款单号,RF 前缀可靠业务键)供后续准确数据补全对接。
表当前 0 行,迁移零风险。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'z5e6f7a8b9c0'
down_revision: Union[str, None] = 'y4d5e6f7a8b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 删除 wx_refund_no 上的主键约束(表当前 0 行,安全)
    op.execute("ALTER TABLE merchant_refunds DROP CONSTRAINT merchant_refunds_pkey")
    # 2. 新增自增 id 并设为主键(BIGSERIAL 自动建序列 merchant_refunds_id_seq + 默认值)
    op.execute("ALTER TABLE merchant_refunds ADD COLUMN id BIGSERIAL")
    op.execute("ALTER TABLE merchant_refunds ADD CONSTRAINT merchant_refunds_pkey PRIMARY KEY (id)")
    # 3. wx_refund_no 降级为可空(原为 PK 即 NOT NULL)
    op.alter_column(
        'merchant_refunds', 'wx_refund_no',
        existing_type=sa.String(length=64),
        nullable=True,
    )
    # PK 隐式索引随约束删除已丢失,重建普通索引供查询
    op.create_index('ix_merchant_refunds_wx_refund_no', 'merchant_refunds', ['wx_refund_no'])
    # 4. 新增商户退款单号(可靠业务键,后续数据补全对接用)
    op.add_column(
        'merchant_refunds',
        sa.Column('merchant_refund_no', sa.String(length=64), nullable=True, comment='商户退款单号(RF前缀,可靠业务键)'),
    )
    op.create_index('ix_merchant_refunds_merchant_refund_no', 'merchant_refunds', ['merchant_refund_no'])


def downgrade() -> None:
    op.drop_index('ix_merchant_refunds_merchant_refund_no', table_name='merchant_refunds')
    op.drop_column('merchant_refunds', 'merchant_refund_no')
    op.drop_index('ix_merchant_refunds_wx_refund_no', table_name='merchant_refunds')
    op.alter_column(
        'merchant_refunds', 'wx_refund_no',
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.execute("ALTER TABLE merchant_refunds DROP CONSTRAINT merchant_refunds_pkey")
    # DROP COLUMN id 会自动删除 BIGSERIAL 关联的序列 merchant_refunds_id_seq
    op.execute("ALTER TABLE merchant_refunds DROP COLUMN id")
    op.execute("ALTER TABLE merchant_refunds ADD CONSTRAINT merchant_refunds_pkey PRIMARY KEY (wx_refund_no)")
