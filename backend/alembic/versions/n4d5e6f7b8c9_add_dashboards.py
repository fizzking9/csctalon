"""add dashboards table

Revision ID: n4d5e6f7b8c9
Revises: m3c4d5e6f7a8
Create Date: 2026-07-28 15:00:00.000000

新增看板表:
- dashboards: 存储看板配置(items JSONB 引用 AnalysisView.id)
- 启动时由 preset_dashboards_seed.ensure_preset_dashboards_registered 注册 4 个常规预设看板
  (退款/投诉/接待/绩效),复用现有 24 个预设视图组合
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "n4d5e6f7b8c9"
down_revision = "m3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=True, comment="预设看板代码 preset_dashboard_xxx"),
        sa.Column("name", sa.String(128), nullable=False, comment="看板名称"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False, comment="退款/投诉/接待/绩效/专项/自定义"),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment="[{view_id,view_code,title,span,visible}]"),
        sa.Column("time_range_default", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='{"days":30}'),
        sa.Column("is_preset", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False, comment="active/archived"),
        sa.Column("visibility", sa.String(16), server_default="manager", nullable=False, comment="public/manager/admin"),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_dashboards_code"),
    )
    op.create_index(op.f("ix_dashboards_code"), "dashboards", ["code"], unique=True)
    op.create_index("ix_dashboards_category", "dashboards", ["category"])
    op.create_index("ix_dashboards_status", "dashboards", ["status"])


def downgrade() -> None:
    op.drop_index("ix_dashboards_status", table_name="dashboards")
    op.drop_index("ix_dashboards_category", table_name="dashboards")
    op.drop_index(op.f("ix_dashboards_code"), table_name="dashboards")
    op.drop_table("dashboards")
