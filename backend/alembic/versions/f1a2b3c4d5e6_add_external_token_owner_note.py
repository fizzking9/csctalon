"""add_external_token_owner_note

外部只读 token 增加可选 owner_note(责任人/用途备注) 字段(F-P2-5 / 工单#11)。

背景: 外部 token 管理界面仅 created_by(username), 无法记录"责任人/用途说明"。
轻量方案: 增加可空 owner_note 自由文本(责任人/用途), 不引入 user/person 外键
(保持 token=调用方凭证模型, A-20260902-021 LOCKED)。

Revision ID: f1a2b3c4d5e6
Revises: e6a7b8c9d0e1
Create Date: 2026-09-04 21:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('external_access_tokens', sa.Column(
        'owner_note', sa.String(length=255), nullable=True,
        comment='责任人/用途备注(可选, 管理界面展示与编辑)',
    ))


def downgrade() -> None:
    op.drop_column('external_access_tokens', 'owner_note')
