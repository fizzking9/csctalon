"""AI 报告模板配置 (工单 #119)

设计:
- 每条记录 = 一份可调度生成的 AI 报告模板(同源 prompt + 关键词匹配 + 多模型分工)
- 模板同源: question_template 是 prompt 内容; 前台实时与后台预生成共用同一份 prompt
- 关键词路由: question_keywords 用于匹配用户问句到对应模板(类似 ai_glossary 关键词路由)
- 多模型分工: llm_provider_code 可指定特定 provider 跑此模板, 默认 None 走多 provider 优先级
- 失败兜底: fallback_template_id 指向简版模板, 详尽模板失败时降级到简版预生成结果
- 参数分级: 预生成 max_tokens 按 period 分档(见 DEFAULT_MAX_TOKENS_BY_PERIOD, R-P1-4):
  详尽周/月/滚动长报告 4000/5000(R3 实测长报告不低于 4000), 日报 2500;
  前台**实时问答**不读此值, 由 settings.AI_MAX_TOKENS=1500(简洁档)单独控制
- 调度: frequency(sync-triggered/daily/weekly/monthly) + trigger_at(HH:MM 或 weekday:H)
- 权限: visibility_scope(public/admin_only); 模板管理仅 admin, 报告查看全员(public) + 私有(user_id)
- 关联 users(id) ON DELETE SET NULL: 创建者被删时模板保留(created_by 置 NULL)
- 自引用 fallback_template_id ON DELETE SET NULL: 简版模板被删时降级字段置 NULL
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# ── 预生成 max_tokens 按 period 分档(R-P1-4 / #240, 单一来源) ──────────────
# 取值与存量逐一对齐: #134 迁移(daily 2500 / weekly 4000 / monthly 5000) 与
# rolling seed(rolling_7d 4000 / rolling_30d 5000)。
# 依据 R3《20260927 算力深度评估》: 详尽长报告经 1200→2400→4000 三轮迭代才不截断,
# 生产长报告 max_tokens 不应低于 4000。**仅用于后台预生成模板**; 前台实时问答走
# settings.AI_MAX_TOKENS(简洁档), 与此无关。
DEFAULT_MAX_TOKENS_BY_PERIOD: dict[str, int] = {
    "daily": 2500,       # 日报相对短(#134)
    "weekly": 4000,      # 周报覆盖 7 天多维度
    "monthly": 5000,     # 月报 30 天逐日走势 + 多维度, 空间最大
    "rolling_7d": 4000,  # 滚动周报(与 weekly 同档)
    "rolling_30d": 5000, # 滚动月报(与 monthly 同档)
}
# 未知/自定义 period 的兜底: 按 R3 长报告下限 4000(宁可宽裕, 不可截断)。
DEFAULT_MAX_TOKENS_LONG_REPORT = 4000


def default_max_tokens_for_period(period: str | None) -> int:
    """按 period 返回预生成模板的业务默认 max_tokens(创建模板时未显式指定则用此)。

    未知 period 按长报告下限 4000 兜底(模板的用途定位就是"详尽完整草稿")。
    """
    return DEFAULT_MAX_TOKENS_BY_PERIOD.get((period or "").strip(),
                                            DEFAULT_MAX_TOKENS_LONG_REPORT)


def resolve_max_tokens(period: str | None, explicit: int | None) -> int:
    """创建模板时决定 max_tokens: 显式正整数优先, 否则按 period 取分档默认。

    explicit 为 None/0 等假值时视为"未指定"(0 不是合法输出上限), 回退 period 默认。
    """
    if explicit:
        return int(explicit)
    return default_max_tokens_for_period(period)


class AiReportTemplate(IDMixin, TimestampMixin, Base):
    """AI 报告模板配置(admin 管理, 用于后台预生成 + 前台匹配命中)。"""

    __tablename__ = "ai_report_templates"

    code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
        comment="模板编码(如 daily_general_morning), 全树唯一",
    )
    name_cn: Mapped[str] = mapped_column(
        String(128), nullable=False,
        comment="显示名(如「每日晨会·整体经营」), admin 管理界面与通知用",
    )
    question_template: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="问题/prompt 模板, 可含变量 {{date}} {{period}}; 前后台同源共用",
    )
    question_keywords: Mapped[list | None] = mapped_column(
        JSONB, nullable=True,
        comment='关键词数组(如 ["今天","今日","整体","总览"]); 用于匹配用户问句到对应模板',
    )
    period: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="窗口周期 daily/weekly/monthly/range",
    )
    frequency: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="调度频率: sync-triggered(同步后触发) / daily / weekly / monthly",
    )
    trigger_at: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        comment="触发时刻: daily→HH:MM(如 09:00); weekly→wday:H(如 1:09=周一9点); monthly→day:H",
    )
    sync_offset_min: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5,
        comment="sync-triggered 频率下, 同步完成后延迟分钟数(避免数据未稳), 默认 5",
    )
    refresh_on_sync: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment=(
            "#130 是否随高频数据同步刷新: True=每次同步成功后刷新(时效性强的日报类); "
            "False=仅按 trigger_at 定时(周报/月报每日一次)。"
            "新建未显式传值时按 period/frequency 自动识别(daily/sync-triggered→True)"
        ),
    )
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="overview", server_default="overview",
        comment=(
            "#181 专项域: overview(整体经营)/refund(退款含涉诉)/complaint(投诉三源)/"
            "reception(接待专项); 生成按此分桶(feed 聚焦不截断), 匹配按问句专项路由"
        ),
    )
    format: Mapped[str] = mapped_column(
        String(16), nullable=False, default="brief", server_default="brief",
        comment="#181 体裁: brief(简报, 结论导向)/detail(明细, 逐维度展开); 匹配按问句详略意图",
    )
    detail_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="standard", server_default="standard",
        comment=(
            "#152 AI 回答模式分级: brief(简报, 快照摘取秒回)/standard(明细, 快照优先+完整性校验"
            "缺维度补算)/deep(深度, 实时 27b 全量 feed)。模板期望的回答详细级别, 预生成按此组织输出密度"
        ),
    )
    required_dimensions: Mapped[list | None] = mapped_column(
        JSONB, nullable=True,
        comment=(
            "#152 业务维度清单(JSONB 关键词数组, 如 [\"四路\",\"涉诉\",\"挽回\"]): 完整性校验器按此"
            "检查快照维度覆盖(关键词在 answer_md 中有效出现, 排除\"未提供/暂无\"语境); "
            "补算/深度实时生成时注入 prompt 要求必须覆盖; NULL=不做完整性校验(简报类)"
        ),
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100,
        comment="多模板同时触发时的执行顺序(小先执行), 默认 100",
    )
    llm_provider_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        comment="指定 provider code 跑此模板; None 走多 provider 优先级遍历(默认)",
    )
    fallback_template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_report_templates.id", ondelete="SET NULL"),
        nullable=True,
        comment="LLM 调用失败时降级到的简版模板 id; None=不降级",
    )
    max_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MAX_TOKENS_LONG_REPORT,
        comment=("后台预生成 token 上限(详尽); 业务默认按 period 分档(创建入口用 "
                 "default_max_tokens_for_period: daily 2500/weekly 4000/monthly 5000), "
                 "本列 default 仅 ORM 兜底=4000(R3 长报告下限); 前台实时问答由 "
                 "settings.AI_MAX_TOKENS 单独控制, 不读此值"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="启停开关; False 时调度器跳过此模板",
    )
    visibility_scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public",
        comment="可见性: public(全员可看预生成报告) / admin_only(仅 admin 可看)",
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="创建者 user_id; 用户被删时置 NULL(模板保留)",
    )
