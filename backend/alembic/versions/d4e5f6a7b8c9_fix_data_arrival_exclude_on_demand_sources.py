"""fix_data_arrival_exclude_on_demand_sources

修正 data_arrival 源发现: 排除"按需/低频"源(shanmao_refund)。

背景: c2d3e4f5a6b7 的 _active_fetch_sources 读 sync_source_configs.default_sync,
但该字段可能被用户改(生产 shanmao_refund 被改成 true), 而代码种子
BMS_DATA_SOURCES 中 shanmao_refund 是 default_sync=False + requires_captcha=True
(按需手动触发, 非每日必跑)。导致 shanmao_refund 被误纳入"每日必达"监控 →
每日 no_arrival 误报。

修复: 停用 data_arrival_shanmao_refund 指标与规则(每日必达监控只覆盖代码种子
default_sync=True 的 refund/ticket/wallet/cs_record + 被动推送源)。

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-09-04 17:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 按需/低频源(非每日必跑): 代码种子 BMS_DATA_SOURCES default_sync=False 或
# requires_captcha=True 的主动抓取源, 不应纳入"每日到达"严格监控
ON_DEMAND_FETCH_SOURCES = {"shanmao_refund"}


def upgrade() -> None:
    conn = op.get_bind()
    for dt in ON_DEMAND_FETCH_SOURCES:
        code = f"data_arrival_{dt}"
        conn.execute(sa.text("UPDATE metric_registry SET enabled=false WHERE code=:c"), {"c": code})
        conn.execute(sa.text("UPDATE alert_rules SET enabled=false WHERE metric_code=:c"), {"c": code})


def downgrade() -> None:
    conn = op.get_bind()
    for dt in ON_DEMAND_FETCH_SOURCES:
        code = f"data_arrival_{dt}"
        conn.execute(sa.text("UPDATE metric_registry SET enabled=true WHERE code=:c"), {"c": code})
        conn.execute(sa.text("UPDATE alert_rules SET enabled=true WHERE metric_code=:c"), {"c": code})
