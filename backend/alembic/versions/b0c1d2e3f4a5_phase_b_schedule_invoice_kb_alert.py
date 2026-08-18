"""Phase B: 排班 + 发票 + 知识库 + 预警

新增表:
- shift_templates / schedules / attendance (排班/班次/考勤)
- expense_invoices / reimbursements / reimbursement_items (发票/报销)
- kb_articles (知识库/话术)
- alert_rules / alerts (预警中心)

幂等: 全部 _table_exists 检查。

Revision ID: b0c1d2e3f4a5
Revises: a0b1c2d3e4f5
Create Date: 2026-08-18 01:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b0c1d2e3f4a5'
down_revision: Union[str, None] = 'a0b1c2d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _ts_cols():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "shift_templates"):
        op.create_table(
            "shift_templates",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("start_time", sa.String(5), nullable=False),
            sa.Column("end_time", sa.String(5), nullable=False),
            sa.Column("is_overnight", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("color", sa.String(16), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            *_ts_cols(),
        )

    if not _has_table(bind, "schedules"):
        op.create_table(
            "schedules",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("person_id", sa.Integer(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("schedule_date", sa.Date(), nullable=False),
            sa.Column("shift_template_id", sa.Integer(), sa.ForeignKey("shift_templates.id"), nullable=True),
            sa.Column("start_time", sa.String(5), nullable=True),
            sa.Column("end_time", sa.String(5), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="正常"),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
            sa.Column("created_by", sa.String(64), nullable=True),
            *_ts_cols(),
            sa.UniqueConstraint("person_id", "schedule_date", name="uq_schedules_person_date"),
        )
        op.create_index("ix_schedules_person_id", "schedules", ["person_id"])
        op.create_index("ix_schedules_schedule_date", "schedules", ["schedule_date"])

    if not _has_table(bind, "attendance"):
        op.create_table(
            "attendance",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("person_id", sa.Integer(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("attendance_date", sa.Date(), nullable=False),
            sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source", sa.String(32), nullable=False, server_default="wecom"),
            sa.Column("raw", postgresql.JSONB(), nullable=True),
            *_ts_cols(),
        )
        op.create_index("ix_attendance_person_id", "attendance", ["person_id"])
        op.create_index("ix_attendance_attendance_date", "attendance", ["attendance_date"])

    if not _has_table(bind, "expense_invoices"):
        op.create_table(
            "expense_invoices",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("invoice_code", sa.String(64), nullable=True),
            sa.Column("invoice_no", sa.String(64), nullable=True),
            sa.Column("invoice_type", sa.String(32), nullable=True),
            sa.Column("seller_name", sa.String(255), nullable=True),
            sa.Column("buyer_name", sa.String(255), nullable=True),
            sa.Column("amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("tax_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("invoice_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("file_url", sa.String(255), nullable=True),
            sa.Column("file_type", sa.String(32), nullable=True),
            sa.Column("ocr_result", postgresql.JSONB(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="待识别"),
            sa.Column("submitter_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("remark", sa.Text(), nullable=True),
            *_ts_cols(),
        )
        op.create_index("ix_expense_invoices_invoice_no", "expense_invoices", ["invoice_no"])
        op.create_index("ix_expense_invoices_status", "expense_invoices", ["status"])

    if not _has_table(bind, "reimbursements"):
        op.create_table(
            "reimbursements",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("batch_no", sa.String(64), nullable=False),
            sa.Column("title", sa.String(255), nullable=True),
            sa.Column("submitter_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("invoice_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(32), nullable=False, server_default="草稿"),
            sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("remark", sa.Text(), nullable=True),
            *_ts_cols(),
        )
        op.create_index("ix_reimbursements_batch_no", "reimbursements", ["batch_no"], unique=True)
        op.create_index("ix_reimbursements_status", "reimbursements", ["status"])

    if not _has_table(bind, "reimbursement_items"):
        op.create_table(
            "reimbursement_items",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("reimbursement_id", sa.Integer(), sa.ForeignKey("reimbursements.id", ondelete="CASCADE"), nullable=False),
            sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("expense_invoices.id"), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=True),
            *_ts_cols(),
        )
        op.create_index("ix_reimbursement_items_reimbursement_id", "reimbursement_items", ["reimbursement_id"])
        op.create_index("ix_reimbursement_items_invoice_id", "reimbursement_items", ["invoice_id"])

    if not _has_table(bind, "kb_articles"):
        op.create_table(
            "kb_articles",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("category", sa.String(64), nullable=True),
            sa.Column("question", sa.Text(), nullable=True),
            sa.Column("answer", sa.Text(), nullable=True),
            sa.Column("tags", postgresql.JSONB(), nullable=True),
            sa.Column("related_policy_id", sa.Integer(), sa.ForeignKey("policies.id"), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="草稿"),
            sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.String(64), nullable=True),
            *_ts_cols(),
        )
        op.create_index("ix_kb_articles_category", "kb_articles", ["category"])
        op.create_index("ix_kb_articles_status", "kb_articles", ["status"])

    if not _has_table(bind, "alert_rules"):
        op.create_table(
            "alert_rules",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("type", sa.String(32), nullable=False),
            sa.Column("condition", postgresql.JSONB(), nullable=True),
            sa.Column("threshold", sa.Float(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            *_ts_cols(),
        )
        op.create_index("ix_alert_rules_type", "alert_rules", ["type"])

    if not _has_table(bind, "alerts"):
        op.create_table(
            "alerts",
            sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
            sa.Column("rule_id", sa.Integer(), sa.ForeignKey("alert_rules.id"), nullable=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("level", sa.String(16), nullable=False, server_default="medium"),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            *_ts_cols(),
        )
        op.create_index("ix_alerts_occurred_at", "alerts", ["occurred_at"])
        op.create_index("ix_alerts_status", "alerts", ["status"])


def downgrade() -> None:
    for t in ("alerts", "alert_rules", "kb_articles", "reimbursement_items", "reimbursements",
              "expense_invoices", "attendance", "schedules", "shift_templates"):
        op.drop_table(t)
