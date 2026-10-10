"""report_templates + reports.framework (可编辑看板)

Revision ID: t9c0d1e2f3a4
Revises: s8b9c0d1e2f3
Create Date: 2026-07-29 19:00:00.000000

可编辑报告看板升级:
- report_templates: 看板模板(周报/专项报告/审计报告通用, 可自定义编辑)
- reports: 加 framework 字段(专项报告/审计报告也支持看板)
- 数据迁移: 10项标准框架 + weekly_section_bindings 合并写入"周报默认模板"种子
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t9c0d1e2f3a4"
down_revision = "s8b9c0d1e2f3"
branch_labels = None
depends_on = None


# 10项标准框架(与 weekly_report.WEEKLY_FRAMEWORK_SECTIONS 一致, 迁移幂等用)
WEEKLY_SECTIONS_JSON = (
    '[{"key":"measure_followup","title":"措施跟进","section_type":"measures"},'
    '{"key":"reception_trend","title":"总体接待人均趋势","section_type":"view"},'
    '{"key":"reception_summary","title":"分项接待汇总","section_type":"view"},'
    '{"key":"group_reception","title":"各组接待量","section_type":"view"},'
    '{"key":"refund_recovery","title":"退款挽回专项","section_type":"view"},'
    '{"key":"complaint","title":"投诉专项(内+外315)","section_type":"view"},'
    '{"key":"issues","title":"问题汇总","section_type":"text"},'
    '{"key":"measure_plan","title":"措施计划(输出下周)","section_type":"measures"},'
    '{"key":"cross_dept","title":"跨部门协调(含上游打分)","section_type":"text"},'
    '{"key":"incidents","title":"异常事故","section_type":"incidents"}]'
)


def upgrade() -> None:
    # 1. 创建 report_templates 表
    op.create_table(
        "report_templates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True, comment="唯一标识(预设用)"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="模板名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="说明"),
        sa.Column("report_type", sa.String(length=32), nullable=False, comment="类型:weekly/special_audit/custom"),
        sa.Column(
            "framework",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="section定义 [{key,title,section_type,view_ids[],owner_id}]",
        ),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否该类型默认模板"),
        sa.Column("is_preset", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否预设(不可删)"),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False, comment="active/archived"),
        sa.Column("visibility", sa.String(length=16), server_default="manager", nullable=False, comment="public/manager/admin"),
        sa.Column("owner_id", sa.Integer(), nullable=True, comment="所有者"),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False, comment="排序"),
        sa.Column("created_by", sa.String(length=64), nullable=True, comment="创建人"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_report_templates_code"),
    )
    op.create_index("ix_report_templates_report_type", "report_templates", ["report_type"])
    # 部分唯一索引: 每种 report_type 只有一个 is_default=true 的 active 模板
    op.create_index(
        "uq_report_templates_default_per_type",
        "report_templates",
        ["report_type"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND status = 'active'"),
    )
    op.create_foreign_key("fk_report_templates_owner_id_users", "report_templates", "users", ["owner_id"], ["id"])

    # 2. reports 加 framework 字段
    op.add_column(
        "reports",
        sa.Column("framework", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="看板框架(可选)"),
    )

    # 3. 数据迁移: 10项标准框架 + weekly_section_bindings 合并写入"周报默认模板"
    # framework 各 section 从 weekly_section_bindings 关联填充 view_ids/owner_id
    op.execute(
        f"""
        INSERT INTO report_templates (code, name, description, report_type, framework, is_default, is_preset, status, visibility, sort_order, created_by, created_at, updated_at)
        SELECT
            'preset_template_weekly',
            '周报默认模板',
            '原10项标准框架(从weekly_section_bindings迁移默认绑定)',
            'weekly',
            (
                SELECT jsonb_agg(jsonb_build_object(
                    'key', sec->>'key',
                    'title', sec->>'title',
                    'section_type', sec->>'section_type',
                    'view_ids', COALESCE(b.view_ids, '[]'::jsonb),
                    'owner_id', b.owner_id,
                    'summary_placeholder', ''
                ))
                FROM jsonb_array_elements('{WEEKLY_SECTIONS_JSON}'::jsonb) AS sec
                LEFT JOIN LATERAL (
                    SELECT view_ids, owner_id FROM weekly_section_bindings WHERE section_key = sec->>'key'
                ) b ON true
            ),
            true, true, 'active', 'manager', 0, 'system', now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM report_templates WHERE code = 'preset_template_weekly')
        """
    )


def downgrade() -> None:
    op.drop_column("reports", "framework")
    op.drop_index("uq_report_templates_default_per_type", table_name="report_templates")
    op.drop_index("ix_report_templates_report_type", table_name="report_templates")
    op.drop_constraint("fk_report_templates_owner_id_users", "report_templates", type_="foreignkey")
    op.drop_table("report_templates")
