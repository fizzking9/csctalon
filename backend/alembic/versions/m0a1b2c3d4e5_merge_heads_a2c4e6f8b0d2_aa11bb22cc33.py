"""merge heads: a2c4e6f8b0d2 + aa11bb22cc33

合并两个分支 head, 统一 alembic 版本链为单一 head:
  - 分支A (a2c4e6f8b0d2): add policies and dept_syncs tables
  - 分支B (aa11bb22cc33): expand ticket short varchars

两条分支均从 z9y8x7w6v5u4 (add_scrm_models) 分叉, 此 merge 把它们汇合,
后续 migration (如 group/specialty 建表) 可在单一 head 上继续追加.

Revision ID: m0a1b2c3d4e5
Revises: a2c4e6f8b0d2, aa11bb22cc33
Create Date: 2026-08-17 10:00:00.000000
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'm0a1b2c3d4e5'
down_revision: Union[str, Sequence[str], None] = ('a2c4e6f8b0d2', 'aa11bb22cc33')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # merge migration: 无需 DDL 操作, 仅合并版本链
    pass


def downgrade() -> None:
    # merge migration 不支持 downgrade (会重新分裂为两个 head)
    pass
