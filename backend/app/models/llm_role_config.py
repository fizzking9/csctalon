"""LLM 角色配置 - 能力档位（助理/专员/专家）的定义与开关

设计（2026-09-21，见 docs/design/20260921-模型角色分组与出域管控设计.md §3.2）:

  三档位（按**任务能力需求**，与 llm_scheduler.level 的「紧迫性」正交）:
    assistant  助理  速度优先: 分类打标 / 语义匹配 / 摘取排版 / 短摘要
    specialist 专员  平衡:     实时问答 / 报告生成 / 治理研判
    expert     专家  质量优先: 长文深度分析 / 复杂推理 / 预生成

  本表仅 3 行，存放**界面文案与角色级开关**，避免把提示语写死在前端。
  角色成员（哪些 provider 服务该角色、兜底顺序）存在 llm_providers 的
  role_scope / role_order 两列上（见 llm_provider.py）。

  运行时选型: llm_tiers.resolve_tier(db, role_key) 优先读本表 + llm_providers；
  DB 无配置时回退 llm_tiers.py 的常量（保证首次部署即生效，见源码注释）。
"""

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class LlmRoleConfig(IDMixin, TimestampMixin, Base):
    """LLM 能力角色定义（单行一角色，共 3 行）"""

    __tablename__ = "llm_role_configs"

    role_key: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False,
        comment="角色键(assistant/specialist/expert)",
    )
    display_name: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="显示名(助理/专员/专家)",
    )
    description: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="用途说明(含典型任务)",
    )
    rationale: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
        comment="选型依据(实测数据); 供界面展示, 让人知道为何这样配",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
        comment="角色总开关; false 时该角色的调用回退默认链",
    )
    allow_cloud: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
        comment=(
            "该角色是否允许出域(细粒度控制)。"
            "false 时即使全局策略为 auto, 本角色也不加入云端 provider"
        ),
    )
    tier_order: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False,
        comment="界面排序(小=前)",
    )
