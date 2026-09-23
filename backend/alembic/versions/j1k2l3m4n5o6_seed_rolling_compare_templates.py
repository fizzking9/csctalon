"""seed: 滑动周/月对比模板 (近7天/近30天, 每天凌晨各一份)

Revision ID: j1k2l3m4n5o6
Revises: f7a8b9c0d1e3
Create Date: 2026-09-23 19:30:00.000000

设计（用户 2026-09-23 规划）
- 滑动窗口: 每天凌晨 02:00 生成一份「截止昨天近 N 天」整体经营对比报告,
  与「前一等长 N 天」对比; 长度恒定 N, 不随星期/月初跳动。
  * rolling_7d_compare  → period='rolling_7d'  (近7天 vs 前7天)
  * rolling_30d_compare → period='rolling_30d' (近30天 vs 前30天)
- 与自然周/月报并存、互不替代:
  * 自然周/月报 (weekly_compare_last/monthly_overview) 仍只在周一/1号出整周期;
  * 滑动模板 frequency='daily' + trigger_at='02:00' → 每天到点一次;
  * refresh_on_sync=false → 不随 30 分钟数据同步刷新(否则会一天刷几十次近7天报告)。
- 窗口由 ai_report_window.compute_pregen_window(period) 计算; worker 调度零改动
  (frequency=daily 已支持 HH:MM 到点扫描), generate_for_template 走 start/end range
  路径, brief 已提供四路/三源/涉诉的前一周期 yoy。
- 关键词与自然周/月报错开: 滚动用「近7天/最近一周/近30天/最近一个月」,
  自然周/月报保留「本周/周报/上周/本月/月报」; 命中层另以窗口贴合度兜底防抢命中。
- 幂等: INSERT ... ON CONFLICT (code) DO NOTHING, 重复 upgrade 不覆盖 admin 调整。
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'j1k2l3m4n5o6'
down_revision: Union[str, None] = 'f7a8b9c0d1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO ai_report_templates
            (code, name_cn, question_template, question_keywords,
             period, frequency, trigger_at, sync_offset_min, priority,
             llm_provider_code, fallback_template_id, max_tokens,
             is_active, visibility_scope, detail_level, required_dimensions,
             refresh_on_sync, scope, format)
        VALUES
            ('rolling_7d_compare', '滚动周报·近7天对比',
             '近7天(截止昨天)整体经营情况, 与前一个7天对比, 给出接待量/退款四路/投诉三源的环比变化与趋势; 缺失维度如实写"暂无", 禁止反推。',
             '["近7天","近七天","最近7天","最近一周","近一周","过去7天","过去一周","滚动周","7天环比"]'::jsonb,
             'rolling_7d', 'daily', '02:00', 5, 100,
             NULL, NULL, 4000, true, 'public',
             'standard', '["接待","退款四路","涉诉","投诉三源"]'::jsonb,
             false, 'overview', 'detail'),
            ('rolling_30d_compare', '滚动月报·近30天对比',
             '近30天(截止昨天)整体经营情况, 与前一个30天对比, 给出接待量/退款四路/投诉三源的环比变化与月度趋势; 缺失维度如实写"暂无", 禁止反推。',
             '["近30天","近三十天","最近30天","近一个月","最近一个月","过去30天","过去一个月","滚动月","30天环比"]'::jsonb,
             'rolling_30d', 'daily', '02:00', 5, 100,
             NULL, NULL, 5000, true, 'public',
             'standard', '["接待","退款四路","涉诉","投诉三源"]'::jsonb,
             false, 'overview', 'detail')
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("""
        DELETE FROM ai_report_templates
        WHERE code IN ('rolling_7d_compare', 'rolling_30d_compare')
    """)
