"""ai_reports 扩字段: template_id/snapshot_date/snapshot_period/is_public/matched_count (工单 #121)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-09-16 13:30:00.000000

设计:
- ai_reports 表加 5 字段, 支撑预生成报告与匹配命中复用统计
- template_id FK ai_report_templates ON DELETE SET NULL(模板被删时报告保留)
- snapshot_date/snapshot_period 报告数据时点(区别于 created_at 入库时点)
- is_public 区分公共报告(预生成, 所有人可看)与私有报告(实时, 仅本人)
- matched_count 命中 +1(用 UPDATE ... RETURNING 保证并发安全, 业务层负责)
- 2 索引: (template_id, snapshot_date) 匹配查询用; (is_public, created_at) 公共报告列表用
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 5 新增字段
    op.add_column('ai_reports', sa.Column(
        'template_id', sa.Integer(), nullable=True,
        comment='#121: 来自预生成则为模板 id, 实时生成则 NULL',
    ))
    op.add_column('ai_reports', sa.Column(
        'snapshot_date', sa.Date(), nullable=True,
        comment='#121: 报告数据时点(窗口起算 date, 区别于 created_at 的入库时点)',
    ))
    op.add_column('ai_reports', sa.Column(
        'snapshot_period', sa.String(length=16), nullable=True,
        comment='#121: daily/weekly/monthly/range(与模板 period 对应)',
    ))
    op.add_column('ai_reports', sa.Column(
        'is_public', sa.Boolean(), nullable=False, server_default=sa.false(),
        comment='#121: true=公共报告(所有人可看, 来自预生成); false=私有(仅本人, 来自实时)',
    ))
    op.add_column('ai_reports', sa.Column(
        'matched_count', sa.Integer(), nullable=False, server_default='0',
        comment='#121: 被命中复用次数(match_template_report 命中时 +1)',
    ))

    # FK: template_id -> ai_report_templates.id ON DELETE SET NULL
    op.create_foreign_key(
        'fk_ai_reports_template_id',
        'ai_reports', 'ai_report_templates',
        ['template_id'], ['id'],
        ondelete='SET NULL',
    )

    # 索引
    op.create_index(
        'ix_ai_reports_template_snapshot',
        'ai_reports', ['template_id', 'snapshot_date'],
    )
    op.create_index(
        'ix_ai_reports_public_created',
        'ai_reports', ['is_public', 'created_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_ai_reports_public_created', table_name='ai_reports')
    op.drop_index('ix_ai_reports_template_snapshot', table_name='ai_reports')
    op.drop_constraint('fk_ai_reports_template_id', 'ai_reports', type_='foreignkey')
    op.drop_column('ai_reports', 'matched_count')
    op.drop_column('ai_reports', 'is_public')
    op.drop_column('ai_reports', 'snapshot_period')
    op.drop_column('ai_reports', 'snapshot_date')
    op.drop_column('ai_reports', 'template_id')
