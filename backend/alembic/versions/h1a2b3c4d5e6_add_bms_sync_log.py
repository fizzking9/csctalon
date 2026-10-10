"""add bms_sync_logs table and sync_source to data_sources

Revision ID: h1a2b3c4d5e6
Revises: 11bcdc63728f
Create Date: 2026-07-27 10:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "h1a2b3c4d5e6"
down_revision = "11bcdc63728f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 创建 bms_sync_logs 表
    op.create_table(
        "bms_sync_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(32), nullable=False, comment="refund/ticket/wallet"),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("trigger_type", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("triggered_by", sa.String(64), nullable=True),
        sa.Column("file_path", sa.String(256), nullable=True),
        sa.Column("filename", sa.String(128), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=True),
        sa.Column("inserted_rows", sa.Integer(), nullable=True),
        sa.Column("updated_rows", sa.Integer(), nullable=True),
        sa.Column("failed_rows", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bms_sync_logs_data_type", "bms_sync_logs", ["data_type"])

    # 2. 给 data_sources 表添加 sync_source 字段
    op.add_column("data_sources", sa.Column("sync_source", sa.String(32), nullable=True, comment="bms=从BMS系统同步, null=手动上传"))

    # 3. 标注三个 BMS 同步数据源
    op.execute("UPDATE data_sources SET sync_source = 'bms' WHERE code IN ('refund', 'ticket', 'wallet')")


def downgrade() -> None:
    op.drop_column("data_sources", "sync_source")
    op.drop_index("ix_bms_sync_logs_data_type", table_name="bms_sync_logs")
    op.drop_table("bms_sync_logs")
