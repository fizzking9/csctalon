"""add person department

Revision ID: w2b3c4d5e6f7
Revises: v1a2b3c4d5e6
Create Date: 2026-07-30

加 persons.department 字段(部门:企微客服部/店铺客服部,开放文本)
并回填现有 HR 人员 = '企微客服部'(店铺客服后续批量纳入时设店铺客服部)。
"""

from alembic import op
import sqlalchemy as sa

revision = "w2b3c4d5e6f7"
down_revision = "v1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "persons",
        sa.Column("department", sa.String(length=64), nullable=True, comment="部门:企微客服部/店铺客服部(开放文本)"),
    )
    op.create_index("ix_persons_department", "persons", ["department"])
    # 回填现有 HR 人员为企微客服部(店铺客服后续由批量纳入端点建 Person 时设店铺客服部)
    op.execute("UPDATE persons SET department = '企微客服部' WHERE department IS NULL")


def downgrade() -> None:
    op.drop_index("ix_persons_department", table_name="persons")
    op.drop_column("persons", "department")
