"""角色成员关联表 —— 支持「一个模型服务多个角色」

背景（2026-09-21，见 docs/design/20260921-模型角色分组与出域管控设计.md §3.1）:

  原设计用 `llm_providers.role_scope`（单值列）表达角色归属，假设「一模型一角色」。
  **部署后立即被实测证伪**: `qwen3-coder:30b` 需同时服务
    - assistant  的首选（实测语义匹配 6/6 全对、快 3x）
    - specialist 的备选（27b 失败时的降级目标）
  单值列下两次更新相互覆盖，导致 assistant 降级为 `qwen2.5:7b`
  —— 而该模型实测语义匹配 **5/5 全错**，比源码常量更差。

  本表改为多对多关联，是正式修复。
  `llm_providers.role_scope` / `role_order` 保留为**兼容字段**（读时兜底），
  待一个版本后删除。

  与 llm_scheduler.level 的区别（勿混淆）:
    - llm_scheduler.level = 任务**紧迫性**（L0 预生成 / L1 admin / L2 用户实时）
    - 本表 role_key      = 任务**能力需求**（该找哪个模型）
"""

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class LlmRoleMember(IDMixin, TimestampMixin, Base):
    """角色成员（多对多: 一个角色多个模型，一个模型可属多个角色）"""

    __tablename__ = "llm_role_members"
    __table_args__ = (
        UniqueConstraint("role_key", "provider_code",
                         name="uq_llm_role_members_role_provider"),
    )

    role_key: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
        comment="角色键(assistant/specialist/expert)",
    )
    provider_code: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="provider code(快照; provider 删除后本行应一并清理)",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False,
        comment="角色内兜底顺序(小=先尝试)",
    )
    enabled: Mapped[bool] = mapped_column(
        default=True, server_default="true", nullable=False,
        comment="该成员在本角色内是否启用（不影响 provider 自身开关）",
    )
