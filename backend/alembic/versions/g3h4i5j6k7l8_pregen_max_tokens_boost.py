"""feat: 三模板 max_tokens 放宽, 为完整草稿留空间 (工单 #134 批次 B1)

Revision ID: g3h4i5j6k7l8
Revises: f8g9h0i1j2k3
Create Date: 2026-09-17 22:50:00.000000

设计:
- 用户原话: "每次做完整而详细的分析, 然后作为一份完整的草稿来使用"。
- 周报/月报预生成应是"完整草稿", 不能因 token 上限被截断——
  后续 #135 摘取层从草稿切分段落返回子集, 若草稿本身被截断则摘取无意义。
- daily_general_morning: 2000 → 2500(日报相对短, 增幅保守)
- weekly_compare_last: 2500 → 4000(周报覆盖 7 天多维度, 需充足空间)
- monthly_overview: 3000 → 5000(月报覆盖 30 天逐天走势 + 多维度, 空间最大)
- 不动前台实时问答路径(ai.py 调用方仍 max_tokens=800 覆盖)
"""
from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = 'g3h4i5j6k7l8'
down_revision: Union[str, None] = 'f8g9h0i1j2k3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        text(
            "UPDATE ai_report_templates SET max_tokens = 2500, updated_at = NOW() "
            "WHERE code = 'daily_general_morning'"
        )
    )
    op.execute(
        text(
            "UPDATE ai_report_templates SET max_tokens = 4000, updated_at = NOW() "
            "WHERE code = 'weekly_compare_last'"
        )
    )
    op.execute(
        text(
            "UPDATE ai_report_templates SET max_tokens = 5000, updated_at = NOW() "
            "WHERE code = 'monthly_overview'"
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            "UPDATE ai_report_templates SET max_tokens = 2000, updated_at = NOW() "
            "WHERE code = 'daily_general_morning'"
        )
    )
    op.execute(
        text(
            "UPDATE ai_report_templates SET max_tokens = 2500, updated_at = NOW() "
            "WHERE code = 'weekly_compare_last'"
        )
    )
    op.execute(
        text(
            "UPDATE ai_report_templates SET max_tokens = 3000, updated_at = NOW() "
            "WHERE code = 'monthly_overview'"
        )
    )
