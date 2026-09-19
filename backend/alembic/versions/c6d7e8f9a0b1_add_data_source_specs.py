"""data_source_specs 接入声明表 (工单 #155)

AI 数据接入助手「接入声明层」: 一行 = 一次数据源接入意图（AI 草稿 / 已确认 / 已落地）。
声明主体字段对齐 data_sources（确认后可直接映射），AI 产物（field_specs /
dedup_recommendation）与校验结果（validator_report）以 JSONB 存储，
保证「AI 建议、规则校验、人工确认」三层可分别追责。

本迁移只建表，不触碰 data_sources / data_source_fields（现有 registry 机制不变）。

Revision ID: c6d7e8f9a0b1
Revises: e4f5a6b7c8d9
Create Date: 2026-09-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_source_specs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("name_cn", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="999"),
        sa.Column("time_field", sa.String(length=64), nullable=True),
        sa.Column("sn_field", sa.String(length=64), nullable=True),
        sa.Column("pk_field", sa.String(length=64), nullable=True),
        sa.Column("pk_strategy", sa.String(length=32), nullable=False, server_default="primary_key"),
        sa.Column("sync_source", sa.String(length=32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("issue_no", sa.String(length=16), nullable=True),
        sa.Column("sample_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("field_specs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dedup_recommendation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("validator_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("apply_package", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_by", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("code", name="uq_data_source_specs_code"),
    )
    op.create_index("ix_data_source_specs_code", "data_source_specs", ["code"], unique=True)
    op.create_index("ix_data_source_specs_status", "data_source_specs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_data_source_specs_status", table_name="data_source_specs")
    op.drop_index("ix_data_source_specs_code", table_name="data_source_specs")
    op.drop_table("data_source_specs")
