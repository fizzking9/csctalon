"""create manual_refund_sync_logs table

手动退款(商户号手机退款处理登记)推送同步日志:
  记录每次 workbuddy 将手动退款明细以 JSON push 到本系统的操作结果:
  total/inserted/updated/failed/warnings + error 摘要。

Revision ID: h1i2j3k4l5m6
Revises: g1a2b3c4d5e6
Create Date: 2026-08-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'h1i2j3k4l5m6'
down_revision: Union[str, None] = 'g1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'manual_refund_sync_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('source_file', sa.String(length=256), nullable=True, comment='推送来源文件名'),
        sa.Column('sync_source', sa.String(length=32), nullable=False, comment='同步来源: workbuddy/manual'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='success/failed/partial'),
        sa.Column('total', sa.Integer(), nullable=True, comment='收到总行数'),
        sa.Column('inserted', sa.Integer(), nullable=True, comment='新增行数'),
        sa.Column('updated', sa.Integer(), nullable=True, comment='更新行数'),
        sa.Column('failed', sa.Integer(), nullable=True, comment='失败行数(硬失败被丢弃)'),
        sa.Column('warnings', sa.Integer(), nullable=True, comment='软警告数(字段被清洗但行保留)'),
        sa.Column('error', sa.Text(), nullable=True, comment='错误摘要'),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录更新时间'),
        sa.PrimaryKeyConstraint('id'),
        comment='手动退款推送同步日志 - workbuddy 推送一次 = 一行记录',
    )
    op.create_index('ix_manual_refund_sync_logs_status', 'manual_refund_sync_logs', ['status'])
    op.create_index('ix_manual_refund_sync_logs_sync_source', 'manual_refund_sync_logs', ['sync_source'])


def downgrade() -> None:
    op.drop_index('ix_manual_refund_sync_logs_sync_source', table_name='manual_refund_sync_logs')
    op.drop_index('ix_manual_refund_sync_logs_status', table_name='manual_refund_sync_logs')
    op.drop_table('manual_refund_sync_logs')
