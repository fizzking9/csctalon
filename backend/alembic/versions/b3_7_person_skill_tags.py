"""B3-7 (#255): 人员技能矩阵表 person_skill_tags

Revision ID: b3_7_person_skill_tags
Revises: b3_3_tag_rel_source
Create Date: 2026-10-09 18:30:00.000000

依据: 排期 §六 B3-7 人员管理重设计(技能矩阵)

范围:
1. 新建 person_skill_tags: 人员 × 技能标签 × 熟练度(1-5)
2. 唯一约束 (person_id, skill_tag): 同一人员同一技能仅一行, 重复打标幂等覆盖

downgrade: 纯新表, DROP 即安全回退。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b3_7_person_skill_tags'
down_revision: Union[str, None] = 'b3_3_tag_rel_source'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'person_skill_tags',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('person_id', sa.BigInteger(), nullable=False, comment='人员ID'),
        sa.Column('skill_tag', sa.String(length=64), nullable=False, comment='技能标签(如:投诉处理/套餐咨询/外呼)'),
        sa.Column('level', sa.Integer(), nullable=False, server_default='1', comment='熟练度 1-5'),
        sa.Column('updated_by', sa.String(length=64), nullable=True, comment='最近操作人'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('person_id', 'skill_tag', name='uq_person_skill_tag'),
    )
    op.create_index('ix_person_skill_tags_person_id', 'person_skill_tags', ['person_id'])
    op.create_index('ix_person_skill_tags_skill_tag', 'person_skill_tags', ['skill_tag'])


def downgrade() -> None:
    op.drop_index('ix_person_skill_tags_skill_tag', table_name='person_skill_tags')
    op.drop_index('ix_person_skill_tags_person_id', table_name='person_skill_tags')
    op.drop_table('person_skill_tags')
