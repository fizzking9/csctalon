"""add std_all view for return_tracking

Revision ID: e8b9c4d1f2a3
Revises: d7a8f3c2e9b1
Create Date: 2026-10-21

#338: 补齐 return_tracking 的 std_all 镜像视图,
供 audit_timeline(设备时序) 与 device_profile(生命周期 KPI) 读取。

新增视图（public schema, SELECT * 透传 ods）：
  std_return_tracking_all → ods.return_trackings
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8b9c4d1f2a3"
down_revision: Union[str, None] = "d7a8f3c2e9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VIEWS = {
    "std_return_tracking_all": "ods.return_trackings",
}


def upgrade() -> None:
    for view, src in _NEW_VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {src}")


def downgrade() -> None:
    for view in _NEW_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {view}")
