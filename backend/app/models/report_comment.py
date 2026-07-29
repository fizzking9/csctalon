"""报告/周报跟进注释 - 统一的活文档批注模型

设计原则(需求说明):
- 周报/报告确认固化后, 后续跟进说明通过注释累积(时间线), 不改原文
- 注释可派生管理措施(跟进中发现问题→转措施, 形成"注释→措施"闭环)
- target_type 区分周报/报告; section_key 定位到周报某个分项(报告时为空)

与 AuditNote 的区别:
- AuditNote = 审计附注(结论+评判+抽检工单+贴图), 必须关联 report_id, 偏正式审计
- ReportComment = 轻量跟进注释(纯文字+可派生措施), 周报/报告通用, 偏日常跟进
"""

import enum

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class CommentTarget(str, enum.Enum):
    WEEKLY = "weekly"  # 周报看板
    REPORT = "report"  # 专项报告


class ReportComment(IDMixin, TimestampMixin, Base):
    """跟进注释(周报/报告通用, 时间线累积, 可派生措施)"""

    __tablename__ = "report_comments"

    target_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="目标类型: weekly/report"
    )
    target_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="目标ID(周报ID/报告ID)")
    section_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="定位到周报某分项(报告时为空)"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="注释内容")
    author_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, comment="作者")
    author_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="作者姓名")
    output_measure_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="派生的管理措施ID(跟进中发现问题→转措施)"
    )
