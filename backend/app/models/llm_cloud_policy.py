"""LLM 出域策略配置（单行表）—— 管理员可在界面切换，无需改 .env 重启

背景（2026-09-21，见 docs/design/20260921-模型角色分组与出域管控设计.md §3.3）:

  出域管控（B1）上线时，策略取自 `settings.LLM_CLOUD_POLICY`（.env）。
  但 .env 改动需重启服务，与「管理员在界面自助调整」的诉求不符 ——
  B3 界面只能**只读展示**并提示"需改 .env 重启"。

  本表把策略 DB 化，照 `scheduler_settings` 的既有模式:
    - **单行表**（id 恒为 1）: 避免"多行配置哪行生效"的歧义
    - 取值受 DB 约束（最后防线，防手工改库写入非法值）
    - 读取带进程缓存（TTL），避免每次调用查库
    - DB 无行时**回退 `settings.LLM_CLOUD_POLICY`** —— 保证升级后行为不变

  策略语义:
    never         = 云端完全不可用（数据绝对不出内网）
    explicit_only = 仅当调用方显式要求时才用云端（默认）
    auto          = 本地失败自动降级云端（⚠️ 可能静默出域）
"""

from sqlalchemy import Boolean, CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# 单行表固定主键（同 scheduler_settings）
SINGLETON_ID = 1

# 合法取值（与 llm_tiers 的策略常量保持一致；此处独立定义以避免循环导入）
VALID_POLICIES = ("never", "explicit_only", "auto")
DEFAULT_POLICY = "explicit_only"


class LlmCloudPolicy(IDMixin, TimestampMixin, Base):
    """出域策略配置（单行）"""

    __tablename__ = "llm_cloud_policy"
    __table_args__ = (
        # 应用层已有校验；DB 约束作为最后防线（防手工改库）
        CheckConstraint(
            "policy IN ('never', 'explicit_only', 'auto')",
            name="ck_llm_cloud_policy_value",
        ),
    )

    policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DEFAULT_POLICY,
        comment="出域策略: never / explicit_only / auto",
    )
    # 是否要求用户确认（explicit_only 下的交互开关；预留，当前未接入交互层）
    require_confirm: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
        comment="explicit_only 下是否需用户确认（预留）",
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="最后修改人账号",
    )
