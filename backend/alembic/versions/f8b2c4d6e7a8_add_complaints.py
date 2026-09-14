"""add complaint tables (external + card)

Revision ID: f8b2c4d6e7a8
Revises: e7a1b3c4d5e6
Create Date: 2026-07-26 20:30:00.000000

新增两张投诉数据表:
- external_complaints: 监督局/315外部投诉(27字段,投诉编号去重)
- card_complaints: 卡商群投诉(10字段,无唯一键自增主键)
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'f8b2c4d6e7a8'
down_revision: Union[str, None] = 'e7a1b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 外部投诉(监督局/315)
    op.create_table('external_complaints',
        sa.Column('complaint_no', sa.String(length=64), nullable=True, comment='投诉编号(部分缺失,缺失时不去重)'),
        sa.Column('complaint_date', sa.DateTime(timezone=True), nullable=True, comment='投诉日期(主时间字段)'),
        sa.Column('platform', sa.String(length=64), nullable=True, comment='平台'),
        sa.Column('complaint_account', sa.String(length=128), nullable=True, comment='投诉账号'),
        sa.Column('company_entity', sa.String(length=128), nullable=True, comment='公司主体'),
        sa.Column('complaint_shop', sa.String(length=128), nullable=True, comment='投诉店铺'),
        sa.Column('purchase_order_no', sa.String(length=128), nullable=True, comment='购买订单编号'),
        sa.Column('complainant', sa.String(length=64), nullable=True, comment='投诉人'),
        sa.Column('complainant_phone', sa.String(length=32), nullable=True, comment='投诉手机号'),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='投诉设备号 SN'),
        sa.Column('wechat_id', sa.String(length=128), nullable=True, comment='微信账户id'),
        sa.Column('problem', sa.Text(), nullable=True, comment='投诉问题'),
        sa.Column('keyword', sa.String(length=128), nullable=True, comment='投诉关键词'),
        sa.Column('complaint_category', sa.String(length=64), nullable=True, comment='投诉分类'),
        sa.Column('solution', sa.String(length=512), nullable=True, comment='解决方案'),
        sa.Column('result', sa.String(length=64), nullable=True, comment='结果'),
        sa.Column('follower', sa.String(length=64), nullable=True, comment='跟进人'),
        sa.Column('handle_time', sa.DateTime(timezone=True), nullable=True, comment='处理时间'),
        sa.Column('handle_detail', sa.Text(), nullable=True, comment='处理明细'),
        sa.Column('internal_analysis', sa.Text(), nullable=True, comment='内部分析'),
        sa.Column('external_analysis', sa.Text(), nullable=True, comment='外部分析'),
        sa.Column('reception_cs', sa.String(length=64), nullable=True, comment='接待客服'),
        sa.Column('package_order_no', sa.String(length=128), nullable=True, comment='套餐订单号'),
        sa.Column('package_name', sa.String(length=128), nullable=True, comment='套餐名称'),
        sa.Column('package_price', sa.Numeric(precision=12, scale=2), nullable=True, comment='套餐价格'),
        sa.Column('actual_pay_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='实际支付金额'),
        sa.Column('negotiate_refund_amount', sa.Numeric(precision=12, scale=2), nullable=True, comment='协商退款金额'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('complaint_no', name='uq_external_complaints_no'),
    )
    op.create_index(op.f('ix_external_complaints_complaint_no'), 'external_complaints', ['complaint_no'])
    op.create_index(op.f('ix_external_complaints_complaint_date'), 'external_complaints', ['complaint_date'])
    op.create_index(op.f('ix_external_complaints_complaint_category'), 'external_complaints', ['complaint_category'])
    op.create_index(op.f('ix_external_complaints_result'), 'external_complaints', ['result'])
    op.create_index(op.f('ix_external_complaints_follower'), 'external_complaints', ['follower'])
    op.create_index(op.f('ix_external_complaints_keyword'), 'external_complaints', ['keyword'])
    op.create_index(op.f('ix_external_complaints_purchase_order_no'), 'external_complaints', ['purchase_order_no'])
    op.create_index(op.f('ix_external_complaints_complainant_phone'), 'external_complaints', ['complainant_phone'])

    # 卡商群投诉
    op.create_table('card_complaints',
        sa.Column('complaint_date', sa.DateTime(timezone=True), nullable=True, comment='投诉日期(主时间字段)'),
        sa.Column('complaint_group', sa.String(length=64), nullable=True, comment='投诉群'),
        sa.Column('complainant', sa.String(length=64), nullable=True, comment='投诉人'),
        sa.Column('complainant_phone', sa.String(length=32), nullable=True, comment='投诉手机号'),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备号 SN'),
        sa.Column('problem', sa.Text(), nullable=True, comment='投诉问题'),
        sa.Column('problem_category', sa.String(length=64), nullable=True, comment='投诉问题分类(二级)'),
        sa.Column('result', sa.String(length=128), nullable=True, comment='结果'),
        sa.Column('solution', sa.String(length=128), nullable=True, comment='解决方案'),
        sa.Column('handler', sa.String(length=64), nullable=True, comment='处理人'),
        sa.Column('handle_result', sa.Text(), nullable=True, comment='处理结果'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_card_complaints_complaint_date'), 'card_complaints', ['complaint_date'])
    op.create_index(op.f('ix_card_complaints_complaint_group'), 'card_complaints', ['complaint_group'])
    op.create_index(op.f('ix_card_complaints_complainant_phone'), 'card_complaints', ['complainant_phone'])
    op.create_index(op.f('ix_card_complaints_problem_category'), 'card_complaints', ['problem_category'])
    op.create_index(op.f('ix_card_complaints_result'), 'card_complaints', ['result'])
    op.create_index(op.f('ix_card_complaints_solution'), 'card_complaints', ['solution'])
    op.create_index(op.f('ix_card_complaints_handler'), 'card_complaints', ['handler'])


def downgrade() -> None:
    op.drop_index(op.f('ix_card_complaints_handler'), table_name='card_complaints')
    op.drop_index(op.f('ix_card_complaints_solution'), table_name='card_complaints')
    op.drop_index(op.f('ix_card_complaints_result'), table_name='card_complaints')
    op.drop_index(op.f('ix_card_complaints_problem_category'), table_name='card_complaints')
    op.drop_index(op.f('ix_card_complaints_complainant_phone'), table_name='card_complaints')
    op.drop_index(op.f('ix_card_complaints_complaint_group'), table_name='card_complaints')
    op.drop_index(op.f('ix_card_complaints_complaint_date'), table_name='card_complaints')
    op.drop_table('card_complaints')
    op.drop_index(op.f('ix_external_complaints_complainant_phone'), table_name='external_complaints')
    op.drop_index(op.f('ix_external_complaints_purchase_order_no'), table_name='external_complaints')
    op.drop_index(op.f('ix_external_complaints_keyword'), table_name='external_complaints')
    op.drop_index(op.f('ix_external_complaints_follower'), table_name='external_complaints')
    op.drop_index(op.f('ix_external_complaints_result'), table_name='external_complaints')
    op.drop_index(op.f('ix_external_complaints_complaint_category'), table_name='external_complaints')
    op.drop_index(op.f('ix_external_complaints_complaint_date'), table_name='external_complaints')
    op.drop_index(op.f('ix_external_complaints_complaint_no'), table_name='external_complaints')
    op.drop_table('external_complaints')
