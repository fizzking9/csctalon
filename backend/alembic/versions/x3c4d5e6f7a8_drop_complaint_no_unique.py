"""drop complaint_no unique constraint (dedup moved to dedup_hash)

Revision ID: x3c4d5e6f7a8
Revises: x2b3c4d5e6f7
Create Date: 2026-08-20 10:30:00.000000

背景:
- 315投诉表原以 complaint_no 去重(唯一索引 ix_external_complaints_complaint_no)。
- 但真实数据中同一投诉编号可对应多条不同投诉(如 12315 平台同一案件号多次派发,
  样例文件 27 个编号覆盖 1101 行, 含 "/" 垃圾编号 498 行), 唯一约束会导致
  同编号多条投诉互相覆盖/插入冲突。
- 2026-08-20 起去重改用 dedup_hash 身份指纹(编号|日期|手机号|SN|问题),
  complaint_no 仅作为普通检索字段 → 唯一索引降级为普通索引。
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'x3c4d5e6f7a8'
down_revision: Union[str, None] = 'x2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f('ix_external_complaints_complaint_no'), table_name='external_complaints')
    # 重新建普通索引(供按编号检索/排序; 非唯一)
    op.create_index(op.f('ix_external_complaints_complaint_no'), 'external_complaints', ['complaint_no'])


def downgrade() -> None:
    op.drop_index(op.f('ix_external_complaints_complaint_no'), table_name='external_complaints')
    # 恢复唯一索引(注意: 若存量数据已有同编号多行, downgrade 会失败, 需先清理)
    op.create_index(op.f('ix_external_complaints_complaint_no'), 'external_complaints', ['complaint_no'], unique=True)
