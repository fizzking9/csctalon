"""ai_reports 加 latest_missing JSONB (工单 #153)

AI 提示词反馈闭环「收集层」: 把 check_dimensions 的维度校验缺失清单落库,
供周期「提示词健康报告」聚合(缺失维度 TopN → 直指 feed 供给缺口)。

- latest_missing: JSONB 数组, 最近一次 standard 维度校验的缺失清单
  (空数组 = 校验通过维度齐全; NULL = 尚未校验, 预生成快照在首次被
  standard 校验/评审时回填)
- 仅记录校验结果, 不覆盖 answer_md 等大字段

Revision ID: e4f5a6b7c8d9
Revises: a7b6c5d4e3f2
Create Date: 2026-09-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, None] = 'a7b6c5d4e3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_reports",
        sa.Column(
            "latest_missing", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
            comment=(
                "#153 最近一次 standard 维度校验的缺失清单(JSONB 数组, 空数组=维度齐全); "
                "供提示词健康报告聚合缺失维度 TopN"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_reports", "latest_missing")
