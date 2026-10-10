"""add staff_assignments table and person lifecycle columns

Revision ID: u0d1e2f3a4b5
Revises: t9c0d1e2f3a4
Create Date: 2026-07-30 10:00:00.000000

新增人员任职历史(SCD Type 2):
- staff_assignments: 岗位/专项/分组的有效期区间记录,支持"按当时任职"关联历史业务数据
- persons 补镜像字段(position/specialty)与生命周期字段(入职/试用/转正/离职/在职状态)

设计:
- 每人至多一条 is_current=true(部分唯一索引 uq_staff_assignments_current_per_person 保证)
- 当前岗位/专项/分组镜像到 persons 表,由 service 层同步,避免双写
- 生命周期为离散单点事件,挂 persons 标量列,不进 SCD2 表
"""

import sqlalchemy as sa

from alembic import op

revision = "u0d1e2f3a4b5"
down_revision = "t9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. staff_assignments 任职历史表
    op.create_table(
        "staff_assignments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False, comment="归属人员"),
        sa.Column("effective_from", sa.Date(), nullable=False, comment="生效起始日期"),
        sa.Column("effective_to", sa.Date(), nullable=True, comment="生效结束日期(null=至今/当前)"),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否当前任职(每人至多一条)"),
        sa.Column("position", sa.String(length=32), nullable=True, comment="岗位:客服/组长/主管"),
        sa.Column("specialty", sa.String(length=64), nullable=True, comment="专项:接待/投诉接待/主管"),
        sa.Column("group_name", sa.String(length=64), nullable=True, comment="分组"),
        sa.Column("change_reason", sa.Text(), nullable=True, comment="变更原因/备注"),
        sa.Column("source", sa.String(length=32), server_default="manual", nullable=False, comment="来源:manual/xlsx_import"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_staff_assignments_person_id", "staff_assignments", ["person_id"])
    op.create_index("ix_staff_assignments_effective_from", "staff_assignments", ["effective_from"])
    op.create_index("ix_staff_assignments_is_current", "staff_assignments", ["is_current"])
    # 复合索引: 点查询(某人在某日期的任职)走此索引
    op.create_index(
        "ix_staff_assignments_person_effective",
        "staff_assignments",
        ["person_id", "effective_from", "effective_to"],
    )
    # 部分唯一索引: 每人至多一条 is_current=true 的当前任职
    op.create_index(
        "uq_staff_assignments_current_per_person",
        "staff_assignments",
        ["person_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    # 2. persons 补镜像字段 + 生命周期字段
    op.add_column(
        "persons",
        sa.Column("position", sa.String(length=32), nullable=True, comment="当前岗位(镜像自最新任职):客服/组长/主管"),
    )
    op.add_column(
        "persons",
        sa.Column("specialty", sa.String(length=64), nullable=True, comment="当前专项(镜像自最新任职):接待/投诉接待/主管"),
    )
    op.add_column(
        "persons",
        sa.Column("hire_date", sa.Date(), nullable=True, comment="入职日期"),
    )
    op.add_column(
        "persons",
        sa.Column("probation_end_date", sa.Date(), nullable=True, comment="试用期到期日"),
    )
    op.add_column(
        "persons",
        sa.Column("regularization_date", sa.Date(), nullable=True, comment="转正日期"),
    )
    op.add_column(
        "persons",
        sa.Column("resignation_date", sa.Date(), nullable=True, comment="离职日期"),
    )
    op.add_column(
        "persons",
        sa.Column(
            "employment_status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
            comment="在职状态:active/probation/regular/resigned",
        ),
    )
    op.create_index("ix_persons_employment_status", "persons", ["employment_status"])


def downgrade() -> None:
    op.drop_index("ix_persons_employment_status", table_name="persons")
    op.drop_column("persons", "employment_status")
    op.drop_column("persons", "resignation_date")
    op.drop_column("persons", "regularization_date")
    op.drop_column("persons", "probation_end_date")
    op.drop_column("persons", "hire_date")
    op.drop_column("persons", "specialty")
    op.drop_column("persons", "position")

    op.drop_index("uq_staff_assignments_current_per_person", table_name="staff_assignments")
    op.drop_index("ix_staff_assignments_person_effective", table_name="staff_assignments")
    op.drop_index("ix_staff_assignments_is_current", table_name="staff_assignments")
    op.drop_index("ix_staff_assignments_effective_from", table_name="staff_assignments")
    op.drop_index("ix_staff_assignments_person_id", table_name="staff_assignments")
    op.drop_table("staff_assignments")
