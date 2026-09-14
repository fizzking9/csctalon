"""rebuild manual_refunds dedup key

去重键从 (登记日期, 订单号, 退款金额) 改为 (登记日期, 订单号, 设备号, 登记人):
- 设备号+订单号+登记日期+登记人 即可确定一笔退款单; 退款金额/状态被修改时应更新同一行,
  而不是因金额变化产生新行(旧行残留导致 D 路被历史数据抬高)。
- 先按新键清理现有重复行(每组保留最新 updated_at 行), 再替换唯一索引。
- 同步日志表补充 deleted 列(全量对账删除行数)。

Revision ID: m7n8o9p0q1r2
Revises: h1i2j3k4l5m6
Create Date: 2026-08-24
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'm7n8o9p0q1r2'
down_revision: Union[str, None] = 'h1i2j3k4l5m6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 清理按新键重复的旧行: 同组(登记日期+订单号+设备号+登记人)保留最新更新的一行
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY (register_time AT TIME ZONE 'Asia/Shanghai')::date,
                                    order_no,
                                    COALESCE(device_sn, ''),
                                    COALESCE(registrant, '')
                       ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
                   ) AS rn
            FROM manual_refunds
            WHERE order_no IS NOT NULL
        )
        DELETE FROM manual_refunds
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
    """))
    # 2) 替换唯一索引: 退款金额不再参与去重(金额修改走 UPDATE)
    #    表达式列必须整体再包一层括号: ((... AT TIME ZONE ...)::date)
    op.drop_index('uq_manual_refunds_register_order_amount', table_name='manual_refunds')
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_manual_refunds_dedup
        ON manual_refunds (
            ((register_time AT TIME ZONE 'Asia/Shanghai')::date),
            order_no,
            COALESCE(device_sn, ''),
            COALESCE(registrant, '')
        )
        WHERE order_no IS NOT NULL
    """))
    # 3) 同步日志增加全量对账删除行数
    op.add_column(
        'manual_refund_sync_logs',
        sa.Column('deleted', sa.Integer(), nullable=True, comment='全量对账删除行数'),
    )


def downgrade() -> None:
    op.drop_column('manual_refund_sync_logs', 'deleted')
    op.drop_index('uq_manual_refunds_dedup', table_name='manual_refunds')
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_manual_refunds_register_order_amount
        ON manual_refunds (register_time, order_no, COALESCE(refund_amount, (0)::numeric))
        WHERE order_no IS NOT NULL
    """))
