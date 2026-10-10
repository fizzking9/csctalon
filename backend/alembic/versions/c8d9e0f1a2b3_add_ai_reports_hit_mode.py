"""add ai_reports.hit_mode (工单 #147)

Revision ID: c8d9e0f1a2b3
Revises: g3h4i5j6k7l8
Create Date: 2026-09-17 22:10:00.000000

设计(#147 B): 历史记录需区分命中粒度。命中(L1/L2)与未命中(实时)统一落库后,
用 hit_mode 标记该条报告是「完整采用预生成全文 / 摘取局部段落 / 实时生成 / 规则秒回」:
- full:    命中·全量 —— 问句命中预生成模板且直接采用整篇报告(extract_section #144 规则短路)
- section: 命中·局部 —— 命中预生成报告后按问句摘取相关段落(LLM 摘取)
- live:    未命中·实时 —— 未命中预生成, 走实时 LLM 生成
- rule:    规则层秒回 —— #98 层3 纯取数问句规则命中(非模板)
历史存量报告该列为 NULL, 前端按「未标记」降级展示(不误标)。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = 'g3h4i5j6k7l8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_reports",
        sa.Column(
            "hit_mode",
            sa.String(length=16),
            nullable=True,
            comment=(
                "#147 命中粒度: full(命中·全量=整篇采用预生成)/"
                "section(命中·局部=摘取段落)/"
                "live(未命中·实时=实时LLM)/"
                "rule(规则层秒回); 历史存量 NULL=未标记"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_reports", "hit_mode")
