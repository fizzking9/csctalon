"""add persons current_group_id and primary_specialty columns

补齐 persons 表的两个镜像列:
- current_group_id: 当前行政分组ID (FK→groups.id)
- primary_specialty: 当前主专项(镜像,从person_specialty_assignments同步)

根因: n1b2c3d4e5f6 只创建了 6 张新表,漏了对已有 persons 表的 ALTER TABLE ADD COLUMN。
本地环境通过 Base.metadata.create_all() 自动补了这两列,但新环境只跑 alembic upgrade head 会缺列。

Revision ID: o1b2c3d4e5f6
Revises: n1b2c3d4e5f6
Create Date: 2026-08-17 20:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'o1b2c3d4e5f6'
down_revision: Union[str, None] = 'n1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c)"
    ), {"t": table_name, "c": column_name})
    return bool(result.scalar())


def _constraint_exists(constraint_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM information_schema.table_constraints "
        "WHERE table_schema='public' AND constraint_name=:c)"
    ), {"c": constraint_name})
    return bool(result.scalar())


def _index_exists(index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname=:i)"
    ), {"i": index_name})
    return bool(result.scalar())


def upgrade() -> None:
    # 1. current_group_id (FK→groups.id, nullable, indexed)
    if not _column_exists('persons', 'current_group_id'):
        op.add_column(
            'persons',
            sa.Column('current_group_id', sa.BigInteger(), nullable=True,
                      comment='当前行政分组ID'),
        )

    # 不管列是新创建还是已存在(Base.metadata.create_all 可能已建列但 FK/索引静默失败),
    # 都要检查并补齐 FK 约束和索引
    if not _index_exists('ix_persons_current_group_id'):
        op.create_index('ix_persons_current_group_id', 'persons', ['current_group_id'])
    if not _constraint_exists('persons_current_group_id_fkey'):
        op.create_foreign_key(
            'persons_current_group_id_fkey', 'persons', 'groups',
            ['current_group_id'], ['id'],
        )

    # 2. primary_specialty (String(64), nullable)
    if not _column_exists('persons', 'primary_specialty'):
        op.add_column(
            'persons',
            sa.Column('primary_specialty', sa.String(length=64), nullable=True,
                      comment='当前主专项(镜像,从person_specialty_assignments同步)'),
        )

    # 3. 兜底: specialty 兼容字段 (早期 persons 表可能也缺)
    if not _column_exists('persons', 'specialty'):
        op.add_column(
            'persons',
            sa.Column('specialty', sa.String(length=64), nullable=True,
                      comment='当前专项(兼容字段,同primary_specialty)'),
        )


def downgrade() -> None:
    if _constraint_exists('persons_current_group_id_fkey'):
        op.drop_constraint('persons_current_group_id_fkey', 'persons', type_='foreignkey')
    if _index_exists('ix_persons_current_group_id'):
        op.drop_index('ix_persons_current_group_id', table_name='persons')
    if _column_exists('persons', 'current_group_id'):
        op.drop_column('persons', 'current_group_id')
    if _column_exists('persons', 'primary_specialty'):
        op.drop_column('persons', 'primary_specialty')
    # specialty 不回滚 (可能被其他 migration 创建)
