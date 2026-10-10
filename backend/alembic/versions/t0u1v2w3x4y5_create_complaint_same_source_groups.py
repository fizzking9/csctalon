"""create complaint_same_source_groups / members

外部投诉同源判重 FINAL v5 落地（#208 / 草案 20260909 §5§6；#206 拍板 ③A④C⑤A）：
- complaint_same_source_groups  同源案件组（同 device_sn 连通分量，打标不删源数据）
- complaint_same_source_members 组成员（一行投诉至多属于一个有效组）
- 状态机 auto/needs_review → confirmed/rejected

Revision ID: t0u1v2w3x4y5
Revises: l3m4n5o6p7q8
Create Date: 2026-10-07 19:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 't0u1v2w3x4y5'
down_revision: Union[str, None] = 'l3m4n5o6p7q8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'complaint_same_source_groups',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('rule_version', sa.String(length=16), nullable=False, comment='判重规则版本（v5）'),
        sa.Column('device_sn', sa.String(length=64), nullable=False, comment='组设备 SN（同 SN 是 v5 硬前提）'),
        sa.Column('status', sa.String(length=16), nullable=False,
                  comment='auto=自动同源 / needs_review=待人工 / confirmed=人工确认 / rejected=人工驳回'),
        sa.Column('confidence', sa.Float(), nullable=True, comment='置信度=组内命中对最低相似度（保守值）'),
        sa.Column('first_complaint_date', sa.DateTime(timezone=True), nullable=True,
                  comment='案件登记日=组内最早 complaint_date（④C 取最早）'),
        sa.Column('latest_complaint_date', sa.DateTime(timezone=True), nullable=True,
                  comment='组内最晚 complaint_date（展示跨度用）'),
        sa.Column('member_count', sa.Integer(), server_default='2', nullable=False, comment='组成员数'),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='命中对证据 [{a,b,sim,daydiff,clues,embed,cross_cluster}]'),
        sa.Column('review_note', sa.String(length=512), nullable=True, comment='人工复核备注'),
        sa.Column('reviewed_by', sa.BigInteger(), nullable=True, comment='复核人 user id'),
        sa.Column('reviewed_name', sa.String(length=64), nullable=True, comment='复核人姓名'),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True, comment='复核时间(UTC aware)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        comment='外部投诉同源案件组（v5 打标不删源数据，#208）',
    )
    op.create_index('ix_same_source_groups_rule_version', 'complaint_same_source_groups', ['rule_version'])
    op.create_index('ix_same_source_groups_sn', 'complaint_same_source_groups', ['device_sn'])
    op.create_index('ix_same_source_groups_status', 'complaint_same_source_groups', ['status'])
    op.create_index('ix_same_source_groups_first_date', 'complaint_same_source_groups', ['first_complaint_date'])

    op.create_table(
        'complaint_same_source_members',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('group_id', sa.BigInteger(), nullable=False),
        sa.Column('complaint_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['complaint_same_source_groups.id'],
                                name='fk_same_source_member_group', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['complaint_id'], ['external_complaints.id'],
                                name='fk_same_source_member_complaint', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'complaint_id', name='uq_same_source_group_complaint'),
        sa.UniqueConstraint('complaint_id', name='uq_same_source_complaint_once'),
        comment='同源组成员（external_complaints 行打标，#208）',
    )
    op.create_index('ix_same_source_members_group_id', 'complaint_same_source_members', ['group_id'])
    op.create_index('ix_same_source_members_complaint_id', 'complaint_same_source_members', ['complaint_id'])


def downgrade() -> None:
    op.drop_index('ix_same_source_members_complaint_id', table_name='complaint_same_source_members')
    op.drop_index('ix_same_source_members_group_id', table_name='complaint_same_source_members')
    op.drop_table('complaint_same_source_members')
    op.drop_index('ix_same_source_groups_first_date', table_name='complaint_same_source_groups')
    op.drop_index('ix_same_source_groups_status', table_name='complaint_same_source_groups')
    op.drop_index('ix_same_source_groups_sn', table_name='complaint_same_source_groups')
    op.drop_index('ix_same_source_groups_rule_version', table_name='complaint_same_source_groups')
    op.drop_table('complaint_same_source_groups')
