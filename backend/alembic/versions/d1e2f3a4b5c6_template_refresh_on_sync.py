"""feat: ai_report_templates 新增 refresh_on_sync 模板级随同步刷新开关 (工单 #130)

Revision ID: d1e2f3a4b5c6
Revises: f4a5b6c7d8e9
Create Date: 2026-09-16 22:10:00.000000

设计:
- 刷新节奏模板化(用户诉求): 日报类内容时效性强, 需随高频数据同步及时预生成;
  周报/月报每日定时一次即可。是否随同步刷新应由模板决定(可手动选择/按周期自动识别)。
- 新列 refresh_on_sync BOOLEAN NOT NULL DEFAULT FALSE:
  * True  = 每次高频数据同步成功后刷新(warm_on_sync_complete 候选)
  * False = 仅按 trigger_at 定时(warm_idle; 周报/月报每日一次)
- 数据回填(自动识别存量): frequency in (daily, sync-triggered) 或 period='daily'
  的存量模板置 TRUE; weekly/monthly 置 FALSE(保持列默认)。
- daily 模板仍保留 trigger_at=09:00 作每日保底首版, refresh_on_sync 是"额外"高频刷新。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ai_report_templates',
        sa.Column(
            'refresh_on_sync',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # 自动识别存量: 日报类(daily 频率/周期, 或 sync-triggered)开启随同步刷新
    op.execute("""
        UPDATE ai_report_templates
        SET refresh_on_sync = TRUE
        WHERE frequency IN ('daily', 'sync-triggered') OR period = 'daily'
    """)


def downgrade() -> None:
    op.drop_column('ai_report_templates', 'refresh_on_sync')
