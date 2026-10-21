"""add std_*_all mirror views for complaints/device/scrm tables

Revision ID: c554d2b3e4f5
Revises: b443c1a2d3e4
Create Date: 2026-10-21

#338: 投诉/设备/SCRM 域业务代码零读 ods —— 全量镜像视图隔离 ods 直读。

新增视图（public schema, SELECT * 透传 ods）：
  std_card_complaint_all        → ods.card_complaints
  std_external_complaint_all    → ods.external_complaints
  std_cs_record_all             → ods.cs_records
  std_bms_order_all             → ods.bms_orders
  std_device_combo_all          → ods.device_combos
  std_device_flow_usage_all     → ods.device_flow_usages
  std_scrm_session_all          → ods.scrm_sessions

定位：
  - 业务层（analytics/api/services）一律读 std_*_all，禁止直读 ods；
  - ods 回归本职：采集写入；
  - 大表（cs_records / bms_orders）后续按需物化为精简 std_* 物理表 + 索引。

视图零存储、随 ods 实时一致。
"""
from typing import Sequence, Union

from alembic import op
from app.models.refund_standard import STD_ALL_VIEWS

revision: str = "c554d2b3e4f5"
down_revision: Union[str, None] = "b443c1a2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 仅本迁移新增的视图（退款五路已由 b443c1a2d3e4 创建）
_NEW_VIEWS = {k: v for k, v in STD_ALL_VIEWS.items() if not k.startswith("std_refund_") and k != "std_wallet_flow_all"}


def upgrade() -> None:
    for view, src in _NEW_VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {src}")


def downgrade() -> None:
    for view in _NEW_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {view}")
