"""治理探测结果 (工单 #162, 设计: docs/design/AI数据治理框架-20260919.md §3.1)

AI 数据治理框架的**探测层产出**：一次探测发现的「结构性可疑」。

设计要点：
- 本表只存**证据**，不存结论 —— 结论由研判层（AI，工单 #164）回填到 verdict/suggestion 等字段。
  这样「探测器（确定性、无 AI）」与「研判（AI）」解耦：探测可以高频跑，研判按需跑。
- `metrics` / `samples` / `repro_sql` 三件套是**可审计**的关键：
  * metrics  量化证据（重复组数 / 冗余行数 / 占比）
  * samples  样本行（**脱敏**，≤10 条，供 AI 研判）
  * repro_sql 复现 SQL（人工可直接在库上执行，核对 metrics 数字 —— 不接受「黑盒结论」）
- `rule_id` 命中时表示「由已沉淀规则直接判定」，跳过 AI 调用（省 LLM，见 §5.4）。

与 `quality_disposition_rules` 的分工：后者管**行级**处置（给具体行打 quality_status），
本表管**结构性**问题（表/索引级），二者互补。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# ── detector 标识（探测层 §4.1）──────────────────────────────
DETECTOR_UNIQUE_INDEX_DUP = "unique_index_dup"   # D1: 唯一索引重复（工单 #162）
DETECTOR_ROW_COUNT_JUMP = "row_count_jump"       # D2: 表级行数突变（工单 #163）
DETECTOR_FK_ORPHAN = "fk_orphan"                 # D3: 外键孤儿（工单 #163）
DETECTOR_TEXT_ANOMALY = "text_anomaly"           # D4: 文本异常（换行/贴列宽上限，工单 #168）
DETECTOR_IMPOSSIBLE_DATE = "impossible_date"     # D5: 不可能日期（越界/未来/倒挂/聚集，工单 #169）
DETECTOR_FILLABLE_GAP = "fillable_gap"           # D6: 可推断空缺（有权威来源可推，工单 #170）

# ── status 值域（finding 生命周期）──────────────────────────
FINDING_STATUS_OPEN = "open"          # 刚探测出，待研判
FINDING_STATUS_JUDGED = "judged"      # 已研判（verdict 已回填），待处置
FINDING_STATUS_RESOLVED = "resolved"  # 已执行治理动作并核销
FINDING_STATUS_IGNORED = "ignored"    # 人工裁定「非问题」（并沉淀为规则）
FINDING_STATUS_AUTO_CLOSED = "auto_closed"  # 重扫不再命中 → 自动收敛（**未执行任何动作**，见 #167）

# 重扫收敛时允许被自动关闭的状态（终态 ignored/resolved/auto_closed 不再动）
CLOSABLE_STATUSES = (FINDING_STATUS_OPEN, FINDING_STATUS_JUDGED)

# ── verdict 值域（研判结论）────────────────────────────────
VERDICT_REAL_ISSUE = "real_issue"                  # 真问题
VERDICT_ALLOWED_BY_BUSINESS = "allowed_by_business"  # 业务允许（如可空列多个 NULL）
VERDICT_UNCERTAIN = "uncertain"                    # 拿不准（低 confidence）

# ── severity_raw 值域（探测器给的原始严重度，仅参考，最终以研判为准）──
SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_HIGH = "high"


class GovernanceFinding(IDMixin, TimestampMixin, Base):
    """一次探测发现的结构性可疑（证据，不含结论）。"""

    __tablename__ = "governance_findings"

    detector: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False,
        comment="探测器标识: unique_index_dup / row_count_jump / fk_orphan",
    )
    table_name: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, comment="目标表名",
    )
    target: Mapped[str] = mapped_column(
        String(128), index=True, nullable=False,
        comment="目标列/索引名（D1 为索引名, 如 uq_wallet_withdrawals_business）",
    )
    severity_raw: Mapped[str] = mapped_column(
        String(8), index=True, nullable=False, default=SEVERITY_WARN,
        comment="探测器给的原始严重度 info/warn/high（仅参考, 最终以研判 verdict 为准）",
    )
    metrics: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="量化证据: {dup_groups, redundant_rows, total_rows, ratio, gap_kind, ...}",
    )
    samples: Mapped[list | None] = mapped_column(
        JSONB, nullable=True,
        comment="样本行（**脱敏**, ≤10 条, 供 AI 研判）",
    )
    repro_sql: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="复现 SQL（可在库上直接执行核对 metrics 数字, 拒绝黑盒结论）",
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, comment="探测时刻",
    )
    status: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False, default=FINDING_STATUS_OPEN,
        comment="生命周期: open(待研判)/judged(已研判)/resolved(已治理)/ignored(裁定非问题)"
                "/auto_closed(重扫不再命中, 自动收敛)",
    )

    # ── 研判层产物（工单 #164 回填；本工单只建字段）──
    verdict: Mapped[str | None] = mapped_column(
        String(24), index=True, nullable=True,
        comment="研判结论: real_issue / allowed_by_business / uncertain",
    )
    verdict_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="AI 判据（须引用规范条款或表说明）",
    )
    suggestion: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="{action, params, impact, risk, requires_human_confirm}",
    )
    confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="研判置信度 0.0~1.0",
    )
    judged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="研判时刻",
    )
    rule_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
        comment="命中的治理规则 id（规则直接判定时跳过 AI 调用）",
    )

    # ── 执行层产物（工单 #165 回填；分级执行见设计 §六）──
    confirmed_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="人工确认人（L2 执行前置红线）",
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="人工确认时刻",
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="执行时刻",
    )
    execute_result: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="执行凭证: {action, auto_level, affected_rows, before, after, backup_table, backup_rows, sql, cache}",
    )
