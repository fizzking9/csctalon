"""llm_role_members 关联表 —— 修复「一模型多角色」限制

背景（2026-09-21，见 docs/design/20260921-模型角色分组与出域管控设计.md §3.1）:

  原设计用 `llm_providers.role_scope`（单值列）表达角色归属，假设「一模型一角色」。
  **部署后立即被实测证伪**: `qwen3-coder:30b` 需同时服务
    - assistant  的首选（实测语义匹配 6/6 全对、快 3x）
    - specialist 的备选（27b 失败时的降级目标）
  单值列下相互覆盖，导致 assistant 降级为 `qwen2.5:7b`
  —— 该模型实测语义匹配 **5/5 全错**，比源码常量更差。

  本迁移建立多对多关联表，是正式修复。

数据迁移策略（关键）:
  1. 建表
  2. 把 llm_providers 中已有的 role_scope/role_order 迁入新表（幂等）
  3. 补充「已知的多角色归属」——即本次暴露的问题本身:
     coder-30b 同时属于 assistant(1) 与 specialist(2)
  4. **保留** role_scope/role_order 列一个版本（读时兜底），
     待运行时确认只读新表后再删除

Revision ID: a3b4c5d6e7f8
Revises: e5f6a7b8c9d1
Create Date: 2026-09-21 09:35:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'e5f6a7b8c9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_role_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("role_key", sa.String(32), nullable=False,
                  comment="角色键(assistant/specialist/expert)"),
        sa.Column("provider_code", sa.String(64), nullable=False,
                  comment="provider code(快照)"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("100"),
                  comment="角色内兜底顺序(小=先尝试)"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true"),
                  comment="该成员在本角色内是否启用"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("role_key", "provider_code",
                            name="uq_llm_role_members_role_provider"),
    )
    op.create_index("ix_llm_role_members_role_key", "llm_role_members", ["role_key"])
    op.create_index("ix_llm_role_members_provider_code", "llm_role_members", ["provider_code"])

    # 2) 迁移已有单值归属（幂等，重复执行不报错）
    op.execute("""
        INSERT INTO llm_role_members (role_key, provider_code, sort_order, enabled)
        SELECT role_scope, code, role_order, true
          FROM llm_providers
         WHERE role_scope IS NOT NULL
           AND role_scope <> ''
        ON CONFLICT (role_key, provider_code) DO NOTHING
    """)

    # 3) 补充多角色归属（本次证伪的正是这一条）
    #    coder-30b 同时服务 assistant(首选) 与 specialist(备选:
    #    27b 失败时降级目标, 实测 coder-30b 快 3x)
    op.execute("""
        INSERT INTO llm_role_members (role_key, provider_code, sort_order, enabled)
        SELECT 'assistant', code, 1, true FROM llm_providers
         WHERE code = 'talon-gmk-qwen3-coder-30b'
        ON CONFLICT (role_key, provider_code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO llm_role_members (role_key, provider_code, sort_order, enabled)
        SELECT 'specialist', code, 2, true FROM llm_providers
         WHERE code = 'talon-gmk-qwen3-coder-30b'
        ON CONFLICT (role_key, provider_code) DO NOTHING
    """)
    # assistant 备选: 7b（降级兜底；注意它实测语义匹配会错，但作最后兜底仍优于无）
    op.execute("""
        INSERT INTO llm_role_members (role_key, provider_code, sort_order, enabled)
        SELECT 'assistant', code, 2, true FROM llm_providers
         WHERE code = 'talon-gmk-qwen2.5-7b'
        ON CONFLICT (role_key, provider_code) DO NOTHING
    """)
    # expert 备选: 27b
    op.execute("""
        INSERT INTO llm_role_members (role_key, provider_code, sort_order, enabled)
        SELECT 'expert', code, 2, true FROM llm_providers
         WHERE code = 'talon-gmk-qwen3.8-27b'
        ON CONFLICT (role_key, provider_code) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("ix_llm_role_members_provider_code", table_name="llm_role_members")
    op.drop_index("ix_llm_role_members_role_key", table_name="llm_role_members")
    op.drop_table("llm_role_members")
