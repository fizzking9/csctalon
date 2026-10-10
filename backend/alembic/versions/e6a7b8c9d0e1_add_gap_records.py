"""add_gap_records

新增采集缺口记账表 gap_records(A-20260904-002 LOCKED / 工单#2 3.3)。

一源一日一行(UNIQUE biz_date+data_type), 记录某业务日某数据源的采集缺口:
  - 同步失败/部分成功 → open/partial
  - 成功同步覆盖 → filled
  - 次日 5:00 清点只补真缺口, 补成功 → filled

Revision ID: e6a7b8c9d0e1
Revises: d4e5f6a7b8c9
Create Date: 2026-09-04 20:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'e6a7b8c9d0e1'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'gap_records',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('biz_date', sa.String(length=10), nullable=False, comment='业务日期 YYYY-MM-DD(北京时间)'),
        sa.Column('data_type', sa.String(length=64), nullable=False, comment='数据源类型(refund/ticket/wallet/cs_record...)'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='缺口状态:open/partial/filled/ignored'),
        sa.Column('first_gap_at', sa.DateTime(timezone=True), nullable=False, comment='首次发现缺口时间'),
        sa.Column('last_try_at', sa.DateTime(timezone=True), nullable=True, comment='末次补跑时间'),
        sa.Column('try_count', sa.BigInteger(), nullable=False, comment='已补跑尝试次数'),
        sa.Column('error', sa.Text(), nullable=True, comment='最近一次失败错误摘要'),
        sa.Column('source_range_start', sa.DateTime(timezone=True), nullable=True, comment='缺口时间范围起点'),
        sa.Column('source_range_end', sa.DateTime(timezone=True), nullable=True, comment='缺口时间范围终点'),
        sa.Column('remark', sa.String(length=500), nullable=True, comment='备注(如失败片清单摘要)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('biz_date', 'data_type', name='uq_gap_records_biz_date_type'),
    )
    op.create_index(op.f('ix_gap_records_biz_date'), 'gap_records', ['biz_date'], unique=False)
    op.create_index(op.f('ix_gap_records_data_type'), 'gap_records', ['data_type'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_gap_records_data_type'), table_name='gap_records')
    op.drop_index(op.f('ix_gap_records_biz_date'), table_name='gap_records')
    op.drop_table('gap_records')
