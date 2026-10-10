"""llm_providers 增 is_cloud/role_scope/role_order（数据出域管控 + 角色归属）

背景（2026-09-21，见 docs/design/20260921-模型角色分组与出域管控设计.md）:

  原实现把云端 provider(deepseek-v4-flash) 硬编码为所有能力档位的兜底项
  （llm_tiers.CLOUD_FALLBACK）。本地模型 transient 失败时（实测 27b 曾 30% 超时）
  会**静默把 prompt 发给 api.deepseek.com**，而 prompt 含真实客服工单数据 ——
  与「数据不出内网」的核心定位冲突，且无开关、无审计标记。

  本迁移为出域管控与角色分组提供数据基础。

  数据回填策略（关键，避免改变现有行为）:
    - 公网域名（如 api.deepseek.com）→ is_cloud=true
    - 内网 IP / localhost / docker 内部 → is_cloud=false
    - role_scope 不预填（留 NULL=不限角色），由管理界面配置；
      运行时 resolve_tier() 在 DB 无角色配置时回退 llm_tiers.py 常量，行为不变。

Revision ID: b1c2d3e4f5a6
Revises: p7q8r9s0t1u2
Create Date: 2026-09-21 09:23:22.970514
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'p7q8r9s0t1u2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) is_cloud: 是否出域。默认 false（保守），随后按 base_url 回填云端标记。
    op.add_column(
        "llm_providers",
        sa.Column(
            "is_cloud", sa.Boolean(), nullable=False, server_default=sa.text("false"),
            comment="是否出域(公网/云端); true 时受 LLM_CLOUD_POLICY 管控",
        ),
    )
    op.create_index("ix_llm_providers_is_cloud", "llm_providers", ["is_cloud"])

    # 2) role_scope: 角色归属; NULL = 不限角色（保持现有行为，由界面再配）
    op.add_column(
        "llm_providers",
        sa.Column(
            "role_scope", sa.String(32), nullable=True,
            comment="角色归属(assistant/specialist/expert); NULL=不限角色",
        ),
    )
    op.create_index("ix_llm_providers_role_scope", "llm_providers", ["role_scope"])

    # 3) role_order: 角色内兜底顺序（小=先尝试）
    op.add_column(
        "llm_providers",
        sa.Column(
            "role_order", sa.Integer(), nullable=False, server_default=sa.text("100"),
            comment="角色内兜底顺序(小=先尝试); 仅 role_scope 非空时有意义",
        ),
    )

    # 4) 回填 is_cloud: 公网域名（非内网 IP / localhost）判为出域。
    #    用 base_url 判定而非写死 code，新增云端 provider 时同样适用。
    op.execute("""
        UPDATE llm_providers
           SET is_cloud = true
         WHERE base_url NOT LIKE '%://127.0.0.1%'
           AND base_url NOT LIKE '%://localhost%'
           AND base_url NOT LIKE '%://host.docker.internal%'
           AND base_url NOT LIKE '%://10.%'
           AND base_url NOT LIKE '%://192.168.%'
           AND base_url !~ '://172\\.(1[6-9]|2[0-9]|3[01])\\.'
    """)

    # 5) llm_call_logs 增 is_cloud: 事后审计「数据去过哪」。
    #    快照式（调用时记录），不依赖 provider 当前配置变化。
    op.add_column(
        "llm_call_logs",
        sa.Column(
            "is_cloud", sa.Boolean(), nullable=False, server_default=sa.text("false"),
            comment="本次调用是否出域(快照); 供事后审计数据流向",
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_call_logs", "is_cloud")

    op.drop_index("ix_llm_providers_role_scope", table_name="llm_providers")
    op.drop_column("llm_providers", "role_order")
    op.drop_column("llm_providers", "role_scope")

    op.drop_index("ix_llm_providers_is_cloud", table_name="llm_providers")
    op.drop_column("llm_providers", "is_cloud")
