"""add refund_wide table

Revision ID: y4d5e6f7a8b0
Revises: x3c4d5e6f7a9
Create Date: 2026-07-31

新建 refund_wide 物化宽表(退费单 × 工单 × 商户退款 × 钱包 × 外部投诉 跨表关联预计算结果)。
为 data_match_relations 加 match_type 索引 + (src_table,src_id,dst_table,dst_id,match_type) 唯一约束
(加约束前先按 (src_table,src_id,dst_table,dst_id,match_type,status) 去重,保留最小 id)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'y4d5e6f7a8b0'
down_revision: Union[str, None] = 'x3c4d5e6f7a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 建 refund_wide 物化宽表
    op.create_table(
        'refund_wide',
        sa.Column('refund_no', sa.String(length=64), nullable=False, comment='退费单号(主键,与 refunds.refund_no 对应)'),
        sa.Column('refund_method', sa.String(length=255), nullable=True, comment='退款方式(冗余自 refunds)'),
        sa.Column('status', sa.String(length=64), nullable=True, comment='状态(冗余自 refunds)'),
        sa.Column('refund_reason', sa.String(length=128), nullable=True, comment='退费原因(冗余自 refunds)'),
        sa.Column('operator', sa.String(length=64), nullable=True, comment='操作人(冗余自 refunds,未归一化别名)'),
        sa.Column('auditor', sa.String(length=64), nullable=True, comment='审核人(冗余自 refunds)'),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备SN(冗余自 refunds)'),
        sa.Column('refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='退费金额(冗余自 refunds)'),
        sa.Column('created_at_raw', sa.DateTime(timezone=True), nullable=True, comment='退费单原始创建时间(冗余自 refunds)'),
        sa.Column('ticket_no', sa.String(length=64), nullable=True, comment='关联工单号'),
        sa.Column('service_item', sa.String(length=64), nullable=True, comment='服务项(退款原因来源,冗余自 service_tickets)'),
        sa.Column('service_category', sa.String(length=32), nullable=True, comment='服务类别(冗余自 service_tickets)'),
        sa.Column('ticket_finish_time', sa.DateTime(timezone=True), nullable=True, comment='工单完成时间'),
        sa.Column('ticket_handle_duration', sa.Integer(), nullable=True, comment='工单处理时长(分钟)'),
        sa.Column('ticket_handler', sa.String(length=64), nullable=True, comment='工单处理人(冗余别名)'),
        sa.Column('wx_refund_no', sa.String(length=64), nullable=True, comment='微信退款单号'),
        sa.Column('mr_trade_time', sa.DateTime(timezone=True), nullable=True, comment='商户退款交易时间'),
        sa.Column('mr_refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='商户退款金额(微信侧真实退款)'),
        sa.Column('mr_refund_status', sa.String(length=32), nullable=True, comment='商户退款状态'),
        sa.Column('mr_fee', sa.Numeric(precision=12, scale=4), nullable=True, comment='商户退款手续费'),
        sa.Column('merchant_id', sa.String(length=64), nullable=True, comment='商户号'),
        sa.Column('wallet_id', sa.String(length=64), nullable=True, comment='关联钱包提现记录ID(wallet_withdrawals.id)'),
        sa.Column('wallet_op_time', sa.DateTime(timezone=True), nullable=True, comment='钱包操作时间(提现时点)'),
        sa.Column('wallet_op_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='钱包操作金额(提现金额)'),
        sa.Column('has_external_complaint', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='该 SN 是否有外部投诉记录'),
        sa.Column('external_complaint_count', sa.Integer(), server_default=sa.text('0'), nullable=False, comment='该 SN 外部投诉条数'),
        sa.Column('external_complaint_latest_time', sa.DateTime(timezone=True), nullable=True, comment='该 SN 最近一次外部投诉时间'),
        sa.Column('matched_at', sa.DateTime(timezone=True), nullable=False, comment='本行物化构建时间(每次重建刷新)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('refund_no'),
    )
    op.create_index('ix_refund_wide_refund_method', 'refund_wide', ['refund_method'])
    op.create_index('ix_refund_wide_status', 'refund_wide', ['status'])
    op.create_index('ix_refund_wide_operator', 'refund_wide', ['operator'])
    op.create_index('ix_refund_wide_auditor', 'refund_wide', ['auditor'])
    op.create_index('ix_refund_wide_device_sn', 'refund_wide', ['device_sn'])
    op.create_index('ix_refund_wide_created_at_raw', 'refund_wide', ['created_at_raw'])
    op.create_index('ix_refund_wide_ticket_no', 'refund_wide', ['ticket_no'])
    op.create_index('ix_refund_wide_wx_refund_no', 'refund_wide', ['wx_refund_no'])
    op.create_index('ix_refund_wide_wallet_id', 'refund_wide', ['wallet_id'])
    op.create_index('ix_refund_wide_has_external_complaint', 'refund_wide', ['has_external_complaint'])

    # 2. data_match_relations:加 match_type 索引 + 复合唯一约束(加前先去重)
    op.create_index('ix_data_match_relations_match_type', 'data_match_relations', ['match_type'])

    # 去重:同一 (src_table,src_id,dst_table,dst_id,match_type) 若有多条,保留最小 id,删其余
    # status 字段不进唯一键:同一关系可能存在 matched + invalid 多状态历史,只保留一条
    op.execute(
        """
        DELETE FROM data_match_relations a
        USING data_match_relations b
        WHERE a.id > b.id
          AND a.src_table = b.src_table
          AND a.src_id = b.src_id
          AND a.dst_table = b.dst_table
          AND a.dst_id = b.dst_id
          AND a.match_type = b.match_type
        """
    )
    op.create_unique_constraint(
        'uq_match_relation_composite',
        'data_match_relations',
        ['src_table', 'src_id', 'dst_table', 'dst_id', 'match_type'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_match_relation_composite', 'data_match_relations', type_='unique')
    op.drop_index('ix_data_match_relations_match_type', table_name='data_match_relations')
    op.drop_index('ix_refund_wide_has_external_complaint', table_name='refund_wide')
    op.drop_index('ix_refund_wide_wallet_id', table_name='refund_wide')
    op.drop_index('ix_refund_wide_wx_refund_no', table_name='refund_wide')
    op.drop_index('ix_refund_wide_ticket_no', table_name='refund_wide')
    op.drop_index('ix_refund_wide_created_at_raw', table_name='refund_wide')
    op.drop_index('ix_refund_wide_device_sn', table_name='refund_wide')
    op.drop_index('ix_refund_wide_auditor', table_name='refund_wide')
    op.drop_index('ix_refund_wide_operator', table_name='refund_wide')
    op.drop_index('ix_refund_wide_status', table_name='refund_wide')
    op.drop_index('ix_refund_wide_refund_method', table_name='refund_wide')
    op.drop_table('refund_wide')
