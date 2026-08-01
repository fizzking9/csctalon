"""merchant_refund unique index (trade_time, merchant_order_no)

Revision ID: a6f7a8b9c1d0
Revises: z5e6f7a8b9c0
Create Date: 2026-08-01

为商户号退款表加 (trade_time, merchant_order_no) 部分唯一索引,支持 upsert 去重。
微信退款单号在 Excel 导出时被 float 截断不可作主键,改用 (交易时间, 商户订单号) 复合键
(已验证 86352 行零重复)。部分索引仅覆盖两列均非空的行,无键脏行不参与去重。
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a6f7a8b9c1d0'
down_revision: Union[str, None] = 'z5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 清理全空脏行(trade_time 和 merchant_order_no 均为 NULL,Excel 末尾残留,无法去重定位)
    op.execute(
        "DELETE FROM merchant_refunds WHERE trade_time IS NULL AND merchant_order_no IS NULL"
    )
    # 部分唯一索引:仅对两列均非空的行去重(NULL 不参与,与 upsert index_where 对齐)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_refunds_time_order "
        "ON merchant_refunds (trade_time, merchant_order_no) "
        "WHERE trade_time IS NOT NULL AND merchant_order_no IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_merchant_refunds_time_order")
