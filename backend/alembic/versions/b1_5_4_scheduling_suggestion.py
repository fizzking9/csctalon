"""add scheduling_suggestion to incidents for B1.5-4 排班联动

B1.5-4 (#235) 事件关联排班调整建议 — 字段登记:

incidents 新增 scheduling_suggestion (Text, nullable):
  - 经营事件登记时可填排班调整建议(如"推文后加早班1人"、"活动期间增开2号位")
  - 仅做字段登记, 排班页关联事件展示等后置于 B3-8 排班目标形态决策
  - 异常事故通常不涉及排班调整, 该字段留空

Revision ID: b1_5_4_scheduling_suggestion
Revises: b1_5_1_incident_event
Create Date: 2026-10-08 12:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b1_5_4_scheduling_suggestion'
down_revision: Union[str, None] = 'b1_5_1_incident_event'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 新增 scheduling_suggestion 字段(排班调整建议, 仅经营事件用, 可空)
    op.add_column(
        'incidents',
        sa.Column(
            'scheduling_suggestion',
            sa.Text(),
            nullable=True,
            comment='排班调整建议(经营事件用, 如"推文后加早班1人")',
        ),
    )


def downgrade() -> None:
    op.drop_column('incidents', 'scheduling_suggestion')
