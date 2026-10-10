"""add complaint match & workbuddy sync support

Revision ID: x2b3c4d5e6f7
Revises: 64a584531400
Create Date: 2026-08-20 10:00:00.000000

背景:
- 315投诉(外部投诉) / 卡商群投诉两张补充表重新启用,由 workbuddy 每 20 分钟
  检查文件后通过 JSON push 同步。
- 同步需要业务去重指纹(dedup_hash)防止重复推送累积:
  * external_complaints 原用 complaint_no 去重,但样例文件中同编号多条不同投诉
    (含 "/" 等垃圾编号 498 条),单一 complaint_no 不可靠 → 改为身份指纹去重
  * card_complaints 无天然唯一键 → 指纹去重
- 两张表新增关联工单号(matched_ticket_no):按 device_sn 与 service_tickets
  外部投诉类工单关联(核心匹配 SN),供"关联合并"展示与同步日志统计。
- 新增 complaint_sync_logs 记录每次 workbuddy 推送的清洗/去重/匹配/报错日志。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'x2b3c4d5e6f7'
down_revision: Union[str, None] = '64a584531400'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- external_complaints: 关联工单 + 去重指纹 ----
    op.add_column('external_complaints', sa.Column('matched_ticket_no', sa.String(length=64), nullable=True, comment='关联服务工单号(按设备SN匹配外部投诉类工单)'))
    op.add_column('external_complaints', sa.Column('matched_at', sa.DateTime(timezone=True), nullable=True, comment='关联工单时间'))
    op.add_column('external_complaints', sa.Column('dedup_hash', sa.String(length=32), nullable=True, comment='业务去重指纹(md5):编号+日期+手机号+SN+问题'))
    op.create_index(op.f('ix_external_complaints_matched_ticket_no'), 'external_complaints', ['matched_ticket_no'])
    op.create_index(op.f('ix_external_complaints_dedup_hash'), 'external_complaints', ['dedup_hash'], unique=True)

    # ---- card_complaints: 关联工单 + 去重指纹 ----
    op.add_column('card_complaints', sa.Column('matched_ticket_no', sa.String(length=64), nullable=True, comment='关联服务工单号(按设备SN匹配外部投诉类工单)'))
    op.add_column('card_complaints', sa.Column('matched_at', sa.DateTime(timezone=True), nullable=True, comment='关联工单时间'))
    op.add_column('card_complaints', sa.Column('dedup_hash', sa.String(length=32), nullable=True, comment='业务去重指纹(md5):日期+群+手机号+SN+问题'))
    op.create_index(op.f('ix_card_complaints_matched_ticket_no'), 'card_complaints', ['matched_ticket_no'])
    op.create_index(op.f('ix_card_complaints_dedup_hash'), 'card_complaints', ['dedup_hash'], unique=True)

    # ---- complaint_sync_logs: workbuddy 推送同步日志 ----
    op.create_table('complaint_sync_logs',
        sa.Column('source_file', sa.String(length=256), nullable=True, comment='推送来源文件名'),
        sa.Column('sync_source', sa.String(length=32), nullable=False, server_default='workbuddy', comment='同步来源: workbuddy/manual'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='success', comment='success/failed/partial'),
        sa.Column('external_total', sa.Integer(), nullable=True, comment='315外部投诉总行数'),
        sa.Column('external_inserted', sa.Integer(), nullable=True, comment='315新增'),
        sa.Column('external_updated', sa.Integer(), nullable=True, comment='315更新'),
        sa.Column('external_failed', sa.Integer(), nullable=True, comment='315失败行'),
        sa.Column('external_matched', sa.Integer(), nullable=True, comment='315关联工单数'),
        sa.Column('card_total', sa.Integer(), nullable=True, comment='卡商投诉总行数'),
        sa.Column('card_inserted', sa.Integer(), nullable=True, comment='卡商新增'),
        sa.Column('card_updated', sa.Integer(), nullable=True, comment='卡商更新'),
        sa.Column('card_failed', sa.Integer(), nullable=True, comment='卡商失败行'),
        sa.Column('card_matched', sa.Integer(), nullable=True, comment='卡商关联工单数'),
        sa.Column('error', sa.Text(), nullable=True, comment='错误摘要'),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True, comment='完成时间'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_complaint_sync_logs_created_at'), 'complaint_sync_logs', ['created_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_complaint_sync_logs_created_at'), table_name='complaint_sync_logs')
    op.drop_table('complaint_sync_logs')
    op.drop_index(op.f('ix_card_complaints_dedup_hash'), table_name='card_complaints')
    op.drop_index(op.f('ix_card_complaints_matched_ticket_no'), table_name='card_complaints')
    op.drop_column('card_complaints', 'dedup_hash')
    op.drop_column('card_complaints', 'matched_at')
    op.drop_column('card_complaints', 'matched_ticket_no')
    op.drop_index(op.f('ix_external_complaints_dedup_hash'), table_name='external_complaints')
    op.drop_index(op.f('ix_external_complaints_matched_ticket_no'), table_name='external_complaints')
    op.drop_column('external_complaints', 'dedup_hash')
    op.drop_column('external_complaints', 'matched_at')
    op.drop_column('external_complaints', 'matched_ticket_no')
