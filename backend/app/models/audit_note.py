"""审计附注模型 - 报告的正式审计产物(结构化)

与 ReportComment(Phase 2,轻量阅后批注)职责区分:
- AuditNote = 审计工作流的正式附注(结构化:结论/评判/抽检/贴图/输出措施),审计时一次性产生
- ReportComment = 报告作为活文档的持续批注(轻量:内容/定位/作者/时间),任何时候可加,时间线累积

"只有报告才能审计": AuditNote.report_id 必填且 FK→reports.id。
归因不归责: 审计结论用于改进,输出 Measure 派生管理措施,形成 审计→措施 闭环。
"""

import enum

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class AuditJudgment(str, enum.Enum):
    PASS = "pass"  # 通过
    ISSUE = "issue"  # 有问题
    IMPROVE = "improve"  # 需改进


class AuditNote(IDMixin, TimestampMixin, Base):
    """审计附注(结构化,关联报告)"""

    __tablename__ = "audit_notes"

    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("reports.id"), nullable=False, index=True, comment="关联报告")
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True, comment="审计结论")
    judgment: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="评判:通过/有问题/需改进")
    sampled_tickets: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="抽检工单号列表")
    attachments: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="贴图附件列表 [{path,url},...] (聊天记录截图)"
    )
    output_measure_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("measures.id"), nullable=True, comment="输出的管理措施(可选,派生Measure)"
    )
    author_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="审计人")
    author_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="审计人姓名")
