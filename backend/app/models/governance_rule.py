"""治理规则沉淀 (工单 #162, 设计: docs/design/AI数据治理框架-20260919.md §3.2)

把「研判层的判断结论」沉淀为**可复用规则**：下次同类 finding 直接命中规则 →
**跳过 AI 调用**（省 LLM，且保证同一问题判定一致），见设计 §5.4。

与 `quality_disposition_rules` 的关系（设计 §3.2 明示）：
- 后者是**行级**处置规则（针对具体行标 quality_status）
- 本表是**结构性**治理规则（针对表/索引级问题）
- 二者互补：本表的执行动作**复用**后者的处置能力，不重写一套。

`auto_level` 三级（设计 §六，红线在 L2）：
- L0_readonly     只读（生成报告/留痕），可自动
- L1_reversible   可逆（标 quality_status，可用 quality_restore 回滚），规则命中时可自动
- L2_irreversible 不可逆（删行/改值/建索引/改表结构）→ **必须人工确认**，禁止自动
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# ── auto_level 值域（执行层分级，见设计 §六）──────────────────
AUTO_LEVEL_READONLY = "L0_readonly"
AUTO_LEVEL_REVERSIBLE = "L1_reversible"
AUTO_LEVEL_IRREVERSIBLE = "L2_irreversible"

# 禁止自动执行的级别（红线：不可逆动作必须人工确认）
MANUAL_ONLY_LEVELS = (AUTO_LEVEL_IRREVERSIBLE,)

# ── disposition 值域（建议动作）──────────────────────────────
DISPOSITION_REPORT_ONLY = "report_only"      # 只报告（不改数据）
DISPOSITION_MARK_IGNORED = "mark_ignored"    # 标 ignored（行级，走 quality_disposition_service）
DISPOSITION_MARK_DUP = "mark_dup_residual"   # 标 dup_residual（行级）
DISPOSITION_DELETE_DUPS = "delete_dups"      # 删重复行（L2，必须人工确认 + 备份）
DISPOSITION_CREATE_INDEX = "create_index"    # 建索引/约束（L2，须走 Alembic 迁移）
DISPOSITION_FIX_TIME = "fix_time"            # 修时间值（L2，采集语义增强用）
DISPOSITION_MARK_PENDING = "mark_pending"    # 标 quality_status=pending 待核（L1，工单 #169）

# ── 动作 → 执行级别映射（**唯一来源**，工单 #165）─────────────
# 为什么必须有这张表：分级是红线（L2 恒需人工确认），若「哪个动作是 L2」散落在
# 执行服务 / 路由 / 前端三处，迟早漂移成「某处漏判 → 不可逆动作被自动执行」。
# 执行层据此判级、研判层据此写 suggestion、前端据此决定是否要确认按钮。
# ⚠️ `governance_judge_service._IRREVERSIBLE_ACTIONS` / `_VALID_ACTIONS` 由本表**派生**
#    （不另写一份，否则新增动作时会漏进红线判定 —— #169 补 fix_time 时即暴露该风险）。
DISPOSITION_LEVELS: dict[str, str] = {
    DISPOSITION_REPORT_ONLY: AUTO_LEVEL_READONLY,
    DISPOSITION_MARK_IGNORED: AUTO_LEVEL_REVERSIBLE,
    DISPOSITION_MARK_DUP: AUTO_LEVEL_REVERSIBLE,
    DISPOSITION_MARK_PENDING: AUTO_LEVEL_REVERSIBLE,
    DISPOSITION_DELETE_DUPS: AUTO_LEVEL_IRREVERSIBLE,
    DISPOSITION_CREATE_INDEX: AUTO_LEVEL_IRREVERSIBLE,
    DISPOSITION_FIX_TIME: AUTO_LEVEL_IRREVERSIBLE,
}


def disposition_level(action: str | None) -> str:
    """动作 → 执行级别；未登记的动作一律按**最严**处理（L2 需人工确认），不放过未知动作。"""
    return DISPOSITION_LEVELS.get(action or "", AUTO_LEVEL_IRREVERSIBLE)


class GovernanceRule(IDMixin, TimestampMixin, Base):
    """治理规则：一次研判结论的沉淀，后续同类问题直接复用（不再调 LLM）。"""

    __tablename__ = "governance_rules"

    detector: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False, comment="适用探测器标识",
    )
    table_name: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False,
        comment='适用表（支持通配 "*" = 任意表）',
    )
    target_pattern: Mapped[str] = mapped_column(
        String(128), index=True, nullable=False,
        comment="目标匹配（精确或正则, 如 uq_wallet_* / *dedup*）",
    )
    match_metrics: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="额外匹配条件（如 {\"ratio\": {\"gt\": 0.5}}）; NULL=不限制",
    )
    verdict: Mapped[str] = mapped_column(
        String(24), index=True, nullable=False,
        comment="该情形下的判定: real_issue / allowed_by_business",
    )
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, comment="判据说明（人读 + 引用规范条款）",
    )
    disposition: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False, default=DISPOSITION_REPORT_ONLY,
        comment="建议动作: report_only/mark_ignored/mark_dup_residual/mark_pending"
                "/delete_dups/create_index/fix_time",
    )
    disposition_params: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="动作参数（如 delete_dups 的保留策略）",
    )
    auto_level: Mapped[str] = mapped_column(
        String(20), index=True, nullable=False, default=AUTO_LEVEL_READONLY,
        comment="执行级别: L0_readonly/L1_reversible/L2_irreversible（L2 恒需人工确认）",
    )
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="命中计数（学习信号: 识别高频问题）",
    )
    last_hit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最后命中时刻",
    )
    # ── 效果回填（工单 #173, 设计 §3.1: 规则健康度信号）──────────────
    # fp_ratio = effect_fp / (effect_executed + effect_rejected + effect_fp)
    # fp_ratio > 0.5 → 评估中心建议停用/降级
    effect_executed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
        comment="按规则执行成功次数（execute 时 +1）",
    )
    effect_rejected: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
        comment="人工驳回次数（规则命中但人工否定建议）",
    )
    effect_fp: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
        comment="误报次数（规则命中但确认业务允许/无问题）",
    )
    last_effect_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次效果事件时间",
    )
    source_finding_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="来自哪次研判（可追溯）",
    )
    created_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="创建人（人工确认者）",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True, comment="是否启用",
    )
