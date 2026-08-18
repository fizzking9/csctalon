"""Phase A: 统一待办/通知 + 异常事故增强 + 设备专项标签

新增:
- notifications 站内信/通知表
- incident_followups 异常事故跟进时间线表
- device_tag_batches 设备专项标签批量导入批次表

变更:
- incidents 增列 category/department/severity/resolved_at/closed_at/is_closed
- device_tag_rel 增列 batch_id/status/expired_at

幂等: 所有 DDL 均做 _table_exists/_column_exists 检查。

Revision ID: a0b1c2d3e4f5
Revises: p1b2c3d4e5f6
Create Date: 2026-08-18 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a0b1c2d3e4f5'
down_revision: Union[str, None] = 'p1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 1. incidents 增列 ----
    if not _has_column(bind, "incidents", "category"):
        op.add_column("incidents", sa.Column("category", sa.String(64), nullable=True, comment="来源/类型"))
        op.create_index("ix_incidents_category", "incidents", ["category"])
    if not _has_column(bind, "incidents", "department"):
        op.add_column("incidents", sa.Column("department", sa.String(64), nullable=True, comment="涉及部门"))
    if not _has_column(bind, "incidents", "severity"):
        op.add_column("incidents", sa.Column("severity", sa.String(16), nullable=True, server_default="中", comment="严重程度"))
    if not _has_column(bind, "incidents", "resolved_at"):
        op.add_column("incidents", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True, comment="解决时间"))
    if not _has_column(bind, "incidents", "closed_at"):
        op.add_column("incidents", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True, comment="闭环时间"))
    if not _has_column(bind, "incidents", "is_closed"):
        op.add_column("incidents", sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.false(), comment="是否闭环"))

    # ---- 2. device_tag_rel 增列 ----
    if not _has_column(bind, "device_tag_rel", "batch_id"):
        op.add_column("device_tag_rel", sa.Column("batch_id", sa.Integer(), nullable=True, comment="来源批次"))
    if not _has_column(bind, "device_tag_rel", "status"):
        op.add_column("device_tag_rel", sa.Column("status", sa.String(16), nullable=False, server_default="active", comment="active/inactive"))
    if not _has_column(bind, "device_tag_rel", "expired_at"):
        op.add_column("device_tag_rel", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True, comment="失效时间"))

    # ---- 3. incident_followups ----
    if not _has_table(bind, "incident_followups"):
        op.create_table(
            "incident_followups",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status_change", sa.String(64), nullable=True),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_incident_followups_incident_id", "incident_followups", ["incident_id"])

    # ---- 4. device_tag_batches ----
    if not _has_table(bind, "device_tag_batches"):
        op.create_table(
            "device_tag_batches",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("batch_no", sa.String(64), nullable=False),
            sa.Column("tag_id", sa.Integer(), sa.ForeignKey("device_tags.id"), nullable=False),
            sa.Column("title", sa.String(255), nullable=True),
            sa.Column("source_file", sa.String(255), nullable=True),
            sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_device_tag_batches_batch_no", "device_tag_batches", ["batch_no"], unique=True)
        op.create_index("ix_device_tag_batches_tag_id", "device_tag_batches", ["tag_id"])

    # ---- 5. notifications ----
    if not _has_table(bind, "notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("type", sa.String(32), nullable=False, server_default="info"),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("ref_type", sa.String(64), nullable=True),
            sa.Column("ref_id", sa.String(64), nullable=True),
            sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_notifications_recipient_id", "notifications", ["recipient_id"])
        op.create_index("ix_notifications_type", "notifications", ["type"])
        op.create_index("ix_notifications_is_read", "notifications", ["is_read"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("device_tag_batches")
    op.drop_table("incident_followups")
    for col in ("expired_at", "status", "batch_id"):
        op.drop_column("device_tag_rel", col)
    for col in ("is_closed", "closed_at", "resolved_at", "severity", "department", "category"):
        op.drop_column("incidents", col)
