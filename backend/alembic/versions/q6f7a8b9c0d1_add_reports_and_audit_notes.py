"""add reports and audit_notes tables

Revision ID: q6f7a8b9c0d1
Revises: p5e6f7a8b9c0
Create Date: 2026-07-29 11:00:00.000000

新增管理闭环(v0.3) Phase 1 核心模型:
- reports: 报告(数据快照,审计前提)。confirm 时固化 snapshot(视图配置+关键结果JSONB),不再随源数据变
- audit_notes: 审计附注(结构化:结论/评判/抽检工单/贴图/输出措施),FK→reports + measures + users
- 补 measures.related_report_id 的 FK→reports(Phase 1 前置 Measure 表无 FK,现补齐)

"只有报告才能审计": AuditNote.report_id NOT NULL FK。
审计→措施闭环: AuditNote.output_measure_id FK→measures(可选)。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "q6f7a8b9c0d1"
down_revision = "p5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # reports 报告
    op.create_table(
        "reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, comment="报告名"),
        sa.Column("report_type", sa.String(length=32), server_default="custom", nullable=False, comment="类型:周报/专项审计/自定义"),
        sa.Column("time_start", sa.DateTime(timezone=True), nullable=True, comment="锁定时间起"),
        sa.Column("time_end", sa.DateTime(timezone=True), nullable=True, comment="锁定时间止"),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="锁定的筛选条件"),
        sa.Column("view_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="包含的视图引用列表"),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="视图配置+执行结果快照(关键数据)"),
        sa.Column("pdf_archive", sa.String(length=512), nullable=True, comment="归档PDF路径"),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False, comment="状态:草稿/已确认/已归档"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True, comment="确认(固化)时间"),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True, comment="确认人"),
        sa.Column("created_by", sa.String(length=64), nullable=True, comment="创建人"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reports_report_type", "reports", ["report_type"])
    op.create_index("ix_reports_status", "reports", ["status"])

    # audit_notes 审计附注
    op.create_table(
        "audit_notes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False, comment="关联报告"),
        sa.Column("conclusion", sa.Text(), nullable=True, comment="审计结论"),
        sa.Column("judgment", sa.String(length=32), nullable=True, comment="评判:通过/有问题/需改进"),
        sa.Column("sampled_tickets", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="抽检工单号列表"),
        sa.Column("attachments", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="贴图附件列表 [{path,url}]"),
        sa.Column("output_measure_id", sa.Integer(), nullable=True, comment="输出的管理措施(派生Measure)"),
        sa.Column("author_id", sa.Integer(), nullable=True, comment="审计人"),
        sa.Column("author_name", sa.String(length=64), nullable=True, comment="审计人姓名"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"]),
        sa.ForeignKeyConstraint(["output_measure_id"], ["measures.id"]),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_notes_report_id", "audit_notes", ["report_id"])

    # 补 measures.related_report_id 的 FK(Phase 1 前置 Measure 表无 FK,现 reports 表已建,补齐)
    op.create_foreign_key(
        "fk_measures_related_report_id",
        "measures",
        "reports",
        ["related_report_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_measures_related_report_id", "measures", type_="foreignkey")
    op.drop_index("ix_audit_notes_report_id", table_name="audit_notes")
    op.drop_table("audit_notes")
    op.drop_index("ix_reports_status", table_name="reports")
    op.drop_index("ix_reports_report_type", table_name="reports")
    op.drop_table("reports")
