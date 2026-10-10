"""seed: 内置默认模板 日报/周报/月报 (工单 #124)

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-09-16 13:40:00.000000

设计:
- 3 条内置默认模板: daily_general_morning / weekly_compare_last / monthly_overview
- 用 INSERT ... ON CONFLICT (code) DO NOTHING(幂等, 重复 upgrade 不报错)
- is_active=true, visibility_scope=public
- max_tokens: daily=2000, weekly=2500, monthly=3000
- trigger_at: daily=09:00, weekly=1:09:00(周一9点), monthly=1:09:00(每月1号9点)
- question_keywords JSONB 数组(用于 #122 匹配层关键词路由)
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 幂等 INSERT: code 冲突时跳过(已 seed 不重置字段, 保留 admin 调整)
    op.execute("""
        INSERT INTO ai_report_templates
            (code, name_cn, question_template, question_keywords,
             period, frequency, trigger_at, sync_offset_min, priority,
             llm_provider_code, fallback_template_id, max_tokens,
             is_active, visibility_scope)
        VALUES
            ('daily_general_morning', '每日晨会·整体经营',
             '今日经营总览, 含接待量/退款四路/投诉三源, 给出关键指标与异动提醒',
             '["今天","今日","整体","总览","晨会","日报"]'::jsonb,
             'daily', 'daily', '09:00', 5, 100,
             NULL, NULL, 2000, true, 'public'),
            ('weekly_compare_last', '周报·本周与上周对比',
             '本周整体经营情况, 与上周对比, 给出环比变化与趋势分析',
             '["本周","这周","周报","对比","上周","环比"]'::jsonb,
             'weekly', 'weekly', '1:09:00', 5, 100,
             NULL, NULL, 2500, true, 'public'),
            ('monthly_overview', '月报·本月总览',
             '本月经营情况总览, 含同比环比, 关键指标月度走势与下月建议',
             '["本月","这个月","月报","上月","同比"]'::jsonb,
             'monthly', 'monthly', '1:09:00', 5, 100,
             NULL, NULL, 3000, true, 'public')
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM ai_report_templates
        WHERE code IN ('daily_general_morning', 'weekly_compare_last', 'monthly_overview')
    """)
