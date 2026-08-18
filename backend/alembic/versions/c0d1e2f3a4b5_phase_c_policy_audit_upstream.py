"""Phase C: 政策措施增强 + 投诉审计 + 跨部门协作 + 数据质量

变更:
- policies 增列 topic_tags/replaces_policy_id/replaced_by_policy_id/diagram_urls

新增表:
- complaint_audits (单个投诉审计)
- upstream_tickets (跨部门协作)
- data_quality_rules (数据质量占位)

幂等: _table_exists/_column_exists 检查。

Revision ID: c0d1e2f3a4b5
Revises: b0c1d2e3f4a5
Create Date: 2026-08-18 02:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c0d1e2f3a4b5'
down_revision: Union[str, None] = 'b0c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _ts_cols():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    # policies 增列
    if not _has_column(bind, "policies", "topic_tags"):
        op.add_column("policies", sa.Column("topic_tags", postgresql.JSONB(), nullable=True, comment="主题/关联标签"))
    if not _has_column(bind, "policies", "replaces_policy_id"):
        op.add_column("policies", sa.Column("replaces_policy_id", sa.Integer(), nullable=True, comment="替代的政策ID"))
    if not _has_column(bind, "policies", "replaced_by_policy_id"):
        op.add_column("policies", sa.Column("replaced_by_policy_id", sa.Integer(), nullable=True, comment="被替代的政策ID"))
    if not _has_column(bind, "policies", "diagram_urls"):
        op.add_column("policies", sa.Column("diagram_urls", postgresql.JSONB(), nullable=True, comment="示意图URL"))

    if not _has_table(bind, "complaint_audits"):
        op.create_table(
            "complaint_audits",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("source_type", sa.String(32), nullable=False),
            sa.Column("ref_id", sa.String(128), nullable=True),
            sa.Column("device_sn", sa.String(64), nullable=True),
            sa.Column("involved_persons", postgresql.JSONB(), nullable=True),
            sa.Column("attribution", sa.String(64), nullable=True),
            sa.Column("conclusion", sa.Text(), nullable=True),
            sa.Column("judgment", sa.String(32), nullable=True),
            sa.Column("attachments", postgresql.JSONB(), nullable=True),
            sa.Column("audited_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("audited_by_name", sa.String(64), nullable=True),
            sa.Column("audited_at", sa.DateTime(timezone=True), nullable=True),
            *_ts_cols(),
        )
        op.create_index("ix_complaint_audits_source_type", "complaint_audits", ["source_type"])
        op.create_index("ix_complaint_audits_device_sn", "complaint_audits", ["device_sn"])

    if not _has_table(bind, "upstream_tickets"):
        op.create_table(
            "upstream_tickets",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("issue", sa.Text(), nullable=False),
            sa.Column("upstream_dept", sa.String(64), nullable=False),
            sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="待响应"),
            sa.Column("source_report_id", sa.Integer(), nullable=True),
            sa.Column("score", sa.Integer(), nullable=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_by", sa.String(64), nullable=True),
            *_ts_cols(),
        )
        op.create_index("ix_upstream_tickets_upstream_dept", "upstream_tickets", ["upstream_dept"])
        op.create_index("ix_upstream_tickets_status", "upstream_tickets", ["status"])

    if not _has_table(bind, "data_quality_rules"):
        op.create_table(
            "data_quality_rules",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("target", sa.String(64), nullable=True),
            sa.Column("condition", postgresql.JSONB(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            *_ts_cols(),
        )


def downgrade() -> None:
    op.drop_table("data_quality_rules")
    op.drop_table("upstream_tickets")
    op.drop_table("complaint_audits")
    for col in ("diagram_urls", "replaced_by_policy_id", "replaces_policy_id", "topic_tags"):
        op.drop_column("policies", col)
