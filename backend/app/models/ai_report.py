"""AI 分析报告历史持久化 (工单 #116)

设计:
- 每条记录 = 一次 AI 分析任务成功后落库的完整产物(question + answer_md + 窗口 + 用时 + token)
- 仅 success 落库(queued/running/failed 不落库, 失败看 llm_call_logs 审计表)
- 归属: user_id 隔离, 仅本人可见; 管理员 role==admin 可查全部
- 永久保留, 无 TTL(用户在工单 #116 明确选永久保留)
- 关联 users(id) ON DELETE CASCADE: 用户被删除时报告级联清空

字段说明:
- task_id: 内存 ai_task_manager 的 task_id(ai_yyyymmddhhmmss_xxxxxx), 仅用于排查关联
- llm_model: 命中的 provider+model 字符串(如 "talon-gmk-qwen3.8:27b"), 便于复盘模型质量差异
- llm_ms/total_ms: 单位毫秒, 来自 ai.py::_runner t0 时间戳
- prompt_tokens/completion_tokens/total_tokens: OpenAI 兼容响应 usage 字段; 服务端不返则 NULL
- 规则层秒回路径(#98 层3)落库时 llm_model="rule-engine", tokens=NULL
"""

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin


class AiReport(IDMixin, TimestampMixin, Base):
    """AI 分析报告历史(仅本人可见; 管理员可查全部)。

    #121 扩字段:
    - template_id: 来自预生成则为模板 id, 实时生成则 NULL
    - snapshot_date / snapshot_period: 数据时点(区别于 created_at)
    - is_public: true=公共报告(所有人可看, 来自预生成), false=私有(仅本人, 来自实时)
    - matched_count: 被命中复用次数
    """

    __tablename__ = "ai_reports"

    task_id: Mapped[str | None] = mapped_column(
        String(32), unique=True, nullable=True, index=True,
        comment="对应内存 ai_task_manager 的 task_id; 规则层秒回无 task_id 则 NULL",
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
        comment="归属用户 id(仅本人可见, admin 可查全部); 公共报告(is_public=True)所有人可看",
    )
    template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_report_templates.id", ondelete="SET NULL"),
        nullable=True, index=True,
        comment="#121: 来自预生成则为模板 id, 实时生成则 NULL",
    )
    snapshot_date: Mapped[str | None] = mapped_column(
        Date, nullable=True, index=True,
        comment="#121: 报告数据时点(窗口起算 date, 区别于 created_at 的入库时点)",
    )
    snapshot_period: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="#121: daily/weekly/monthly/range(与模板 period 对应)",
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="#121: true=公共报告(所有人可看, 来自预生成); false=私有(仅本人, 来自实时)",
    )
    matched_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="#121: 被命中复用次数(match_template_report 命中时 +1)",
    )
    question: Mapped[str] = mapped_column(Text, nullable=False, comment="原问句")
    period: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="窗口周期 daily/weekly/monthly/range",
    )
    date: Mapped[str | None] = mapped_column(Date, nullable=True, comment="显式日期(纯日期)")
    start: Mapped[str | None] = mapped_column(Date, nullable=True, comment="区间起点(纯日期)")
    end: Mapped[str | None] = mapped_column(Date, nullable=True, comment="区间终点(纯日期)")
    window_source: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="窗口判定来源: explicit_range/explicit_date/nl/nl_llm/period/rule",
    )
    window_label: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="窗口说明(如 2026-09-15(今天))",
    )
    answer_md: Mapped[str] = mapped_column(Text, nullable=False, comment="LLM 返回的 markdown 报告")
    llm_model: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="命中的 provider+model; 规则层秒回=rule-engine",
    )
    llm_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="LLM 推理耗时(ms)")
    total_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="端到端总耗时(ms)")
    prompt_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="输入 token(OpenAI usage.prompt_tokens); 服务端不返则 NULL",
    )
    completion_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="输出 token(OpenAI usage.completion_tokens); 服务端不返则 NULL",
    )
    total_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="总 token(OpenAI usage.total_tokens); 服务端不返则 NULL",
    )
