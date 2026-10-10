"""AI 报告用户评价 (工单 #174 演进: 评价采集→周期汇总分析)

用户在报告/对话结果页对 AI 产物打分+留意见，**只采集不实时分析**；
评估中心按周期（与健康报告同款当日语义）汇总这些评价，产出
「评价归因」——低分报告共性、高频意见关键词，喂给迭代建议。

设计说明（Design Rationale）
- 目的：把「执行结果」缺的用户主观反馈补上（评估中心原来只有客观数据：
  命中率/缺失维度/高频兜底）。评价=采集（report_id/rating/opinion），
  **不做实时逐条分析**（设计文档 §1.4 非目标）；分析走周期聚合。
- 逻辑：一张 report_id+user_id 维度表，每条 = 一次评价采集；聚合端
  (prompt_eval.build_summary) 按窗口汇总 rating 分布 + 低频共性低分归因。
- 取舍：不限制一用户一报告只能评一次（允许多次反映迭代前后评价变化），
  但前端收集入口轻量，避免打断看报告；评价不直接改任何提示词/模板，
  只作为「参考依据」进入版本管理（#178 references）。
"""

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class AiReportFeedback(IDMixin, TimestampMixin, Base):
    """一条 AI 报告评价采集（打分 + 可选意见）。"""

    __tablename__ = "ai_report_feedbacks"

    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_reports.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="被评价的 AI 报告 id",
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="评价人 user_id",
    )
    rating: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="评分 1(未解决/没用)~5(很满意/很准)",
    )
    opinion: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="可选意见/改进建议（自由文本，供汇总归因）",
    )
    hit_mode: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="评价时该报告命中粒度快照(full/section/live/rule)，供按命中归因",
    )
