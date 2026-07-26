"""add operation_logs

Revision ID: e7a1b3c4d5e6
Revises: c5d0e2a13f6b
Create Date: 2026-07-26 23:50:00.000000

新增操作日志表(I47:操作日志要落库可查):
- operation_logs: 中间件自动记录变更请求(POST/PUT/DELETE)+ 登录事件
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7a1b3c4d5e6'
down_revision: Union[str, None] = 'c5d0e2a13f6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('operation_logs',
        sa.Column('user_id', sa.Integer(), nullable=True, comment='操作人ID'),
        sa.Column('username', sa.String(length=64), nullable=True, comment='操作人账号(冗余,用户删除后仍可审计)'),
        sa.Column('method', sa.String(length=8), nullable=False, comment='HTTP方法'),
        sa.Column('path', sa.String(length=255), nullable=False, comment='请求路径'),
        sa.Column('action', sa.String(length=64), nullable=False, comment='操作标签(如 策略.发布)'),
        sa.Column('target_type', sa.String(length=32), nullable=True, comment='操作对象类型'),
        sa.Column('target_id', sa.String(length=64), nullable=True, comment='操作对象ID'),
        sa.Column('status_code', sa.Integer(), nullable=True, comment='响应状态码'),
        sa.Column('detail', sa.Text(), nullable=True, comment='详情(JSON或文本)'),
        sa.Column('ip', sa.String(length=64), nullable=True, comment='客户端IP'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_operation_logs_user_id'), 'operation_logs', ['user_id'])
    op.create_index(op.f('ix_operation_logs_action'), 'operation_logs', ['action'])
    op.create_index(op.f('ix_operation_logs_target_type'), 'operation_logs', ['target_type'])
    op.create_index(op.f('ix_operation_logs_created_at'), 'operation_logs', ['created_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_operation_logs_created_at'), table_name='operation_logs')
    op.drop_index(op.f('ix_operation_logs_target_type'), table_name='operation_logs')
    op.drop_index(op.f('ix_operation_logs_action'), table_name='operation_logs')
    op.drop_index(op.f('ix_operation_logs_user_id'), table_name='operation_logs')
    op.drop_table('operation_logs')
