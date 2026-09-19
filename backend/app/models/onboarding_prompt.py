"""AI 数据接入助手·起草提示词配置（工单 #156）

设计说明（Design Rationale）
- 目的：AI 起草服务的提示词模板落库可迭代（#153 反馈闭环思路）—— 修改提示词
  不需要发版，直接改 DB 即生效；version 留痕便于追溯与回滚。
- 逻辑：code 唯一标识一份提示词（draft_system=系统提示/角色+铁律+输出结构，
  draft_user=用户提示/样例注入），content 为模板正文（可含 {占位符}，
  由起草服务格式化注入规范原文与样例）。
- 取舍：本表不存「规范原文」本身 —— 规范注入段由代码按常量生成
  （见 onboarding_draft_service.build_spec_rules），避免提示词里出现第二份
  META 规范定义（与命名/去重规范漂移风险同 A-20260910-004）。DB 配置是
  增强不是必需：起草服务对「取不到/被禁用」有模块级默认提示词兜底。
"""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class OnboardingPrompt(IDMixin, TimestampMixin, Base):
    """AI 数据接入助手·起草提示词配置（DB 可迭代，版本留痕）。"""

    __tablename__ = "onboarding_prompts"

    code: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False,
        comment="提示词标识（draft_system / draft_user 等），全表唯一",
    )
    name_cn: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="显示名（如「起草·系统提示」）",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="提示词模板正文（可含 {占位符}，由起草服务格式化注入规范原文与样例）",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1",
        comment="版本号（每次修改递增，便于追溯与回滚）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
        comment="启用开关；False 时起草服务回退模块级默认提示词",
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="创建人 user_id（提示词编辑走 admin 界面，工单 #157）",
    )
