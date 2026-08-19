"""add incident recurring fields (is_recurring/recurrence_pattern/valid_from/valid_to)

- incidents 表增加 周期性事件支持字段: is_recurring / recurrence_pattern / valid_from / valid_to
- 用于广告推送/定期维护等阶段性影响事件的登记与每日生效判定
- 所有改动均为幂等 (列已存在则跳过)

Revision ID: r6f7a8b9c0d1
Revises: w1x2y3z4a5b6
Create Date: 2026-08-19 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r6f7a8b9c0d1"
down_revision: Union[str, None] = "w1x2y3z4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    insp = sa.inspect(bind)
    return index in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "incidents", "is_recurring"):
        op.add_column(
            "incidents",
            sa.Column("is_recurring", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if not _has_column(bind, "incidents", "recurrence_pattern"):
        op.add_column(
            "incidents",
            sa.Column("recurrence_pattern", sa.String(length=32), nullable=True),
        )
    if not _has_column(bind, "incidents", "valid_from"):
        op.add_column(
            "incidents",
            sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column(bind, "incidents", "valid_to"):
        op.add_column(
            "incidents",
            sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_index(bind, "incidents", "ix_incidents_valid_from"):
        op.create_index("ix_incidents_valid_from", "incidents", ["valid_from"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "incidents", "ix_incidents_valid_from"):
        op.drop_index("ix_incidents_valid_from", table_name="incidents")
    for col in ("valid_to", "valid_from", "recurrence_pattern", "is_recurring"):
        if _has_column(bind, "incidents", col):
            op.drop_column("incidents", col)
