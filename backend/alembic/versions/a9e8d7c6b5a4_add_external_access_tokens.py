"""add_external_access_tokens

新增 external_access_tokens 表: 外部工具只读访问令牌(workbuddy 等)。
- token 明文不落库, 仅存 SHA-256 hash + prefix; 吊销=enabled=false 软删。
- scopes JSONB: reception/refund/complaint/brief(可扩展)。
- rate_limit_per_min 个性化限频(Redis 固定窗口), NULL=全局默认。

Revision ID: a9e8d7c6b5a4
Revises: 73a7922ec598
Create Date: 2026-09-02 18:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'a9e8d7c6b5a4'
down_revision: Union[str, None] = '73a7922ec598'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('external_access_tokens',
        sa.Column('name', sa.String(length=64), nullable=False, comment='调用方标识(如 workbuddy-简报), 审计/管理界面显示'),
        sa.Column('token_hash', sa.String(length=64), nullable=False, comment='SHA-256(token), 不存明文'),
        sa.Column('token_prefix', sa.String(length=12), nullable=False, comment='明文前 8 位, 管理界面识别用'),
        sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='权限范围: reception/refund/complaint/brief, 可扩展'),
        sa.Column('rate_limit_per_min', sa.Integer(), nullable=True, comment='每 token 个性化限频(次/分钟); NULL=用全局默认'),
        sa.Column('enabled', sa.Boolean(), nullable=False, comment='总开关: false=已吊销(软删)'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True, comment='可选过期时间'),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True, comment='最后使用时间(管理界面看活跃度)'),
        sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人账号'),
        sa.Column('revoked_by', sa.String(length=64), nullable=True, comment='吊销人账号'),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_external_access_tokens_expires_at'), 'external_access_tokens', ['expires_at'], unique=False)
    op.create_index(op.f('ix_external_access_tokens_token_hash'), 'external_access_tokens', ['token_hash'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_external_access_tokens_token_hash'), table_name='external_access_tokens')
    op.drop_index(op.f('ix_external_access_tokens_expires_at'), table_name='external_access_tokens')
    op.drop_table('external_access_tokens')
