"""add upload_records.source and data_sources.category

Revision ID: m3c4d5e6f7a8
Revises: k2b3c4d5e6f7
Create Date: 2026-07-28 10:00:00.000000

- upload_records.source: 导入来源 manual=手动上传 / bms=BMS同步(历史记录为 null)
- data_sources.category: 业务分组(数据查看左侧分组用)
  现金流 / 工单与客服 / 投诉登记 / 基础数据
  启动时 ensure_data_sources_registered 也会按种子刷新,此处回填保证迁移后立即可用
"""

import sqlalchemy as sa

from alembic import op

revision = "m3c4d5e6f7a8"
down_revision = "k2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. upload_records 增加 source 字段
    op.add_column(
        "upload_records",
        sa.Column("source", sa.String(16), nullable=True, comment="导入来源:manual=手动上传, bms=BMS同步;历史记录为null"),
    )
    op.create_index("ix_upload_records_source", "upload_records", ["source"])

    # 2. data_sources 增加 category 字段
    op.add_column(
        "data_sources",
        sa.Column("category", sa.String(32), nullable=True, comment="业务分组:现金流/工单与客服/投诉登记/基础数据"),
    )
    op.create_index("ix_data_sources_category", "data_sources", ["category"])

    # 3. 回填 category(与 DATA_SOURCE_SEEDS 保持一致)
    op.execute("UPDATE data_sources SET category = '现金流' WHERE code IN ('refund', 'wallet', 'merchant_refund')")
    op.execute("UPDATE data_sources SET category = '工单与客服' WHERE code IN ('ticket', 'cs_record')")
    op.execute("UPDATE data_sources SET category = '投诉登记' WHERE code IN ('external_complaint', 'card_complaint')")
    op.execute("UPDATE data_sources SET category = '基础数据' WHERE code = 'device_combo'")


def downgrade() -> None:
    op.drop_index("ix_data_sources_category", table_name="data_sources")
    op.drop_column("data_sources", "category")
    op.drop_index("ix_upload_records_source", table_name="upload_records")
    op.drop_column("upload_records", "source")
