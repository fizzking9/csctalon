"""llm_role_configs 表 + seed 三角色（助理/专员/专家）

背景（2026-09-21，见 docs/design/20260921-模型角色分组与出域管控设计.md §3.2）:

  能力档位原先是 `llm_tiers.py` 里的 Python 常量，管理员无法在界面调整。
  本迁移把**角色定义**落库（仅 3 行，存文案与角色级开关），
  角色**成员**（哪些 provider 服务该角色、兜底顺序）存在 llm_providers 的
  role_scope / role_order 两列上。

  运行时选型: llm_tiers.resolve_tier() 优先读 DB；DB 无配置时回退源码常量
  （保证首次部署即生效，见 llm_tiers.py 注释）。

  为什么用「加两列 + 一张小表」而非规范化的关联表？
    实测本集群模型能力差异明显且互斥（MoE 有数值缺陷、Dense 慢），
    **一个模型服务多个角色的场景当前不存在**，且 provider 仅 5 个。
    加关联表会引入 JOIN 复杂度而收益为零。若将来出现多角色需求，再迁移。

seed 策略:
  - 三角色的 display_name/description/rationale 取自 llm_tiers.py 的实测结论，
    保证界面展示与源码一致（避免两处文案漂移）。
  - allow_cloud 默认 **false**（保守）：角色级出域需管理员显式开启，
    与全局 LLM_CLOUD_POLICY 形成双重管控（两者都为真才出域）。

Revision ID: e5f6a7b8c9d1
Revises: b1c2d3e4f5a6
Create Date: 2026-09-21 09:26:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d1'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 三角色的文案与选型依据（与 app/services/llm_tiers.py 保持同步）
_ROLES = [
    {
        "key": "assistant", "name": "助理", "order": 10,
        "desc": "速度优先：分类打标 / 语义匹配 / 摘取排版 / 短摘要",
        "rationale": (
            "实测 coder-30b 语义匹配 6/6 全对、摘取数字全对，decode 78 t/s（快 3x）。"
            "备选 7b 仅作降级 —— 注意它实测语义匹配 5/5 全错，不可作该类首选。"
        ),
    },
    {
        "key": "specialist", "name": "专员", "order": 20,
        "desc": "平衡：实时问答 / 报告生成 / 结构化输出 / 治理研判",
        "rationale": (
            "实测 27b 数值聚合 4/4、语义 0.95（最稳），Dense 架构无 MoE 算术缺陷。"
            "备选 coder-30b 快 3x，承接非数值降级场景。"
        ),
    },
    {
        "key": "expert", "name": "专家", "order": 30,
        "desc": "质量优先：长文深度分析 / 跨数据关联 / 复杂推理 / 预生成",
        "rationale": (
            "实测 122b 数值 4/4、prefill 331 t/s（集群最快），长文唯一选择。"
            "备选 27b 数值同样 4/4。"
        ),
    },
]


def upgrade() -> None:
    op.create_table(
        "llm_role_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("role_key", sa.String(32), nullable=False,
                  comment="角色键(assistant/specialist/expert)"),
        sa.Column("display_name", sa.String(32), nullable=False, comment="显示名(助理/专员/专家)"),
        sa.Column("description", sa.String(255), nullable=True, comment="用途说明(含典型任务)"),
        sa.Column("rationale", sa.String(512), nullable=True,
                  comment="选型依据(实测数据); 供界面展示"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true"),
                  comment="角色总开关; false 时该角色调用回退默认链"),
        sa.Column("allow_cloud", sa.Boolean(), nullable=False, server_default=sa.text("false"),
                  comment="该角色是否允许出域; 与全局 LLM_CLOUD_POLICY 双重管控"),
        sa.Column("tier_order", sa.Integer(), nullable=False, server_default=sa.text("100"),
                  comment="界面排序(小=前)"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_unique_constraint("uq_llm_role_configs_role_key", "llm_role_configs", ["role_key"])
    op.create_index("ix_llm_role_configs_role_key", "llm_role_configs", ["role_key"])

    # seed 三角色（幂等: ON CONFLICT DO NOTHING，重复执行不报错）
    for r in _ROLES:
        op.execute(sa.text("""
            INSERT INTO llm_role_configs
                (role_key, display_name, description, rationale,
                 enabled, allow_cloud, tier_order)
            VALUES (:k, :n, :d, :ra, true, false, :o)
            ON CONFLICT (role_key) DO NOTHING
        """).bindparams(k=r["key"], n=r["name"], d=r["desc"], ra=r["rationale"], o=r["order"]))


def downgrade() -> None:
    op.drop_index("ix_llm_role_configs_role_key", table_name="llm_role_configs")
    op.drop_constraint("uq_llm_role_configs_role_key", "llm_role_configs", type_="unique")
    op.drop_table("llm_role_configs")
