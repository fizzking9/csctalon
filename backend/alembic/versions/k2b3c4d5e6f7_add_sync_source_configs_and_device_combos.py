"""add sync_source_configs and device_combos tables

Revision ID: k2b3c4d5e6f7
Revises: h1a2b3c4d5e6
Create Date: 2026-07-27 16:00:00.000000

- sync_source_configs: BMS 同步源运行配置(URL/加密凭证/开关),与 data_sources 分离
- device_combos: 设备套餐导出表(对齐 BMS 设备套餐页导出列)
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "k2b3c4d5e6f7"
down_revision = "h1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. sync_source_configs 表
    op.create_table(
        "sync_source_configs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("data_type", sa.String(32), nullable=False, comment="refund/ticket/wallet/device_combo"),
        sa.Column("name", sa.String(64), nullable=False, comment="显示名"),
        sa.Column("base_url", sa.String(256), nullable=False, comment="BMS 基础地址"),
        sa.Column("page_path", sa.String(256), nullable=False, comment="页面路径"),
        sa.Column(
            "time_labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="时间筛选标签(多个时间框时按标签精确填)",
        ),
        sa.Column("username", sa.Text(), nullable=True, comment="BMS 账号(Fernet 加密)"),
        sa.Column("password", sa.Text(), nullable=True, comment="BMS 密码(Fernet 加密)"),
        sa.Column("default_sync", sa.Boolean(), server_default=sa.text("true"), nullable=False, comment="是否纳入定时同步/默认勾选"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False, comment="总开关"),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("data_type", name="uq_sync_source_configs_data_type"),
    )
    op.create_index("ix_sync_source_configs_data_type", "sync_source_configs", ["data_type"])

    # 2. device_combos 表
    op.create_table(
        "device_combos",
        sa.Column("recharge_order_no", sa.String(64), nullable=False, comment="充值订单号"),
        sa.Column("device_sn", sa.String(64), nullable=True, comment="设备SN"),
        sa.Column("package_name", sa.String(128), nullable=True, comment="套餐名称"),
        sa.Column("package_type", sa.String(64), nullable=True, comment="套餐类型:续费套餐"),
        sa.Column("traffic_type", sa.String(64), nullable=True, comment="流量类型:无限/固定流量"),
        sa.Column("merchant_name", sa.String(128), nullable=True, comment="商户名称"),
        sa.Column("actual_amount", sa.Numeric(12, 2), nullable=True, comment="实际支付金额"),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True, comment="开始日期"),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True, comment="结束日期"),
        sa.Column("updated_at_raw", sa.DateTime(timezone=True), nullable=True, comment="原始更新时间"),
        sa.Column("created_at_raw", sa.DateTime(timezone=True), nullable=True, comment="原始创建时间"),
        sa.Column("valid_days", sa.Integer(), nullable=True, comment="套餐有效天数"),
        sa.Column("creator", sa.String(64), nullable=True, comment="创建人:WeChat/SYS/Wallet"),
        sa.Column("available_traffic", sa.BigInteger(), nullable=True, comment="可用流量(字节,-1=无限)"),
        sa.Column("used_traffic", sa.BigInteger(), nullable=True, comment="已用流量(字节)"),
        sa.Column("remaining_traffic", sa.BigInteger(), nullable=True, comment="剩余流量(字节)"),
        sa.Column("status", sa.String(64), nullable=True, comment="当前状态:流量正常"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("recharge_order_no"),
        sa.ForeignKeyConstraint(["device_sn"], ["devices.sn"], name="fk_device_combos_device_sn"),
    )
    op.create_index("ix_device_combos_device_sn", "device_combos", ["device_sn"])
    op.create_index("ix_device_combos_package_type", "device_combos", ["package_type"])
    op.create_index("ix_device_combos_merchant_name", "device_combos", ["merchant_name"])
    op.create_index("ix_device_combos_created_at_raw", "device_combos", ["created_at_raw"])
    op.create_index("ix_device_combos_status", "device_combos", ["status"])


def downgrade() -> None:
    op.drop_index("ix_device_combos_status", table_name="device_combos")
    op.drop_index("ix_device_combos_created_at_raw", table_name="device_combos")
    op.drop_index("ix_device_combos_merchant_name", table_name="device_combos")
    op.drop_index("ix_device_combos_package_type", table_name="device_combos")
    op.drop_index("ix_device_combos_device_sn", table_name="device_combos")
    op.drop_table("device_combos")
    op.drop_index("ix_sync_source_configs_data_type", table_name="sync_source_configs")
    op.drop_table("sync_source_configs")
