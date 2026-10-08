"""extend incidents with event_type / target_audience / start_at / end_at / impact_metrics

B1.5-1 (#232) 经营事件时间轴标注 - 表结构变更:

incidents 原表只支持「异常事故」登记(occurred_at + duration_minutes 表示点事件 +
持续时长)。本次扩展支持两种事件类型:
  - 异常事故 (默认, 与原语义一致): occurred_at + duration_minutes
  - 经营事件 (新增): 推文/广告/活动/定期维护等可预期事件, 用 start_at + end_at
    表示持续时间区间(点事件 end_at=null)

新增字段:
  - event_type       String(16) NOT NULL DEFAULT '异常事故'
                     取值: '经营事件' / '异常事故' (用 String 而非 PG ENUM,
                     与现有 status/severity/category 等同字段保持一致,
                     避免 ENUM 修改成本)
  - target_audience  Text, 推文/活动目标群体描述(经营事件用)
  - start_at         TIMESTAMPTZ, 经营事件生效起(含)
  - end_at           TIMESTAMPTZ, 经营事件生效止(含, NULL=点事件或无限期)
  - impact_metrics   JSONB, 量化影响指标 {key: value} 用于事件与数据对照(进
                     一步在 B1.5-3 趋势曲线叠加中消费)

不变字段:
  - occurred_at / duration_minutes 保留, 仅服务于「异常事故」类型; 经营事件
    也可填 occurred_at 表示事件发生时点(用于时间轴打点), 但通常 start_at/end_at
    更适合表达持续时间区间。

Revision ID: b1_5_1_incident_event
Revises: t0u1v2w3x4y5
Create Date: 2026-10-08 11:50:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'b1_5_1_incident_event'
down_revision: Union[str, None] = 't0u1v2w3x4y5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 新增列(全部 nullable / 有默认值, 与历史数据兼容)
    #    event_type 给默认值 '异常事故', 这样存量行无需回填即可读为「异常事故」
    op.add_column(
        'incidents',
        sa.Column(
            'event_type',
            sa.String(length=16),
            nullable=False,
            server_default='异常事故',
            comment='事件类型: 经营事件/异常事故(默认)',
        ),
    )
    op.add_column(
        'incidents',
        sa.Column(
            'target_audience',
            sa.Text(),
            nullable=True,
            comment='目标群体(经营事件用: 推文/活动受众描述)',
        ),
    )
    op.add_column(
        'incidents',
        sa.Column(
            'start_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='经营事件生效起始(含, 与 end_at 共同表达持续时间区间)',
        ),
    )
    op.add_column(
        'incidents',
        sa.Column(
            'end_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='经营事件生效结束(含, NULL=点事件或无限期)',
        ),
    )
    op.add_column(
        'incidents',
        sa.Column(
            'impact_metrics',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='量化影响指标 {key: value} (用于事件与数据对照)',
        ),
    )

    # 2) 索引: event_type 用于筛选(经营事件 vs 异常事故)
    #    start_at 用于时间轴查询(按生效起排序)
    op.create_index('ix_incidents_event_type', 'incidents', ['event_type'])
    op.create_index('ix_incidents_start_at', 'incidents', ['start_at'])


def downgrade() -> None:
    op.drop_index('ix_incidents_start_at', table_name='incidents')
    op.drop_index('ix_incidents_event_type', table_name='incidents')
    op.drop_column('incidents', 'impact_metrics')
    op.drop_column('incidents', 'end_at')
    op.drop_column('incidents', 'start_at')
    op.drop_column('incidents', 'target_audience')
    op.drop_column('incidents', 'event_type')
