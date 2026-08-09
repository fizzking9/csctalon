"""expand is_appeal from 16 to 64 & add lenience for ticket short-string cols

服务请求管理(ticket)导入历史上两次触发 StringDataRightTruncation:
  1. id=11~17: is_appeal VARCHAR(16) 过长
  2. id=259~293: updated_by_raw 键不一致 (已在 upsert.py 修复)

本迁移:
  - service_tickets.is_appeal: 16 → 64 (容纳「第N次申诉中」等复合状态)
  - channel / service_category / status 等短列统一扩展至 64, 避免以后新增状态再炸

Revision ID: aa11bb22cc33
Revises: 97862042a459
Create Date: 2026-08-07 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa11bb22cc33'
down_revision: Union[str, None] = '97862042a459'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 扩 is_appeal 16→64 (最迫切, 历史上多次报错)
    op.alter_column(
        'service_tickets', 'is_appeal',
        existing_type=sa.VARCHAR(length=16),
        type_=sa.VARCHAR(length=64),
        existing_nullable=True,
        existing_comment='是否申诉',
    )
    # 顺手把其余 <64 的分类/状态列统一扩到 64, 避免后续因上游状态扩展再返工
    for col, old_len, comment in [
        ('channel', 32, '录入渠道:H5'),
        ('service_category', 32, '服务类别:退款'),
        ('status', 32, '状态:待处理/已完成'),
        ('urge_status', 32, '催单状态'),
    ]:
        op.alter_column(
            'service_tickets', col,
            existing_type=sa.VARCHAR(length=old_len),
            type_=sa.VARCHAR(length=64),
            existing_nullable=True,
            existing_comment=comment,
        )
    # phone 32 保留 (E.164 最长 15 位, 32 已富余)


def downgrade() -> None:
    # 回滚不做实际截断, 仅变类型回去, 避免已存的 >16 数据被删
    for col, new_len in [
        ('urge_status', 32),
        ('status', 32),
        ('service_category', 32),
        ('channel', 32),
        ('is_appeal', 16),
    ]:
        op.alter_column(
            'service_tickets', col,
            existing_type=sa.VARCHAR(length=64),
            type_=sa.VARCHAR(length=new_len),
            existing_nullable=True,
        )
