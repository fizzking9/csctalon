"""add cs_records table

Revision ID: b4c9d1f02e5a
Revises: a3f8c2e91b4d
Create Date: 2026-07-26 23:00:00.000000

新增智能助手处理记录表:
- cs_records: 客服接待/操作流水,接待量、工作负荷、退款挽回成功率分析的源头数据
- 无天然唯一键(同设备同时间可多次接待),采用自增 id + pk_strategy=none,导入时简单 insert
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4c9d1f02e5a'
down_revision: Union[str, None] = 'a3f8c2e91b4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('cs_records',
        sa.Column('problem_category', sa.String(length=64), nullable=True, comment='问题大类:退款/网络问题...'),
        sa.Column('problem_type', sa.String(length=64), nullable=True, comment='问题类型'),
        sa.Column('device_sn', sa.String(length=64), nullable=True, comment='设备号 SN'),
        sa.Column('solution', sa.String(length=128), nullable=True, comment='方案'),
        sa.Column('operation_time', sa.DateTime(timezone=True), nullable=True, comment='操作时间(主时间字段)'),
        sa.Column('operator', sa.String(length=64), nullable=True, comment='操作人:企微-XXX(绩效来源)'),
        sa.Column('send_content', sa.Text(), nullable=True, comment='发送内容'),
        sa.Column('description', sa.Text(), nullable=True, comment='说明'),
        sa.Column('execution_result', sa.String(length=128), nullable=True, comment='执行结果说明'),
        sa.Column('user_accepted', sa.String(length=32), nullable=True, comment='用户是否接受:接受/未接受/不涉及'),
        sa.Column('consult_time', sa.DateTime(timezone=True), nullable=True, comment='咨询时间'),
        sa.Column('problem_create_time', sa.DateTime(timezone=True), nullable=True, comment='问题类型创建时间'),
        sa.Column('last_consult_time', sa.DateTime(timezone=True), nullable=True, comment='最后咨询时间'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['device_sn'], ['devices.sn'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_cs_records_problem_category'), 'cs_records', ['problem_category'])
    op.create_index(op.f('ix_cs_records_problem_type'), 'cs_records', ['problem_type'])
    op.create_index(op.f('ix_cs_records_device_sn'), 'cs_records', ['device_sn'])
    op.create_index(op.f('ix_cs_records_operation_time'), 'cs_records', ['operation_time'])
    op.create_index(op.f('ix_cs_records_operator'), 'cs_records', ['operator'])
    op.create_index(op.f('ix_cs_records_execution_result'), 'cs_records', ['execution_result'])
    op.create_index(op.f('ix_cs_records_user_accepted'), 'cs_records', ['user_accepted'])


def downgrade() -> None:
    op.drop_index(op.f('ix_cs_records_user_accepted'), table_name='cs_records')
    op.drop_index(op.f('ix_cs_records_execution_result'), table_name='cs_records')
    op.drop_index(op.f('ix_cs_records_operator'), table_name='cs_records')
    op.drop_index(op.f('ix_cs_records_operation_time'), table_name='cs_records')
    op.drop_index(op.f('ix_cs_records_device_sn'), table_name='cs_records')
    op.drop_index(op.f('ix_cs_records_problem_type'), table_name='cs_records')
    op.drop_index(op.f('ix_cs_records_problem_category'), table_name='cs_records')
    op.drop_table('cs_records')
