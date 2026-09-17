"""expand default template question_keywords (工单 #147 A 提示词工程)

Revision ID: b3d4e5f6a7c8
Revises: c8d9e0f1a2b3
Create Date: 2026-09-17 22:15:00.000000

设计(#147 A): 只做提示词工程(不拆新模板)提升日常问法命中率。
扩充 3 内置模板 question_keywords, 覆盖经营侧高频词(客流/营收/退款率/投诉量/
月度复盘/KPI/目标达成/环比同比走势等), 让日常问法更易命中对应模板, 减少走实时 LLM。
用 UPDATE ... 覆盖(不 INSERT), 因模板已存在; 幂等: 覆盖为固定「权威」关键词集,
is_active=false 的模板不在此(仅 update 既有 3 code)。
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b3d4e5f6a7c8'
down_revision: Union[str, None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 日报: 覆盖「现在/今天」时段 + 整体经营(接待/退款/投诉)高频词
    op.execute("""
        UPDATE ai_report_templates
        SET question_keywords = '["今天","今日","现在","当前","今天怎么样","整体经营","经营总览","总览","晨会","日报","接待量","退款","投诉"]'::jsonb
        WHERE code = 'daily_general_morning'
    """)
    # 周报: 本周 + 环比 + 趋势/进度类
    op.execute("""
        UPDATE ai_report_templates
        SET question_keywords = '["本周","这周","这周吧","周报","上周","环比","对比","周趋势","周度","这周情况","本周经营","目标达成","KPI"]'::jsonb
        WHERE code = 'weekly_compare_last'
    """)
    # 月报: 本月 + 同比 + 月度复盘/走势类
    op.execute("""
        UPDATE ai_report_templates
        SET question_keywords = '["本月","这个月","这月","月报","上月","同比","月度","月度复盘","月度总结","月度走势","下月建议","月经营","KPI达成"]'::jsonb
        WHERE code = 'monthly_overview'
    """)


def downgrade() -> None:
    # 回滚到 #124 原始关键词集
    op.execute("""
        UPDATE ai_report_templates
        SET question_keywords = '["今天","今日","整体","总览","晨会","日报"]'::jsonb
        WHERE code = 'daily_general_morning'
    """)
    op.execute("""
        UPDATE ai_report_templates
        SET question_keywords = '["本周","这周","周报","对比","上周","环比"]'::jsonb
        WHERE code = 'weekly_compare_last'
    """)
    op.execute("""
        UPDATE ai_report_templates
        SET question_keywords = '["本月","这个月","月报","上月","同比"]'::jsonb
        WHERE code = 'monthly_overview'
    """)
