"""提示词版本化配置 (工单 #178, 设计: docs/design/AI提示词评估中心-20260920.md 演进)

两级提示词（生成侧 _SYSTEM_PROMPT / 摘取侧 _EXTRACT_SYSTEM）从**代码常量**升级为
**DB 可迭代 + 版本化**：代码常量保留为兜底默认（提示词铁律），DB 行只有在
`is_active=TRUE` 时才生效 —— 初始 seed 不激活，因此**升级后行为与现状完全一致**
（零回归）；管理员经版本管理界面激活新版本后，后续生成/摘取使用 DB 内容。

设计说明（Design Rationale）
- 目的：LLM 自动迭代闭环（#176）的安全落点 —— 提示词可「新增版本 → 金丝雀验证 →
  激活」且可一键回滚到任意旧版本，同时不违反 AGENTS.md 提示词铁律（不自动改代码常量，
  DB 更新走 admin + 留痕）。
- 逻辑：一个 code 多行 = 多个版本；部分唯一索引 (code) WHERE is_active 保证
  **同一 code 至多一个生效版本**；版本递增由服务层复制当前生效行 content 实现，
  旧版本行永不覆盖（回滚 = 激活旧版本）。
- 取舍：**不 seed 为激活态** —— seed 行仅作初始版本留痕；生效与否完全由人工激活决定。
  H2 锚点联动改动成本高，本期 DB 只承接提示词正文，锚点常量保持代码侧。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# ── code 值域（两级提示词，一一对应加载点）────────────────────
PROMPT_CODE_GEN_SYSTEM = "gen_system"      # 生成侧 _SYSTEM_PROMPT（ai_feed）
PROMPT_CODE_EXTRACT_SYSTEM = "extract_system"  # 摘取侧 _EXTRACT_SYSTEM（ai_extract_service）


class PromptConfig(IDMixin, TimestampMixin, Base):
    """提示词版本化配置（DB 可迭代 + 版本可回滚）。"""

    __tablename__ = "prompt_configs"
    __table_args__ = (
        # 同一 code 至多一个生效版本（部分唯一索引：NULL/FALSE 不参与唯一约束）
        Index(
            "uq_prompt_configs_active_code", "code",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    code: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False,
        comment="提示词标识（gen_system=生成侧 / extract_system=摘取侧）",
    )
    name_cn: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="显示名（如「生成侧·System 铁律」）",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="提示词正文（完整可渲染文本，可含 {{占位符}}）",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1",
        comment="版本号（每次迭代递增；旧版本行保留可回滚）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="生效标记；TRUE=当前生效版本（每 code 至多一个），FALSE=历史/草稿版本",
    )
    baseline: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment="激活时基线指标（命中率/live 占比，金丝雀验证 #179 回填）",
    )
    change_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="本版迭代依据：解决什么问题/为何改（新建版本时人工填写，供版本账本可溯）",
    )
    references: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="本版参考依据：评估中心建议/用户评价/意见工单/文档链接等来源（JSONB 数组或自由文本）",
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="创建人 user_id（版本管理走 admin）",
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="激活时刻",
    )
    activated_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="激活人 user_id",
    )
