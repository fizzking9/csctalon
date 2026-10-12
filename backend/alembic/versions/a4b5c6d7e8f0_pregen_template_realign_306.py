"""pregen_template_realign: AI 预生成模板收敛 (#306)

Revision ID: a4b5c6d7e8f0
Revises: f299combo
Create Date: 2026-10-12

#306 AI 模板报告预生成收敛(生产 26 天命中率审计后拍板):

1. 五个保留模板(daily×3 / weekly / monthly)的专用 provider 由冷备
   talon-gmk-qwen3.8-27b 切到热驻 talon-gmk-gpt-oss-120b:
   27b 已转冷备(preload=false), 每次预生成触发 ~80s 冷加载并与 node-4090
   热驻 coder-30b 抢显存; 10-12 冒烟实测已全部兜底跑到 120b。
   仅更新当前仍为 27b 的行(可重入, 不覆盖之后的人工调整)。
2. rolling_7d_compare / rolling_30d_compare 停用(is_active=false):
   26 天命中率 rolling_7d 1/27(3.7%)、rolling_30d 0/26, 且与自然周/月报
   语义重叠; 停用后相关问句走实时 LLM 兜底, 历史报告保留可命中。

code/provider 均为本文件内固定字面量(非外部输入), 沿用仓库数据迁移的内联写法。
配套代码变更(同工单): worker 触发日当天漏跑当补 + settings 默认 skip 模式 off。
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a4b5c6d7e8f0'
down_revision: Union[str, None] = 'f299combo'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 保留模板: 27b → 120b(仅当前仍指向 27b 的行, 重复执行幂等)
    op.execute(
        """
        UPDATE ai_report_templates
        SET llm_provider_code = 'talon-gmk-gpt-oss-120b'
        WHERE code IN (
                  'daily_general_morning',
                  'daily_refund_brief',
                  'daily_complaint_brief',
                  'weekly_compare_last',
                  'monthly_overview'
              )
          AND llm_provider_code = 'talon-gmk-qwen3.8-27b'
        """
    )
    # 2. 滚动模板停用(幂等: 已是 false 的行不受影响; 历史 ai_reports 保留)
    op.execute(
        """
        UPDATE ai_report_templates
        SET is_active = FALSE
        WHERE code IN ('rolling_7d_compare', 'rolling_30d_compare')
          AND is_active = TRUE
        """
    )


def downgrade() -> None:
    # 回滚滚动模板为启用
    op.execute(
        """
        UPDATE ai_report_templates
        SET is_active = TRUE
        WHERE code IN ('rolling_7d_compare', 'rolling_30d_compare')
          AND is_active = FALSE
        """
    )
    # 回滚 provider: 120b → 27b(仅本工单迁移覆盖的 5 个 code)
    op.execute(
        """
        UPDATE ai_report_templates
        SET llm_provider_code = 'talon-gmk-qwen3.8-27b'
        WHERE code IN (
                  'daily_general_morning',
                  'daily_refund_brief',
                  'daily_complaint_brief',
                  'weekly_compare_last',
                  'monthly_overview'
              )
          AND llm_provider_code = 'talon-gmk-gpt-oss-120b'
        """
    )
