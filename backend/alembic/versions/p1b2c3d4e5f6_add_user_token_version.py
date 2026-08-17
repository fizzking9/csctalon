"""add users.token_version for JWT revocation

新增 users.token_version 列, 实现 JWT 吊销机制(M7):
- 签发 token 时写入 tkv=token_version
- 每次请求校验 token.tkv == users.token_version
- 改密/登出时 token_version+1, 使所有已签发 token 立即失效

幂等: 若列已存在则跳过(适配本地已用 create_all 补列的环境)。

Revision ID: p1b2c3d4e5f6
Revises: o1b2c3d4e5f6
Create Date: 2026-08-17 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p1b2c3d4e5f6'
down_revision: Union[str, None] = 'o1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "users", "token_version"):
        op.add_column(
            "users",
            sa.Column(
                "token_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
                comment="JWT吊销版本号",
            ),
        )
    # 确保存量用户为 0(防御性: server_default 已保证, 此处幂等)
    op.execute("UPDATE users SET token_version = 0 WHERE token_version IS NULL")


def downgrade() -> None:
    op.drop_column("users", "token_version")
