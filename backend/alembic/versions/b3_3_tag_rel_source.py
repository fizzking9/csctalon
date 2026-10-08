"""B3-3 (#249): device_tag_rel 补业务事实来源列(source_table/source_id/reason)

Revision ID: b3_3_tag_rel_source
Revises: b3_9_upload_source_widen
Create Date: 2026-10-09 17:30:00.000000

依据: docs/arch/A-20261007-002 §3.6 (S-P1-13)
     排期 §六 B3-3

范围:
1. device_tag_rel 补 3 列可空来源列(老批次数据为 NULL, 与 batch_id 两来源并存)
2. 部分唯一约束: 同 device_sn+tag_id 当前有效仅一条(复合主键已约束,
   此处不加新约束, 只补注释说明)

downgrade: 纯新增列, DROP 即安全回退。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b3_3_tag_rel_source'
down_revision: Union[str, None] = 'b3_9_upload_source_widen'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('device_tag_rel', sa.Column('source_table', sa.String(length=32), nullable=True, comment='来源表名(可空)'))
    op.add_column('device_tag_rel', sa.Column('source_id', sa.String(length=64), nullable=True, comment='来源行主键值(可空)'))
    op.add_column('device_tag_rel', sa.Column('reason', sa.Text(), nullable=True, comment='打标原因(可空)'))


def downgrade() -> None:
    op.drop_column('device_tag_rel', 'reason')
    op.drop_column('device_tag_rel', 'source_id')
    op.drop_column('device_tag_rel', 'source_table')
