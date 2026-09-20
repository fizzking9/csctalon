"""提示词迭代留痕 (工单 #173, 设计: docs/design/AI提示词评估中心-20260920.md §3.2)

半自动执行闭环的「账本」：评估中心产建议 → 人工确认 → 服务端应用，
每一步都在本表落一行，状态机 proposed → confirmed → applied / rejected。

设计说明（Design Rationale）
- 目的：提示词迭代是**有副作用的写入**（改模板 keywords/问句、调规则），必须留痕
  才能审计「谁改了、什么时候、为什么、改成什么、应用成功没有」。
- 逻辑：scope 区分五类建议（补关键词/改问句/新模板/调规则/锚点落地指引），
  old_value/new_value 支持应用时 diff 对比，result 记应用结果。
- 取舍：**不改代码常量提示词**（_SYSTEM_PROMPT/_EXTRACT_SYSTEM 是铁律），
  anchor_guide 类型只记「差异 + 落地指引」，不提供写入路径；
  新模板(new_template)本期只记建议，不自动建（建模板需人工补 required_dimensions）。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# ── scope 值域（建议类型，见设计 §6.4）──────────────────────────
ITER_SCOPE_TEMPLATE_KEYWORD = "template_keyword"  # 补模板 question_keywords
ITER_SCOPE_TEMPLATE_QUESTION = "template_question"  # 改模板 question_template
ITER_SCOPE_NEW_TEMPLATE = "new_template"  # 建议新建模板（本期只记建议）
ITER_SCOPE_RULE_ADJUST = "rule_adjust"  # 调整治理规则（启停/动作）
ITER_SCOPE_ANCHOR_GUIDE = "anchor_guide"  # 锚点漂移落地指引（只记不改）

# ── status 值域（状态机）─────────────────────────────────────────
ITER_STATUS_PROPOSED = "proposed"  # 评估产出，待人工确认
ITER_STATUS_CONFIRMED = "confirmed"  # 人工已确认，待应用
ITER_STATUS_APPLIED = "applied"  # 已应用到模板/规则
ITER_STATUS_REJECTED = "rejected"  # 人工不采纳

# 可应用（服务端能写入的 scope）与只记录（anchor_guide/new_template）的区分
APPLICABLE_SCOPES = frozenset({
    ITER_SCOPE_TEMPLATE_KEYWORD,
    ITER_SCOPE_TEMPLATE_QUESTION,
    ITER_SCOPE_RULE_ADJUST,
})
RECORD_ONLY_SCOPES = frozenset({
    ITER_SCOPE_NEW_TEMPLATE,
    ITER_SCOPE_ANCHOR_GUIDE,
})


class PromptIteration(IDMixin, TimestampMixin, Base):
    """提示词/规则迭代建议留痕（半自动执行闭环账本）。"""

    __tablename__ = "prompt_iterations"
    scope: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False, comment="建议类型（见 ITER_SCOPE_*）",
    )
    target_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="目标 id（模板 id / 规则 id）",
    )
    target_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="目标快照名（模板 name_cn / 规则 code）",
    )
    suggestion: Mapped[str] = mapped_column(
        Text, nullable=False, comment="建议内容（新关键词/新问句/规则调整/落地指引）",
    )
    reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="建议理由（如「近7天快照3份0命中」）",
    )
    old_value: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="旧值（应用时对比）",
    )
    new_value: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="新值",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ITER_STATUS_PROPOSED, server_default="proposed",
        comment="状态机: proposed/confirmed/applied/rejected",
    )
    result: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="应用结果（success/skipped/error + 摘要）",
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="建议产出人（评估触发者 user_id）",
    )
    confirmed_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="确认人 user_id",
    )
    applied_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="应用人 user_id",
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="确认时刻",
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="应用时刻",
    )
