"""add measures and incidents tables

Revision ID: p5e6f7a8b9c0
Revises: n4d5e6f7b8c9
Create Date: 2026-07-29 10:00:00.000000

新增管理闭环(v0.3) Phase 1 前两个模型:
- measures: 管理措施(审计/周报输出物,带「预期目标+验证时间+验证方式」到期回看)
- incidents: 异常事件登记(未通知的非预期事项,转化链路:异常→复盘→转预期→提前部署)

模块职责三分离: SpecialTask=外部专项执行 / TodoItem=日常待办 / Measure=管理措施(本表)
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "p5e6f7a8b9c0"
down_revision = "n4d5e6f7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # measures 管理措施
    op.create_table(
        "measures",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, comment="措施标题"),
        sa.Column("measure_type", sa.String(length=32), server_default="internal", nullable=False, comment="类型:内部管理/外部协调"),
        sa.Column("source", sa.String(length=32), server_default="manual", nullable=False, comment="来源:审计/周报/异常事件/手工"),
        sa.Column("owner_id", sa.Integer(), nullable=True, comment="负责人"),
        sa.Column("related_report_id", sa.Integer(), nullable=True, comment="关联报告(Report表待建,暂无FK)"),
        sa.Column("expected_goal", sa.Text(), nullable=True, comment="预期目标(量化或定性)"),
        sa.Column("verify_time", sa.DateTime(timezone=True), nullable=True, comment="验证时间(到期日)"),
        sa.Column("verify_method", sa.Text(), nullable=True, comment="验证方式(看哪个视图/指标/抽查)"),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False, comment="状态"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True, comment="生效起始"),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True, comment="生效结束"),
        sa.Column("review_result", sa.String(length=32), nullable=True, comment="回看结论:生效/部分生效/未生效/需调整"),
        sa.Column("review_note", sa.Text(), nullable=True, comment="回看备注"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True, comment="回看时间"),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True, comment="回看人"),
        sa.Column("created_by", sa.String(length=64), nullable=True, comment="创建人"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_measures_status", "measures", ["status"])
    op.create_index("ix_measures_verify_time", "measures", ["verify_time"])

    # incidents 异常事件登记
    op.create_table(
        "incidents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, comment="事项名"),
        sa.Column("subject", sa.String(length=255), nullable=True, comment="主体(涉及的业务/设备/系统)"),
        sa.Column("detail", sa.Text(), nullable=True, comment="明细描述"),
        sa.Column("registrar_id", sa.Integer(), nullable=True, comment="登记人"),
        sa.Column("impact", sa.Text(), nullable=True, comment="影响(量化:工单增量/涉及金额/影响人数)"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True, comment="发生时间"),
        sa.Column("duration_minutes", sa.Integer(), nullable=True, comment="持续时间(分钟)"),
        sa.Column("related_tickets", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="关联工单号列表"),
        sa.Column("status", sa.String(length=32), server_default="registered", nullable=False, comment="状态"),
        sa.Column("converted_to_expected", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否转为预期事项"),
        sa.Column("review_note", sa.Text(), nullable=True, comment="复盘结论/备注"),
        sa.Column("created_by", sa.String(length=64), nullable=True, comment="创建人"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["registrar_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_occurred_at", "incidents", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_incidents_occurred_at", table_name="incidents")
    op.drop_index("ix_incidents_status", table_name="incidents")
    op.drop_table("incidents")
    op.drop_index("ix_measures_verify_time", table_name="measures")
    op.drop_index("ix_measures_status", table_name="measures")
    op.drop_table("measures")
