"""ai_report_templates 加 detail_level + required_dimensions (工单 #152)

AI 回答模式分级框架(A-20260919-001)模板配置扩展:
- detail_level: 模板期望的回答详细级别 brief/standard/deep(预生成按此组织输出密度)
- required_dimensions: 业务维度清单(JSONB 关键词数组), 供完整性校验器检查快照维度覆盖;
  NULL=不做完整性校验(简报类)

种子回填现有 3 模板(按 code, 幂等):
- daily_general_morning: 明细级(standard), 维度=接待/退款/四路/涉诉/挽回/投诉/结论
- weekly_compare_last / monthly_overview: 深度级(deep), 维度同日报+趋势对比/环比/同比

Revision ID: a7b6c5d4e3f2
Revises: dc20f480c17e
Create Date: 2026-09-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'a7b6c5d4e3f2'
down_revision: Union[str, None] = 'dc20f480c17e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 种子回填: code → (detail_level, required_dimensions)
_SEED: dict[str, tuple[str, list[str]]] = {
    "daily_general_morning": (
        "standard",
        ["接待", "退款", "四路", "涉诉", "挽回", "投诉", "结论"],
    ),
    "weekly_compare_last": (
        "deep",
        ["接待", "退款", "四路", "涉诉", "挽回", "投诉", "趋势", "环比", "结论"],
    ),
    "monthly_overview": (
        "deep",
        ["接待", "退款", "四路", "涉诉", "挽回", "投诉", "趋势", "同比", "结论"],
    ),
}


def upgrade() -> None:
    op.add_column(
        "ai_report_templates",
        sa.Column(
            "detail_level", sa.String(length=16), nullable=False,
            server_default="standard",
            comment="#152 回答详细级别: brief/standard/deep(预生成按此组织输出密度)",
        ),
    )
    op.add_column(
        "ai_report_templates",
        sa.Column(
            "required_dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
            comment=(
                "#152 业务维度清单(JSONB 关键词数组, 如 [\"四路\",\"涉诉\",\"挽回\"]); "
                "完整性校验器按此检查快照维度覆盖; NULL=不做校验"
            ),
        ),
    )
    # 种子回填(按 code 幂等更新; 用户已改过的模板会被覆盖, 属预期种子)
    for code, (detail_level, dims) in _SEED.items():
        op.execute(
            sa.text(
                "UPDATE ai_report_templates "
                "SET detail_level = :detail, required_dimensions = :dims "
                "WHERE code = :code"
            ).bindparams(
                sa.bindparam("detail", value=detail_level),
                sa.bindparam("dims", value=dims, type_=postgresql.JSONB()),
                sa.bindparam("code", value=code),
            )
        )


def downgrade() -> None:
    op.drop_column("ai_report_templates", "required_dimensions")
    op.drop_column("ai_report_templates", "detail_level")
