"""add urge & dispatch fields to service_tickets

催单风险预测 & 投诉关联分析 所需的 8 个新列:
  urge_count         催单次数(int)
  urge_status        催单状态(str)
  last_dispatch_time 最后派单时间(datetime tz)
  assignee           分配人员(str)
  contact_person     联系人员(str)
  dispatcher         派发人员(str)
  last_urge_by       最后催单人(str)
  last_urge_time     最后催单时间(datetime tz)

Revision ID: a7b8c9d0e1f2
Revises: 11bcdc63728f
Create Date: 2026-08-03 11:50:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'a6f7a8b9c1d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 催单风险预测 + 投诉关联分析 新增 8 列 (nullable, 不影响旧数据)
    op.add_column('service_tickets', sa.Column('urge_count', sa.Integer(), nullable=True, comment='催单次数'))
    op.add_column('service_tickets', sa.Column('urge_status', sa.String(length=32), nullable=True, comment='催单状态'))
    op.add_column(
        'service_tickets',
        sa.Column('last_dispatch_time', sa.DateTime(timezone=True), nullable=True, comment='最后派单时间'),
    )
    op.add_column('service_tickets', sa.Column('assignee', sa.String(length=64), nullable=True, comment='分配人员'))
    op.add_column('service_tickets', sa.Column('contact_person', sa.String(length=64), nullable=True, comment='联系人员'))
    op.add_column('service_tickets', sa.Column('dispatcher', sa.String(length=64), nullable=True, comment='派发人员'))
    op.add_column('service_tickets', sa.Column('last_urge_by', sa.String(length=64), nullable=True, comment='最后催单人'))
    op.add_column(
        'service_tickets',
        sa.Column('last_urge_time', sa.DateTime(timezone=True), nullable=True, comment='最后催单时间'),
    )
    # 催单次数 + 最后催单时间 是催单风险分析最常用的过滤/排序维度, 加索引(其他列筛选较少, 不建索引)
    op.create_index('ix_service_tickets_urge_count', 'service_tickets', ['urge_count'])
    op.create_index('ix_service_tickets_last_urge_time', 'service_tickets', ['last_urge_time'])


def downgrade() -> None:
    op.drop_index('ix_service_tickets_last_urge_time', table_name='service_tickets')
    op.drop_index('ix_service_tickets_urge_count', table_name='service_tickets')
    op.drop_column('service_tickets', 'last_urge_time')
    op.drop_column('service_tickets', 'last_urge_by')
    op.drop_column('service_tickets', 'dispatcher')
    op.drop_column('service_tickets', 'contact_person')
    op.drop_column('service_tickets', 'assignee')
    op.drop_column('service_tickets', 'last_dispatch_time')
    op.drop_column('service_tickets', 'urge_status')
    op.drop_column('service_tickets', 'urge_count')
