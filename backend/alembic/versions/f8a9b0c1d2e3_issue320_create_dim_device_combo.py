"""#320 P1: 新建 dim_device_combo 干净维度表 (SCD Type 2)

设备-套餐时点快照: 每台设备套餐变化的历史区间, 供退款/投诉两侧按事件时点
JOIN 取当时套餐, 统一两侧口径(消除 #311/#313 漂移面)。

字段要点:
- (device_sn, valid_from) 唯一键, is_current 标记当前生效行;
- valid_to=NULL 表示当前仍生效; 历史行 valid_to 为下一行 valid_from;
- 质检套餐在构建期过滤, 本表只存真实套餐。

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-10-13 23:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: str = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dim_device_combo",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_sn", sa.String(length=64), nullable=False, comment="设备 SN"),
        sa.Column(
            "package_name", sa.String(length=128), nullable=False,
            comment="套餐名(已过滤工厂质检)",
        ),
        sa.Column("source", sa.String(length=16), nullable=False, comment="来源: bms_order / device_combo"),
        sa.Column("order_no", sa.String(length=64), nullable=True, comment="来源单号(可空)"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, comment="区间起(含)"),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True, comment="区间止(不含); NULL=当前"),
        sa.Column("is_current", sa.Boolean(), nullable=False, comment="当前生效标记"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_sn", "valid_from", name="uq_dim_device_combo_sn_from"),
    )
    op.create_index(
        "ix_dim_device_combo_sn_current", "dim_device_combo", ["device_sn", "is_current"],
    )
    op.create_index(
        "ix_dim_device_combo_sn_range", "dim_device_combo", ["device_sn", "valid_from", "valid_to"],
    )


def downgrade() -> None:
    op.drop_index("ix_dim_device_combo_sn_range", table_name="dim_device_combo")
    op.drop_index("ix_dim_device_combo_sn_current", table_name="dim_device_combo")
    op.drop_table("dim_device_combo")
