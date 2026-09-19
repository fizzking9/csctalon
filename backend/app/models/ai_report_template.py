"""AI 报告模板配置 (工单 #119)

设计:
- 每条记录 = 一份可调度生成的 AI 报告模板(同源 prompt + 关键词匹配 + 多模型分工)
- 模板同源: question_template 是 prompt 内容; 前台实时与后台预生成共用同一份 prompt
- 关键词路由: question_keywords 用于匹配用户问句到对应模板(类似 ai_glossary 关键词路由)
- 多模型分工: llm_provider_code 可指定特定 provider 跑此模板, 默认 None 走多 provider 优先级
- 失败兜底: fallback_template_id 指向简版模板, 详尽模板失败时降级到简版预生成结果
- 参数分级: max_tokens 后台默认 2000(详尽), 前台实时由调用方覆盖为 800(简洁防截断)
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
        Integer, nullable=False, default=2000,
        comment="后台预生成 token 上限(详尽), 默认 2000; 前台实时由调用方覆盖为 800",
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
