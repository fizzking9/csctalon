"""add weekly_reports table

Revision ID: r7a8b9c0d1e2
Revises: q6f7a8b9c0d1
Create Date: 2026-07-29 17:00:00.000000

新增管理闭环(v0.3) Phase 1 周报模型:
- weekly_reports: 周报(10项标准框架, 复用 Report 快照/审计机制)
- framework JSONB 存储 10 项框架数据 [{key,title,summary,data,related_report_id}]
- 确认后固化为快照, 可持续批注/下钻审计
- 退款挽回 = cs_records 退款项接待SN − refunds 实际退款SN 的集合差(按设备SN核算)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "r7a8b9c0d1e2"
down_revision = "q6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekly_reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, comment="周报名"),
        sa.Column("week_start", sa.DateTime(timezone=True), nullable=True, comment="周起始(周一)"),
        sa.Column("week_end", sa.DateTime(timezone=True), nullable=True, comment="周结束(周日)"),
        sa.Column(
            "framework",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="10项框架数据 [{key,title,summary,data,related_report_id}]",
        ),
        sa.Column("summary", sa.Text(), nullable=True, comment="周报总评/概述"),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False, comment="状态:草稿/已确认/已归档"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True, comment="确认(固化)时间"),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True, comment="确认人"),
        sa.Column("created_by", sa.String(length=64), nullable=True, comment="创建人"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_weekly_reports_week_start", "weekly_reports", ["week_start"])
    op.create_index("ix_weekly_reports_status", "weekly_reports", ["status"])


def downgrade() -> None:
    op.drop_index("ix_weekly_reports_status", table_name="weekly_reports")
    op.drop_index("ix_weekly_reports_week_start", table_name="weekly_reports")
    op.drop_table("weekly_reports")
