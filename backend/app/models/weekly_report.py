"""周报看板模型 - 10项标准框架,复用 Report 的快照/审计机制

设计原则(需求说明 §12.4 + 周报看板工作流):
- 周报看板 = "视图(本周数据) + 跟进摘要" 的组合样式(对齐实际周报docx格式)
- 周报是"活文档": 确认后固化快照, 可持续批注(ReportComment), 可下钻审计(关联 Report)
- 视图自动链接: 管理员配一次默认绑定(WeeklySectionBinding), 一键生成本周看板时自动填充
- 草稿态视图可动态增删(软覆盖本次, 不改默认配置)
- 会议评审: 草稿态可编辑会议结论, 确认时固化, 后续补充走 ReportComment 留痕

10项框架(固定结构, 顺序固定):
  1. measure_followup   措施跟进        (section_type=measures)
  2. reception_trend    总体接待人均趋势 (section_type=view)
  3. reception_summary  分项接待汇总     (section_type=view)
  4. group_reception    各组接待量       (section_type=view)
  5. refund_recovery    退款挽回专项     (section_type=view)
  6. complaint          投诉专项(内+外315)(section_type=view)
  7. issues             问题汇总        (section_type=text)
  8. measure_plan       措施计划(输出下周)(section_type=measures)
  9. cross_dept         跨部门协调(含上游打分)(section_type=text)
 10. incidents          异常事故        (section_type=incidents)

framework JSONB section 结构:
  {key, title, section_type, view_ids[], owner_id, summary, data, related_report_ids[]}
- view_ids: section_type=view 时关联的分析视图列表(可动态增删), 执行时锁定本周时间范围
- owner_id: 该分项负责人(软归属, 默认从绑定配置填充)
- summary: 跟进摘要/关键数据(文字, 主管/组长会前预填或会上共同填写)
- data: 快照(确认时固化的视图执行结果, 按 view_id 索引 {view_id: result}; 草稿态为空)
- related_report_ids: 下钻专项报告列表(会议中对某专项有异议→派生专项报告)
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class WeeklyReportStatus(str, enum.Enum):
    DRAFT = "draft"  # 草稿(可编辑)
    CONFIRMED = "confirmed"  # 已确认(快照已固化,可批注/审计)
    ARCHIVED = "archived"  # 已归档


# 周报 10 项标准框架(顺序固定, 不可调整)
# section_type: view=分析视图 / measures=措施列表 / incidents=异常列表 / text=纯文字
WEEKLY_FRAMEWORK_SECTIONS = [
    {"key": "measure_followup", "title": "措施跟进", "section_type": "measures"},
    {"key": "reception_trend", "title": "总体接待人均趋势", "section_type": "view"},
    {"key": "reception_summary", "title": "分项接待汇总", "section_type": "view"},
    {"key": "group_reception", "title": "各组接待量", "section_type": "view"},
    {"key": "refund_recovery", "title": "退款挽回专项", "section_type": "view"},
    {"key": "complaint", "title": "投诉专项(内+外315)", "section_type": "view"},
    {"key": "issues", "title": "问题汇总", "section_type": "text"},
    {"key": "measure_plan", "title": "措施计划(输出下周)", "section_type": "measures"},
    {"key": "cross_dept", "title": "跨部门协调(含上游打分)", "section_type": "text"},
    {"key": "incidents", "title": "异常事故", "section_type": "incidents"},
]


def default_framework() -> list:
    """生成空白 10 项框架(各 section 含 key/title/section_type, 内容待填)"""
    return [
        {
            "key": s["key"],
            "title": s["title"],
            "section_type": s["section_type"],
            "view_ids": [],  # 列表, 支持多视图
            "owner_id": None,  # 负责人(软归属)
            "summary": "",
            "data": None,  # 确认时固化: {view_id: 执行结果}
            "related_report_ids": [],  # 下钻专项报告列表
        }
        for s in WEEKLY_FRAMEWORK_SECTIONS
    ]


class WeeklyReport(IDMixin, TimestampMixin, Base):
    """周报看板(10项标准框架, 复用 Report 快照/审计机制)"""

    __tablename__ = "weekly_reports"

    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="周报看板名(如 2026-W30 客服中心周报)")
    week_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True, comment="周起始(周一)"
    )
    week_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="周结束(周日)")
    framework: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        comment="10项框架 [{key,title,section_type,view_ids[],owner_id,summary,data,related_report_ids[]}]",
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="周报总评/概述")
    meeting_conclusion: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="会议评审结论(周会记录,确认时固化)"
    )
    status: Mapped[str] = mapped_column(
        String(32), default=WeeklyReportStatus.DRAFT.value, nullable=False, index=True, comment="状态:草稿/已确认/已归档"
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="确认(固化)时间")
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="确认人")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="创建人")
