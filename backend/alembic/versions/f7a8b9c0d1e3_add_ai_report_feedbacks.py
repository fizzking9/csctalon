"""AI 报告用户评价表 ai_report_feedbacks (工单 #174 演进)

用户在报告/对话结果页打分(1~5)+留意见，**只采集不实时分析**；
评估中心按周期汇总归因（低分报告/高频意见）驱动提示词迭代。

幂等：表已存在（本地 create_all 环境可能已建）则跳过。

Revision ID: f7a8b9c0d1e3
Revises: e6f7a8b9c0d2
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'f7a8b9c0d1e3'
down_revision: Union[str, None] = 'e6f7a8b9c0d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("ai_report_feedbacks"):
        return
    op.create_table(
        'ai_report_feedbacks',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('report_id', sa.BigInteger(), nullable=False, comment='被评价的 AI 报告 id'),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='评价人 user_id'),
        sa.Column('rating', sa.Integer(), nullable=False, comment='评分 1~5'),
        sa.Column('opinion', sa.Text(), nullable=True, comment='可选意见/改进建议'),
        sa.Column('hit_mode', sa.String(length=16), nullable=True, comment='评价时命中粒度快照'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()'), comment='创建时刻'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()'), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('ix_ai_report_feedbacks_report_id', 'report_id'),
        sa.Index('ix_ai_report_feedbacks_user_id', 'user_id'),
        # FK 级联: 报告/用户删除时评价一并清空(与 ai_reports.user_id 同款)
        sa.ForeignKeyConstraint(['report_id'], ['ai_reports.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("ai_report_feedbacks"):
        op.drop_table('ai_report_feedbacks')