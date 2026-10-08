"""R-P1-4 (#240): 存量详尽长报告模板 max_tokens 兜底对齐 R3 下限 4000

Revision ID: b2_5_long_report_tokens
Revises: b1_5_4_scheduling_suggestion
Create Date: 2026-10-09 14:00:00.000000

背景:
- R3《20260927 算力深度评估》结论: 详尽长报告经 1200→2400→4000 三轮迭代才不截断,
  生产长报告 max_tokens 不应低于 4000。
- 内置模板数据层已对齐(#134: weekly 4000/monthly 5000; rolling seed: rolling_7d
  4000/rolling_30d 5000), 但历史上可能存在手动新建的周/月/滚动长报告模板仍 <4000
  (旧创建入口统一默认 2000)。本迁移做**幂等兜底**: 仅把这些长报告模板的下限抬到
  4000, GREATEST 保证已 ≥4000 的(如 monthly 5000)不被压低; 日报(daily)不在其列。

注意:
- 纯数据对齐, 无表结构变更(列本就无 server_default, ORM 默认由代码常量控制)。
- downgrade 无法还原各行原值(未逐行备份), 故不做反向 UPDATE —— 把长报告下限从
  4000 降回 2000 会重新引入截断, 没有回退价值。
- revision id 长度受限(alembic_version.version_num varchar(32)), 故用短 id。
"""
from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = 'b2_5_long_report_tokens'
down_revision: Union[str, None] = 'b1_5_4_scheduling_suggestion'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 详尽长报告(周/月/滚动)下限对齐 4000; GREATEST 保留已更高的设置(如 monthly 5000)。
    op.execute(
        text(
            "UPDATE ai_report_templates "
            "SET max_tokens = GREATEST(max_tokens, 4000), updated_at = NOW() "
            "WHERE period IN ('weekly', 'monthly', 'rolling_7d', 'rolling_30d') "
            "AND max_tokens < 4000"
        )
    )


def downgrade() -> None:
    # 见模块 docstring: 无法还原原值且回退会重新引入截断, 有意 no-op。
    pass
