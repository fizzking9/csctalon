"""add ticket_action_logs table (P2-T5)

工单操作流水表:记录一张工单生命周期内每一次业务动作,
用于精确还原「这张工单今天被谁做了什么」, 支撑客服工作量按「更新时间/动作时间」统计。

表结构:
  ticket_no         VARCHAR(64)   FK -> service_tickets.ticket_no (ON DELETE CASCADE)
  action_type       VARCHAR(32)   登记/派发/转派/催单/结单/取消/重开/改状态/改处理人/改服务项/上游更新/备注更新
  operator          VARCHAR(64)   操作人(中文名/企微名/system)
  action_at         TIMESTAMPTZ   动作发生时间 (登记/派发/催单那一刻, 不是落库时间)
  detail            JSONB         字段变更快照 (old_xxx / new_xxx)
  remark            TEXT          催单备注等文本

二级索引:
  ix_ticket_action_logs_ticket_no_action_at   (ticket_no, action_at DESC)   → 工单时间线查询
  ix_ticket_action_logs_operator_action_at    (operator, action_at DESC)    → 某客服当日动作
  ix_ticket_action_logs_action_at             (action_at)                   → 当日所有动作(今日看板统计)

Revision ID: c3e4f5a6b7c8
Revises: b2d3e4f5a6b7
Create Date: 2026-08-06 14:50:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3e4f5a6b7c8'
down_revision: Union[str, None] = 'b2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ticket_action_logs',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False,
                  comment='行创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False,
                  comment='行最后更新时间(一般不更新)'),
        sa.Column('ticket_no', sa.String(length=64), nullable=False,
                  comment='关联工单号'),
        sa.Column('action_type', sa.String(length=32), nullable=False,
                  comment='动作类型:登记/派发/转派/催单/结单/取消/重开/改状态/改处理人/改服务项/上游更新/备注更新'),
        sa.Column('operator', sa.String(length=64), nullable=True,
                  comment='操作人(中文姓名/企微名/系统/system)'),
        sa.Column('action_at', sa.DateTime(timezone=True), nullable=False,
                  comment='动作发生时间 (登记/派发/催单那一刻,不是落库时间)'),
        sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='动作细节快照, 如 {old_status:"在途", new_status:"已完成", ...}'),
        sa.Column('remark', sa.Text(), nullable=True,
                  comment='动作备注/原由(如催单备注文本)'),
        sa.ForeignKeyConstraint(['ticket_no'], ['service_tickets.ticket_no'], ondelete='CASCADE',
                                name='fk_ticket_action_logs_ticket_no'),
        sa.PrimaryKeyConstraint('id', name='pk_ticket_action_logs'),
    )
    op.create_index(
        'ix_ticket_action_logs_ticket_action',
        'ticket_action_logs',
        ['ticket_no', 'action_at'],
        unique=False,
    )
    op.create_index(
        'ix_ticket_action_logs_operator_action',
        'ticket_action_logs',
        ['operator', 'action_at'],
        unique=False,
    )
    op.create_index(
        'ix_ticket_action_logs_action_at',
        'ticket_action_logs',
        ['action_at'],
        unique=False,
    )
    op.create_index(
        'ix_ticket_action_logs_action_type',
        'ticket_action_logs',
        ['action_type'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_ticket_action_logs_action_type', table_name='ticket_action_logs')
    op.drop_index('ix_ticket_action_logs_action_at', table_name='ticket_action_logs')
    op.drop_index('ix_ticket_action_logs_operator_action', table_name='ticket_action_logs')
    op.drop_index('ix_ticket_action_logs_ticket_action', table_name='ticket_action_logs')
    op.drop_table('ticket_action_logs')
