"""add doc_sync_configs table (腾讯文档/金山文档共享文档同步配置)

Revision ID: d5f6a7b8c9d0
Revises: n9a8b7c6d5e4f
Create Date: 2026-08-22 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d5f6a7b8c9d0"
down_revision: Union[str, None] = "n9a8b7c6d5e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    insp = sa.inspect(bind)
    return insp.has_table(table)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "doc_sync_configs"):
        return
    op.create_table(
        "doc_sync_configs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), server_default="", nullable=False),
        sa.Column("app_id", sa.Text(), nullable=True),
        sa.Column("app_secret", sa.Text(), nullable=True),
        sa.Column("redirect_uri", sa.String(length=512), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("open_id", sa.String(length=128), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("doc_url", sa.String(length=512), nullable=True),
        sa.Column("file_id", sa.String(length=256), nullable=True),
        sa.Column("external_sheet_name", sa.String(length=128), nullable=True),
        sa.Column("card_sheet_name", sa.String(length=128), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sync_interval_minutes", sa.Integer(), server_default="20", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=32), nullable=True),
        sa.Column("last_sync_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_doc_sync_configs_platform", "doc_sync_configs", ["platform"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "doc_sync_configs"):
        op.drop_table("doc_sync_configs")
