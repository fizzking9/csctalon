"""add previous_content_md to integration_profiles (工单 #47 模板版本历史与回滚)

Revision ID: x9y0z1a2b3c4
Revises: b3d4e5f6a7c8
Create Date: 2026-09-18 11:00:00.000000

设计(#47 C 方案):
  在 integration_profiles 加 previous_content_md TEXT NULL 单字段。
  每次 update_profile 前由 service 把旧 content_md 快照到 previous_content_md;
  rollback_profile 把 previous 写回 content_md, 同时把当前 content_md 再存为 previous
  (可逆回滚 —— 回滚动作本身也是一次「保存当前为 previous」)。

  选择单字段而非新表的理由:
    - 接入模板低频改动, 一版回滚足够覆盖「误改」场景(多版历史属于过度设计);
    - 无新表 → 无 ORM 关系/级联/迁移风险, 改动面最小;
    - 与 #44 LOCKED 文档不冲突(只增字段, 不改既有结构)。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'x9y0z1a2b3c4'
down_revision: Union[str, None] = 'b3d4e5f6a7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 加 previous_content_md 字段: 上一版 content_md 快照, 用于回滚
    op.add_column(
        'integration_profiles',
        sa.Column(
            'previous_content_md',
            sa.Text(),
            nullable=True,
            comment='上一版 content_md 快照(每次 update 前写入, 回滚时读回; NULL=无历史版本)',
        ),
    )


def downgrade() -> None:
    op.drop_column('integration_profiles', 'previous_content_md')
