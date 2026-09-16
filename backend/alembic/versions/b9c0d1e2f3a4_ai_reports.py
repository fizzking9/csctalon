"""ai_reports: AI 分析报告历史持久化 (工单 #116)

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-09-16 00:00:00.000000

设计:
- ai_reports 表: AI 分析任务成功后落库的完整产物(question + answer_md + 窗口 + 用时 + token)
- 仅 success 落库(失败/运行中不落库); 永久保留(用户在工单 #116 明确选永久保留)
- 归属: user_id 隔离, 仅本人可见; admin 可查全部
- 关联 users(id) ON DELETE CASCADE: 用户被删除时报告级联清空
- 索引: (user_id, created_at DESC) 主查询路径; created_at 时间范围扫
- 不 seed(用户主动使用产生)
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b9c0d1e2f3a4'
down_revision: Union[str, None] = 'a8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_reports',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(length=32), nullable=True, comment='对应内存 ai_task_manager 的 task_id; 规则层秒回无 task_id 则 NULL'),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='归属用户 id(仅本人可见, admin 可查全部)'),
        sa.Column('question', sa.Text(), nullable=False, comment='原问句'),
        sa.Column('period', sa.String(length=16), nullable=True, comment='窗口周期 daily/weekly/monthly/range'),
        sa.Column('date', sa.Date(), nullable=True, comment='显式日期(纯日期)'),
        sa.Column('start', sa.Date(), nullable=True, comment='区间起点(纯日期)'),
        sa.Column('end', sa.Date(), nullable=True, comment='区间终点(纯日期)'),
        sa.Column('window_source', sa.String(length=16), nullable=True, comment='窗口判定来源: explicit_range/explicit_date/nl/nl_llm/period/rule'),
        sa.Column('window_label', sa.String(length=64), nullable=True, comment='窗口说明(如 2026-09-15(今天))'),
        sa.Column('answer_md', sa.Text(), nullable=False, comment='LLM 返回的 markdown 报告'),
        sa.Column('llm_model', sa.String(length=64), nullable=True, comment='命中的 provider+model; 规则层秒回=rule-engine'),
        sa.Column('llm_ms', sa.Integer(), nullable=False, comment='LLM 推理耗时(ms)'),
        sa.Column('total_ms', sa.Integer(), nullable=False, comment='端到端总耗时(ms)'),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True, comment='输入 token(OpenAI usage.prompt_tokens); 服务端不返则 NULL'),
        sa.Column('completion_tokens', sa.Integer(), nullable=True, comment='输出 token(OpenAI usage.completion_tokens); 服务端不返则 NULL'),
        sa.Column('total_tokens', sa.Integer(), nullable=True, comment='总 token(OpenAI usage.total_tokens); 服务端不返则 NULL'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', name='uq_ai_reports_task_id'),
    )
    # 主查询路径: 列表分页(本人最近 N 条) + 时间范围筛选
    op.create_index(
        'ix_ai_reports_user_created',
        'ai_reports',
        ['user_id', sa.text('created_at DESC')],
    )
    # 时间范围扫(管理员看全局时间线, 或 KPI 聚合)
    op.create_index(
        'ix_ai_reports_created_at',
        'ai_reports',
        ['created_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_ai_reports_created_at', table_name='ai_reports')
    op.drop_index('ix_ai_reports_user_created', table_name='ai_reports')
    op.drop_table('ai_reports')
