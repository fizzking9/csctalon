"""add ingest_mode_configs table (投诉数据采集方式开关)

Revision ID: e1f2a3b4c5d6
Revises: d5f6a7b8c9d0
Create Date: 2026-08-22 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    insp = sa.inspect(bind)
    return insp.has_table(table)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "ingest_mode_configs"):
        return
    op.create_table(
        "ingest_mode_configs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.create_index("ix_ingest_mode_configs_mode", "ingest_mode_configs", ["mode"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "ingest_mode_configs"):
        op.drop_table("ingest_mode_configs")
