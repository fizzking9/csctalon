"""ai_report_templates: AI 报告模板配置 (工单 #119)

Revision ID: c1d2e3f4a5b6
Revises: b9c0d1e2f3a4
Create Date: 2026-09-16 12:50:00.000000

设计:
- ai_report_templates 表: 配置化的 AI 报告模板(同源 prompt + 关键词匹配 + 调度 + 多模型分工)
- 模板同源: 前台实时与后台预生成共用同一份 question_template prompt
- question_keywords 用 JSONB(数组)便于匹配用户问句
- 自引用 fallback_template_id ON DELETE SET NULL: 简版被删时降级字段置 NULL
- 关联 users(id) ON DELETE SET NULL: 创建者被删时模板保留
- 不 seed(留待 #124 内置默认模板工单)
- 索引: (is_active, frequency) 调度器扫描用; code unique 唯一标识
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'b9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_report_templates',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False, comment='模板编码(如 daily_general_morning), 全树唯一'),
        sa.Column('name_cn', sa.String(length=128), nullable=False, comment='显示名(如「每日晨会·整体经营」), admin 管理界面与通知用'),
        sa.Column('question_template', sa.Text(), nullable=False, comment='问题/prompt 模板, 可含变量 {{date}} {{period}}; 前后台同源共用'),
        sa.Column('question_keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='关键词数组(如 ["今天","今日","整体","总览"]); 用于匹配用户问句到对应模板'),
        sa.Column('period', sa.String(length=16), nullable=False, comment='窗口周期 daily/weekly/monthly/range'),
        sa.Column('frequency', sa.String(length=32), nullable=False, comment='调度频率: sync-triggered(同步后触发) / daily / weekly / monthly'),
        sa.Column('trigger_at', sa.String(length=32), nullable=True, comment='触发时刻: daily→HH:MM(如 09:00); weekly→wday:H(如 1:09=周一9点); monthly→day:H'),
        sa.Column('sync_offset_min', sa.Integer(), nullable=False, server_default='5', comment='sync-triggered 频率下, 同步完成后延迟分钟数(避免数据未稳), 默认 5'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100', comment='多模板同时触发时的执行顺序(小先执行), 默认 100'),
        sa.Column('llm_provider_code', sa.String(length=64), nullable=True, comment='指定 provider code 跑此模板; None 走多 provider 优先级遍历(默认)'),
        sa.Column('fallback_template_id', sa.BigInteger(), nullable=True, comment='LLM 调用失败时降级到的简版模板 id; None=不降级'),
        sa.Column('max_tokens', sa.Integer(), nullable=False, server_default='2000', comment='后台预生成 token 上限(详尽), 默认 2000; 前台实时由调用方覆盖为 800'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true(), comment='启停开关; False 时调度器跳过此模板'),
        sa.Column('visibility_scope', sa.String(length=16), nullable=False, server_default='public', comment='可见性: public(全员可看预生成报告) / admin_only(仅 admin 可看)'),
        sa.Column('created_by', sa.BigInteger(), nullable=True, comment='创建者 user_id; 用户被删时置 NULL(模板保留)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['fallback_template_id'], ['ai_report_templates.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_ai_report_templates_code'),
    )
    # 调度器扫描: WHERE is_active=true AND frequency=? 按时点过滤
    op.create_index(
        'ix_ai_report_templates_active_freq',
        'ai_report_templates',
        ['is_active', 'frequency'],
    )


def downgrade() -> None:
    op.drop_index('ix_ai_report_templates_active_freq', table_name='ai_report_templates')
    op.drop_table('ai_report_templates')
