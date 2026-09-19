"""data_source_specs 支持异步起草态 (工单 #157)

背景: 设计 §七 要求 `POST /ai-onboarding/drafts` 为「异步起草（202 + draft_id）+ 轮询」，
而 #155 建表时 code 为 NOT NULL —— 异步起草在 LLM 返回前**还不知道** AI 建议的 code，
无法预建草稿行。故本迁移:

1. `code` 改可空: 起草中的占位行 code=NULL（PostgreSQL unique 索引允许多个 NULL，
   天然支持并发多份起草互不冲突）；起草完成后由服务写入 AI 建议 code。
2. 新增 `error_message`: 起草失败原因（status=failed 时有值），供前端展示与重试判断。
   不复用 validator_report —— 后者语义是「规范校验报告」，塞异常信息会污染口径。

status 值域由 draft/confirmed/applied/rejected 扩展为
drafting/draft/failed/confirmed/applied/rejected（String(16) 无 CHECK 约束，无需 DDL）。

Revision ID: g2h3i4j5k6l7
Revises: f1g2h3i4j5k6
Create Date: 2026-09-19
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'g2h3i4j5k6l7'
down_revision: Union[str, None] = 'f1g2h3i4j5k6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # code 可空: 起草中的占位行尚无 AI 建议 code（unique 索引对 NULL 不去重, 支持并发起草）
    op.alter_column("data_source_specs", "code", existing_type=sa.String(length=64), nullable=True)
    op.add_column(
        "data_source_specs",
        sa.Column(
            "error_message", sa.Text(), nullable=True,
            comment="起草失败原因（status=failed 时有值）",
        ),
    )


def downgrade() -> None:
    # 回滚前先清理 code 为 NULL 的行: 它们是「起草中/起草失败」的占位草稿,
    # 既无 code 也无落地价值; 不清理则下面的 SET NOT NULL 会直接报错。
    op.execute("DELETE FROM data_source_specs WHERE code IS NULL")
    op.drop_column("data_source_specs", "error_message")
    op.alter_column("data_source_specs", "code", existing_type=sa.String(length=64), nullable=False)
