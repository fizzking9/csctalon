"""add strategy_docs and strategy_read_receipts

Revision ID: c5d0e2a13f6b
Revises: b4c9d1f02e5a
Create Date: 2026-07-26 23:30:00.000000

新增发布页核心表:
- strategy_docs: 策略文档(版本管理:草稿/已发布/已归档)
- strategy_read_receipts: 已读回执(CS 查看文档时记录,unique doc+user)
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c5d0e2a13f6b'
down_revision: Union[str, None] = 'b4c9d1f02e5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('strategy_docs',
        sa.Column('title', sa.String(length=255), nullable=False, comment='文档标题'),
        sa.Column('category', sa.String(length=32), nullable=True, comment='分类:退款策略/清退策略/服务规范/通用'),
        sa.Column('content', sa.Text(), nullable=True, comment='内容(Markdown)'),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False, comment='版本号'),
        sa.Column('status', sa.String(length=16), server_default='draft', nullable=False, comment='draft草稿/published已发布/archived已归档'),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True, comment='发布时间'),
        sa.Column('published_by', sa.String(length=64), nullable=True, comment='发布人'),
        sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_strategy_docs_title'), 'strategy_docs', ['title'])
    op.create_index(op.f('ix_strategy_docs_category'), 'strategy_docs', ['category'])
    op.create_index(op.f('ix_strategy_docs_status'), 'strategy_docs', ['status'])

    op.create_table('strategy_read_receipts',
        sa.Column('doc_id', sa.Integer(), nullable=False, comment='文档ID'),
        sa.Column('user_id', sa.Integer(), nullable=False, comment='用户ID'),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=False, comment='阅读时间'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['doc_id'], ['strategy_docs.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('doc_id', 'user_id', name='uq_strategy_read_doc_user'),
    )
    op.create_index(op.f('ix_strategy_read_receipts_doc_id'), 'strategy_read_receipts', ['doc_id'])
    op.create_index(op.f('ix_strategy_read_receipts_user_id'), 'strategy_read_receipts', ['user_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_strategy_read_receipts_user_id'), table_name='strategy_read_receipts')
    op.drop_index(op.f('ix_strategy_read_receipts_doc_id'), table_name='strategy_read_receipts')
    op.drop_table('strategy_read_receipts')
    op.drop_index(op.f('ix_strategy_docs_status'), table_name='strategy_docs')
    op.drop_index(op.f('ix_strategy_docs_category'), table_name='strategy_docs')
    op.drop_index(op.f('ix_strategy_docs_title'), table_name='strategy_docs')
    op.drop_table('strategy_docs')
