"""ai_report_templates 加 scope/format 字段 + 专项分桶模板 seed (工单 #181)

#181 意见 id=6「提示词命中应该几个维度 时间窗口 专项类型的简报还是明细」:
命中/生成只有「关键词→整体经营简报」单一链路, 缺 窗口×专项(scope)×体裁(format)
三维 → 命中后内容错配(实测 ai_reports#387 「涉诉情况: 数据截断, 暂无」)。
根因: 单一日报 feed 覆盖全部维度超长被截断, 模型只能答"暂无"。

本迁移:
- scope: overview(整体经营)/refund(退款含涉诉)/complaint(投诉三源)/reception(接待专项)
  —— 生成侧按此分桶(每桶 feed 聚焦不截断), 匹配侧按问句专项路由
- format: brief(简报)/detail(明细) —— 匹配按问句详略意图, 生成侧注入体裁要求
- 存量回填: daily_general_morning→(overview,brief); weekly/monthly→(overview,detail)
- seed 2 条专项分桶日报(幂等, refresh_on_sync=true 随高频同步刷新):
  daily_refund_brief   退款专项(含涉诉)  scope=refund    format=brief
  daily_complaint_brief 投诉专项(三源)    scope=complaint format=brief
  优先级(priority)高于整体日报(100)排在后面: 110/120
  模型沿用生产现状 provider(27b, 与 daily_general_morning 一致)

Revision ID: p7q8r9s0t1u2
Revises: e8d7c6b5a4f3
Create Date: 2026-09-21
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p7q8r9s0t1u2"
down_revision: Union[str, None] = "e8d7c6b5a4f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 加列(server_default 保证存量行有值; 语义由回填修正)
    op.add_column(
        "ai_report_templates",
        sa.Column(
            "scope", sa.String(length=16), nullable=False,
            server_default="overview",
            comment=(
                "#181 专项域: overview(整体经营)/refund(退款含涉诉)/complaint(投诉三源)/"
                "reception(接待专项); 生成按此分桶(feed 聚焦不截断), 匹配按问句专项路由"
            ),
        ),
    )
    op.add_column(
        "ai_report_templates",
        sa.Column(
            "format", sa.String(length=16), nullable=False,
            server_default="brief",
            comment="#181 体裁: brief(简报, 结论导向)/detail(明细, 逐维度展开); 匹配按问句详略意图",
        ),
    )

    # 2) 存量回填: 整体经营日报=简报; 周报/月报=明细
    op.execute(
        "UPDATE ai_report_templates SET scope='overview', format='brief' "
        "WHERE code = 'daily_general_morning'"
    )
    op.execute(
        "UPDATE ai_report_templates SET scope='overview', format='detail' "
        "WHERE code IN ('weekly_compare_last', 'monthly_overview')"
    )

    # 3) seed 专项分桶日报(幂等: code 冲突跳过, 保留 admin 调整)
    op.execute(
        """
        INSERT INTO ai_report_templates
            (code, name_cn, question_template, question_keywords,
             period, frequency, trigger_at, sync_offset_min, priority,
             llm_provider_code, fallback_template_id, max_tokens,
             is_active, visibility_scope, detail_level, required_dimensions,
             refresh_on_sync, scope, format)
        VALUES
            ('daily_refund_brief', '日报·退款专项(含涉诉)',
             '今日退款专项简报: 四路分布与涉诉情况、退款原因、退款挽回率各如何? 缺失维度如实写"暂无", 禁止反推。',
             '["退款","涉诉","四路","退费","提现","挽回","退款率","退款金额"]'::jsonb,
             'daily', 'sync-triggered', NULL, 5, 110,
             'talon-gmk-qwen3.8-27b', NULL, 2500, true, 'public',
             'standard', '["四路","涉诉","退款原因","挽回"]'::jsonb,
             true, 'refund', 'brief'),
            ('daily_complaint_brief', '日报·投诉专项(三源)',
             '今日投诉专项简报: 三源(内部/商户号/外部)登记/处理/积压各如何? 缺失维度如实写"暂无", 禁止反推。',
             '["投诉","三源","积压","内部投诉","外部投诉","商户号投诉","12315","登记","处理"]'::jsonb,
             'daily', 'sync-triggered', NULL, 5, 120,
             'talon-gmk-qwen3.8-27b', NULL, 2000, true, 'public',
             'standard', '["三源","登记","处理","积压"]'::jsonb,
             true, 'complaint', 'brief')
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    # 先删 seed 的专项分桶模板(幂等删除)
    op.execute(
        "DELETE FROM ai_report_templates "
        "WHERE code IN ('daily_refund_brief', 'daily_complaint_brief')"
    )
    op.drop_column("ai_report_templates", "format")
    op.drop_column("ai_report_templates", "scope")
