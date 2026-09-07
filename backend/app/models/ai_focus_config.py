"""AI 分析助手 - 关注点/分析视角配置 (A-20260904-003 v1.2 二期切片 / 工单 #26)

背景(2026-09-07 用户确认): 经营数据 AI 助手提示词三层中, 只有"关注点/分析视角
(Focus)"是运营主观偏好, 应可配置化(增改/排序/启停)以提升使用效率; 而口径卡
(Business Glossary)与 System 铁律是客观事实/防幻觉底线, 保持代码常量(见
docs/design/20260907-ai-assistant-focus-configurable-0.md 已通过终审)。

设计:
- 每行 = 一条"关注点/分析视角"指导文本, 注入 AI 会话 Prompt 的 user 段
  (位于【业务口径】与【给定数据】之间), 为建议性参考, 不影响口径/铁律。
- scope_key: global(兜底全带)/refund/complaint/reception, 按问句关键词路由。
- sort_order 排序、enabled 启停。seed 提供默认关注点(与 QUICK_QUESTIONS 对应)。
"""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class AiFocusConfig(IDMixin, TimestampMixin, Base):
    """AI 助手关注点/分析视角配置行(运营可增改/排序/启停)"""

    __tablename__ = "ai_focus_configs"

    code: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, comment="唯一标识(如 focus-refund-litigation)"
    )
    scope_key: Mapped[str] = mapped_column(
        String(16), default="global", nullable=False, index=True,
        comment="适用范围: global(兜底全带)/refund/complaint/reception; 空=global",
    )
    title: Mapped[str] = mapped_column(String(64), nullable=False, comment="标题(如 退款关注点)")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="关注点正文(注入模型 Prompt 的指导文本)")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序(小在前)")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, comment="启用; false=停用不注入")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="最后更新人")
