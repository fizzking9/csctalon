"""weekly kanban upgrade: meeting_conclusion + bindings + comments + report drill-down

Revision ID: s8b9c0d1e2f3
Revises: r7a8b9c0d1e2
Create Date: 2026-07-29 18:00:00.000000

周报看板升级(用户确认的工作流):
- weekly_reports: 新增 meeting_conclusion(会议评审结论)
- weekly_section_bindings: 分项默认绑定配置(管理员配一次, 一键生成自动填充)
- report_comments: 统一跟进注释(周报/报告通用, 可派生措施)
- reports: 新增 assignee_id/due_date/source_weekly_id/source_section_key(专项下钻指派+回链)
- 数据迁移: framework 旧格式 view_id(单值)→view_ids(列表), related_report_id→related_report_ids
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "s8b9c0d1e2f3"
down_revision = "r7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. weekly_reports 加 meeting_conclusion
    op.add_column(
        "weekly_reports",
        sa.Column("meeting_conclusion", sa.Text(), nullable=True, comment="会议评审结论(周会记录,确认时固化)"),
    )

    # 2. reports 加专项下钻字段
    op.add_column(
        "reports",
        sa.Column("assignee_id", sa.Integer(), nullable=True, comment="指派负责人(专项下钻: 安排谁审查)"),
    )
    op.create_foreign_key("fk_reports_assignee_id_users", "reports", "users", ["assignee_id"], ["id"])
    op.add_column(
        "reports",
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True, comment="要求提交时间(专项下钻: 期限)"),
    )
    op.add_column(
        "reports",
        sa.Column("source_weekly_id", sa.Integer(), nullable=True, comment="来源周报ID(从周报某项下钻创建时回链)"),
    )
    op.add_column(
        "reports",
        sa.Column("source_section_key", sa.String(length=64), nullable=True, comment="来源周报分项key(从周报某项下钻创建时回链)"),
    )

    # 3. weekly_section_bindings 表
    op.create_table(
        "weekly_section_bindings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("section_key", sa.String(length=64), nullable=False, comment="对应 WEEKLY_FRAMEWORK_SECTIONS 的 key"),
        sa.Column("view_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="默认绑定的分析视图ID列表"),
        sa.Column("owner_id", sa.Integer(), nullable=True, comment="默认负责人"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False, comment="是否启用"),
        sa.Column("created_by", sa.String(length=64), nullable=True, comment="创建人"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_key", name="uq_weekly_section_bindings_section_key"),
    )
    op.create_foreign_key("fk_weekly_section_bindings_owner_id_users", "weekly_section_bindings", "users", ["owner_id"], ["id"])

    # 4. report_comments 表
    op.create_table(
        "report_comments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False, comment="目标类型: weekly/report"),
        sa.Column("target_id", sa.Integer(), nullable=False, comment="目标ID(周报ID/报告ID)"),
        sa.Column("section_key", sa.String(length=64), nullable=True, comment="定位到周报某分项(报告时为空)"),
        sa.Column("content", sa.Text(), nullable=False, comment="注释内容"),
        sa.Column("author_id", sa.Integer(), nullable=True, comment="作者"),
        sa.Column("author_name", sa.String(length=64), nullable=True, comment="作者姓名"),
        sa.Column("output_measure_id", sa.Integer(), nullable=True, comment="派生的管理措施ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_comments_target_type", "report_comments", ["target_type"])
    op.create_index("ix_report_comments_target_id", "report_comments", ["target_id"])

    # 5. 数据迁移: framework 旧格式 view_id(单值)→view_ids(列表), related_report_id→related_report_ids
    # PostgreSQL JSONB 处理: 遍历 framework 数组, 补齐新字段
    op.execute(
        """
        UPDATE weekly_reports
        SET framework = (
            SELECT jsonb_agg(
                CASE
                    WHEN (elem ? 'view_id') AND NOT (elem ? 'view_ids') THEN
                        elem || jsonb_build_object(
                            'view_ids',
                            CASE WHEN elem->>'view_id' IS NOT NULL AND (elem->>'view_id') != ''
                                 THEN jsonb_build_array((elem->>'view_id')::int)
                                 ELSE '[]'::jsonb
                            END
                        ) - 'view_id'
                    WHEN NOT (elem ? 'view_ids') THEN
                        elem || jsonb_build_object('view_ids', '[]'::jsonb)
                    ELSE elem
                END
            )
            FROM jsonb_array_elements(framework) AS elem
        )
        WHERE jsonb_typeof(framework) = 'array'
        """
    )
    op.execute(
        """
        UPDATE weekly_reports
        SET framework = (
            SELECT jsonb_agg(
                CASE
                    WHEN (elem ? 'related_report_id') AND NOT (elem ? 'related_report_ids') THEN
                        elem || jsonb_build_object(
                            'related_report_ids',
                            CASE WHEN elem->>'related_report_id' IS NOT NULL AND (elem->>'related_report_id') != ''
                                 THEN jsonb_build_array((elem->>'related_report_id')::int)
                                 ELSE '[]'::jsonb
                            END
                        ) - 'related_report_id'
                    WHEN NOT (elem ? 'related_report_ids') THEN
                        elem || jsonb_build_object('related_report_ids', '[]'::jsonb)
                    ELSE elem
                END
            )
            FROM jsonb_array_elements(framework) AS elem
        )
        WHERE jsonb_typeof(framework) = 'array'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_report_comments_target_id", table_name="report_comments")
    op.drop_index("ix_report_comments_target_type", table_name="report_comments")
    op.drop_table("report_comments")
    op.drop_table("weekly_section_bindings")
    op.drop_column("reports", "source_section_key")
    op.drop_column("reports", "source_weekly_id")
    op.drop_column("reports", "due_date")
    op.drop_constraint("fk_reports_assignee_id_users", "reports", type_="foreignkey")
    op.drop_column("reports", "assignee_id")
    op.drop_column("weekly_reports", "meeting_conclusion")
