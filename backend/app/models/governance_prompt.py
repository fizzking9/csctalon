"""治理研判提示词配置 (工单 #164, 设计: docs/design/AI数据治理框架-20260919.md §5.3)

设计说明（Design Rationale）
- 目的：研判层（AI 治理分析师）的提示词落库可迭代 —— 改判据不需要发版，直接改 DB 生效；
  version 留痕便于追溯与回滚（同 `onboarding_prompts` #156 的「配置是数据」思路）。
- 逻辑：code 唯一标识一份提示词（judge_system = 角色+铁律+输出结构，
  judge_user = 待研判 findings + 表说明注入），content 为模板正文（含 {占位符}，
  由研判服务格式化注入规范原文与证据）。
- 取舍：**不与 `onboarding_prompts` 共用一张表** —— 两张表分属两个 AI 服务
  （接入助手 / 治理分析师），共用会让「谁在用这条提示词」变得不可读；
  规范原文段也不落库（由代码常量生成，见 governance_judge_service.build_spec_rules），
  避免提示词里出现第二份 META 规范定义（同 A-20260910-004 漂移教训）。
  DB 配置是增强不是必需：研判服务对「取不到/被禁用」有模块级默认提示词兜底。
"""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class GovernancePrompt(IDMixin, TimestampMixin, Base):
    """治理研判提示词配置（DB 可迭代，版本留痕）。"""

    __tablename__ = "governance_prompts"

    code: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False,
        comment="提示词标识（judge_system / judge_user），全表唯一",
    )
    name_cn: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="显示名（如「研判·系统提示」）",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="提示词模板正文（可含 {占位符}，由研判服务格式化注入规范原文与证据）",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1",
        comment="版本号（每次修改递增，便于追溯与回滚）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
        comment="启用开关；False 时研判服务回退模块级默认提示词",
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="创建人 user_id（提示词编辑走 admin 界面，工单 #165）",
    )
