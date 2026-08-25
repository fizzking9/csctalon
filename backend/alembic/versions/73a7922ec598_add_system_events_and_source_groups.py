"""add_system_events_and_source_groups

Revision ID: 73a7922ec598
Revises: m7n8o9p0q1r2
Create Date: 2026-08-25 11:06:57.088116
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '73a7922ec598'
down_revision: Union[str, None] = 'm7n8o9p0q1r2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 数据源分组(组级凭证, 同域共享)
    op.create_table('source_groups',
        sa.Column('group_key', sa.String(length=64), nullable=False, comment='组标识: 如 bms-support / bms-wifi / shanmao / scrm / aiacc'),
        sa.Column('group_name', sa.String(length=64), nullable=False, comment='组显示名: 如 BMS 主平台 / 闪猫 / SCRM'),
        sa.Column('base_url', sa.String(length=256), nullable=True, comment='组域名/基础地址'),
        sa.Column('username', sa.Text(), nullable=True, comment='组级登录账号(Fernet加密); 组内单源未配置时继承'),
        sa.Column('password', sa.Text(), nullable=True, comment='组级登录密码(Fernet加密)'),
        sa.Column('sort_order', sa.Integer(), nullable=False, comment='展示顺序'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_source_groups_group_key'), 'source_groups', ['group_key'], unique=True)

    # 系统事务通知
    op.create_table('system_events',
        sa.Column('category', sa.String(length=32), nullable=False, comment='类别: sync(数据同步) / push(推送接收) / import(导入) / alert(预警) / schedule(定时调度) / match(关联匹配) / auth(登录验证)'),
        sa.Column('action', sa.String(length=64), nullable=False, comment='具体动作: bms_sync / complaint_push / manual_refund_push / test_login ...'),
        sa.Column('title', sa.String(length=255), nullable=False, comment='标题(人类可读)'),
        sa.Column('summary', sa.String(length=512), nullable=True, comment='概要(结果数字/影响范围)'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='结果: success / failed / partial / info'),
        sa.Column('level', sa.String(length=16), nullable=False, comment='级别: info / warning / error (由写入方显式指定, 与 status 可不同)'),
        sa.Column('detail', sa.Text(), nullable=True, comment='详情/错误信息'),
        sa.Column('is_read', sa.Boolean(), nullable=False, comment='是否已读(角标/未读计数用)'),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True, comment='已读时间'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_system_events_action'), 'system_events', ['action'], unique=False)
    op.create_index(op.f('ix_system_events_category'), 'system_events', ['category'], unique=False)
    op.create_index(op.f('ix_system_events_is_read'), 'system_events', ['is_read'], unique=False)
    op.create_index(op.f('ix_system_events_status'), 'system_events', ['status'], unique=False)

    # sync_source_configs 归组
    op.add_column('sync_source_configs', sa.Column('group_key', sa.String(length=64), nullable=True, comment='所属数据源分组(source_groups.group_key), 组级凭证继承'))
    op.create_index(op.f('ix_sync_source_configs_group_key'), 'sync_source_configs', ['group_key'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_sync_source_configs_group_key'), table_name='sync_source_configs')
    op.drop_column('sync_source_configs', 'group_key')
    op.drop_index(op.f('ix_system_events_status'), table_name='system_events')
    op.drop_index(op.f('ix_system_events_is_read'), table_name='system_events')
    op.drop_index(op.f('ix_system_events_category'), table_name='system_events')
    op.drop_index(op.f('ix_system_events_action'), table_name='system_events')
    op.drop_table('system_events')
    op.drop_index(op.f('ix_source_groups_group_key'), table_name='source_groups')
    op.drop_table('source_groups')
