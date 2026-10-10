"""llm_cloud_policy 单行表 —— 出域策略可从界面切换（无需改 .env 重启）

背景（2026-09-21，见 docs/design/20260921-模型角色分组与出域管控设计.md §3.3）:

  B1 出域管控上线时，策略取自 `settings.LLM_CLOUD_POLICY`（.env）。
  但 .env 改动需重启服务，与「管理员界面自助调整」诉求不符 ——
  B3 界面只能只读展示并提示"需改 .env 重启"。

  本迁移照 `scheduler_settings` 的既有模式把策略 DB 化:
    - 单行表（id 恒为 1）: 避免"多行配置哪行生效"歧义
    - DB CHECK 约束作为最后防线（防手工改库写入非法值）
    - **不预置行**: DB 无行时运行时回退 settings.LLM_CLOUD_POLICY，
      保证升级后行为与 B1 完全一致（零行为变更）

Revision ID: d4e5f6a7b8c0
Revises: a3b4c5d6e7f8
Create Date: 2026-09-21 09:40:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c0'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_cloud_policy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("policy", sa.String(16), nullable=False,
                  server_default=sa.text("'explicit_only'"),
                  comment="出域策略: never / explicit_only / auto"),
        sa.Column("require_confirm", sa.Boolean(), nullable=False,
                  server_default=sa.text("true"),
                  comment="explicit_only 下是否需用户确认（预留）"),
        sa.Column("updated_by", sa.String(64), nullable=True, comment="最后修改人账号"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("policy IN ('never', 'explicit_only', 'auto')",
                           name="ck_llm_cloud_policy_value"),
    )
    # 刻意**不预置行** —— 运行时回退 settings.LLM_CLOUD_POLICY（B1 行为）。
    # 管理员首次在界面保存时才写入，届时 DB 值优先。


def downgrade() -> None:
    op.drop_table("llm_cloud_policy")
