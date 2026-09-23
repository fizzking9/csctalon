"""扩容 ai_reports.window_label varchar(64) -> varchar(255)

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-09-23 22:15:00.000000

背景: 滑动周/月报告的预生成 window_label 形如
  预生成(template=rolling_7d_compare, snapshot=2026-09-23,
          rolling_7d(近7天·截止昨天 2026-09-16~2026-09-22))
含模板 code + 中文区间名 + 两个日期, 约 67 字符, 超出原 varchar(64),
导致 save_report 落库 StringDataRightTruncation(滚动报告静默生成失败)。
自然周/月报 label 短未暴露; 该列为纯说明文本, 扩到 255 根治并保留完整快照信息。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'k2l3m4n5o6p7'
down_revision: Union[str, None] = 'j1k2l3m4n5o6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'ai_reports', 'window_label',
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    # 回滚收窄: 若存在超长值 PG 会报错, 属预期(回滚前应先清理滚动报告)
    op.alter_column(
        'ai_reports', 'window_label',
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
