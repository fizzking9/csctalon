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


def _column_exists(table_name: str, column_name: str) -> bool:
    """列存在性检查(幂等升级规范, 见 alembic-migration管理规范.md §五)

    已有表环境可能由 Base.metadata.create_all() 建表(含新列), 直接 add_column 会报
    DuplicateColumn → 必须先查 information_schema。
    限定 table_schema='public' 避免多 schema 同名表产生假阳性(审查 N-5)。
    """
    conn = op.get_bind()
    return bool(conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c)"
    ), {"t": table_name, "c": column_name}).scalar())


def _index_exists(index_name: str) -> bool:
    """索引存在性检查(幂等; 限定 schemaname='public', 审查 N-5)"""
    conn = op.get_bind()
    return bool(conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM pg_indexes "
        "WHERE schemaname = 'public' AND indexname = :i)"
    ), {"i": index_name}).scalar())


def upgrade() -> None:
    """投诉两表补 quality_status / quality_note(幂等: 已存在则跳过)"""
    for table in _TABLES:
        if not _column_exists(table, 'quality_status'):
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
        if not _column_exists(table, 'quality_note'):
            op.add_column(
                table,
                sa.Column(
                    'quality_note',
                    sa.String(length=512),
                    nullable=True,
                    comment='质量说明(自动纠错或待确认原因, 含原始值)',
                ),
            )
        idx = f'ix_{table}_quality_status'
        if not _index_exists(idx):
            op.create_index(idx, table, ['quality_status'])


def downgrade() -> None:
    """回退: 删索引与两列(幂等)

    ⚠️ 降级会物理删除两列 → 已写入的治理状态(pending/ok/auto_fixed 等)一并丢失且不可恢复。
    如需降级, 请先自行导出非空 quality_status/quality_note 作为备份。
    """
    for table in _TABLES:
        idx = f'ix_{table}_quality_status'
        if _index_exists(idx):
            op.drop_index(idx, table_name=table)
        if _column_exists(table, 'quality_note'):
            op.drop_column(table, 'quality_note')
        if _column_exists(table, 'quality_status'):
            op.drop_column(table, 'quality_status')
