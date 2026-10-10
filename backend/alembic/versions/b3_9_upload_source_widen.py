"""B3-9 (#248): upload_records.source 扩宽至 32 字符, 支持 shared_doc 批次台账

Revision ID: b3_9_upload_source_widen
Revises: b3_2_combo_dict
Create Date: 2026-10-09 17:00:00.000000

背景:
- S-P1-14: shared_doc_sync_service(#33) 走 ingest_service 入库但不落 upload_records,
  共享表格通道无批次台账/质量留痕, 可观测性与手工通道不对等。
- 本迁移仅扩宽 source 列(16→32), 枚举值扩展 manual/bms/shared_doc。

downgrade: 纯列宽回退(32→16), 无数据损失(shared_doc 值仅 10 字符)。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b3_9_upload_source_widen'
down_revision: Union[str, None] = 'b3_2_combo_dict'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'upload_records', 'source',
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'upload_records', 'source',
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=True,
    )
