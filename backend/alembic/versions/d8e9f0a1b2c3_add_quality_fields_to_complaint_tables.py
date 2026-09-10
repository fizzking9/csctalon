"""add quality fields to complaint tables (#42)

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-10 12:25:00.000000

背景(#42, 2026-09-10):
  治理框架(A-20260908-003 拒收行 / A-20260909-001 治理闭环)覆盖了退款四路
  (refund/shanmao_refund/ticket/wallet 继承 QualityMixin, manual_refund 内联同名字段),
  但**投诉两表 external_complaints / card_complaints 没有质量字段** →
  入库前校验(ingest_validator)发现的问题无处落标, 无法进入质量页治理队列。

本次为两表补 QualityMixin 的 2 列(与既有表完全同口径, 不引入新语义):
  quality_status  String(16)  索引    None=正常 / ok / auto_fixed / pending /
                                      dup_residual / ignored
  quality_note    String(512)          质量说明(含原始值)

⚠️ 纯加列(可空, 无默认), 对既有数据与统计**零影响**:
  - 统计排除口径 COALESCE(quality_status,'') NOT IN ('dup_residual','ignored'),
    存量行均为 NULL → 全部保持纳入。
  - 打标(pending 等)由后续接入(本期仅建列, 不自动打标, 避免误标)。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('external_complaints', 'card_complaints')


def upgrade() -> None:
    """投诉两表补 quality_status / quality_note"""
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                'quality_status',
                sa.String(length=16),
                nullable=True,
                comment=(
                    '数据质量状态: None=正常 / ok=已人工确认 / auto_fixed=已自动纠错 / '
                    'pending=待人工确认 / dup_residual=已治理重复(排除) / ignored=人工彻底忽略(排除)'
                ),
            ),
        )
        op.add_column(
            table,
            sa.Column(
                'quality_note',
                sa.String(length=512),
                nullable=True,
                comment='质量说明(自动纠错或待确认原因, 含原始值)',
            ),
        )
        op.create_index(
            f'ix_{table}_quality_status',
            table,
            ['quality_status'],
        )


def downgrade() -> None:
    """回退: 删索引与两列"""
    for table in _TABLES:
        op.drop_index(f'ix_{table}_quality_status', table_name=table)
        op.drop_column(table, 'quality_note')
        op.drop_column(table, 'quality_status')
