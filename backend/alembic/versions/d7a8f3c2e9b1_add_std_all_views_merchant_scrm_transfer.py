"""add std_all views for merchant_refund and scrm_session_transfer

Revision ID: d7a8f3c2e9b1
Revises: c554d2b3e4f5
Create Date: 2026-10-21

#338: 补齐 merchant_refund / scrm_session_transfer 的 std_all 镜像视图,
供 analysis_engine._MODEL_REGISTRY 与 person_extractor.PERSON_FIELD_SOURCES 读取。

新增视图（public schema, SELECT * 透传 ods）：
  std_merchant_refund_all       → ods.merchant_refunds
  std_scrm_session_transfer_all → ods.scrm_session_transfers
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7a8f3c2e9b1"
down_revision: Union[str, None] = "c554d2b3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VIEWS = {
    "std_merchant_refund_all": "ods.merchant_refunds",
    "std_scrm_session_transfer_all": "ods.scrm_session_transfers",
}


def upgrade() -> None:
    for view, src in _NEW_VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {src}")


def downgrade() -> None:
    for view in _NEW_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {view}")
