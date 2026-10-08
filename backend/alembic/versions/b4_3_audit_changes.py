"""B4-5 (#261): 统一变更审计表 audit_changes (S-P1-11)

Revision ID: b4_3_audit_changes
Revises: b4_2_batch_jobs
Create Date: 2026-10-09 02:00:00.000000

依据: 排期 §七 B4-5; 范式来源 zammad histories / helpdesk TicketChange
     (统一变更模型: 实体 + 动作 + 字段级 before/after)。

范围:
1. audit_changes 统一变更审计表(entity_type/entity_id/action/actor/source/
   changes JSONB{field:[before,after]}/summary)
2. 索引: entity_type+entity_id 联合(实体追溯主路径) / created_at(保留期清理扫描)
   / source(来源过滤) —— 单列 entity_type/entity_id 索引由联合索引左前缀覆盖,
   不重复建。

downgrade: 新表 DROP 即安全回退(仅审计数据, 无业务数据损失)。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = 'b4_3_audit_changes'
down_revision: Union[str, None] = 'b4_2_batch_jobs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'audit_changes',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('entity_type', sa.String(length=64), nullable=False, comment='实体类型(表名, 如 persons/manual_refunds)'),
        sa.Column('entity_id', sa.String(length=64), nullable=False, comment='实体主键(统一字符串)'),
        sa.Column('action', sa.String(length=16), nullable=False, comment='动作: create/update/delete'),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, comment='操作人ID(系统触发为空)'),
        sa.Column('actor_name', sa.String(length=64), nullable=True, comment='操作人账号(冗余,用户删除后仍可审计)'),
        sa.Column('source', sa.String(length=16), nullable=False, comment='来源: api/night_batch/manual_import/system'),
        sa.Column('changes', JSONB(), nullable=True, comment='字段级变更: {field: [before, after]}'),
        sa.Column('summary', sa.Text(), nullable=True, comment='人类可读摘要'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        comment='统一变更审计: 实体字段级 before/after(#261 B4-5)',
    )
    # 实体追溯主路径: 查"某实体被谁改成什么样"
    op.create_index('ix_audit_changes_entity', 'audit_changes', ['entity_type', 'entity_id'])
    # 保留期清理扫描 + 时间范围查询
    op.create_index('ix_audit_changes_created_at', 'audit_changes', ['created_at'])
    op.create_index('ix_audit_changes_source', 'audit_changes', ['source'])


def downgrade() -> None:
    op.drop_index('ix_audit_changes_source', table_name='audit_changes')
    op.drop_index('ix_audit_changes_created_at', table_name='audit_changes')
    op.drop_index('ix_audit_changes_entity', table_name='audit_changes')
    op.drop_table('audit_changes')
