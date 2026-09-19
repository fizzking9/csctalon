"""数据源接入声明（AI 数据接入助手，工单 #155）

设计说明（Design Rationale）
- 目的：把「新数据源接入」从人肉写 DATA_SOURCE_SEEDS 变成「AI 起草声明 → 校验器拦截
  → 人工确认 → 生成落地包」的可审计流程。一行 = 一次接入意图（草稿/已确认/已落地）。
- 逻辑：声明主体字段与 DataSource 对齐（确认后可直接映射），另存 AI 产物
  （字段语义映射 field_specs / 去重建议 dedup_recommendation）与校验结果
  （validator_report），保证「AI 建议、规则校验、人工确认」三层可分别追责。
- 取舍：本表只存**建议与流转状态**，不直接落 data_sources —— 落地仍走
  「建表迁移 + registry 种子 + CI 测试」现有流程（见 docs/design/AI数据接入助手设计-20260919.md §六）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import IDMixin, TimestampMixin

# 接入声明流转状态
SPEC_STATUS_DRAFT = "draft"  # AI 起草完成，待人工确认
SPEC_STATUS_CONFIRMED = "confirmed"  # 人工确认通过（fail 清零 + warn 已备注处置）
SPEC_STATUS_APPLIED = "applied"  # 已按落地包走完现有流程
SPEC_STATUS_REJECTED = "rejected"  # 人工拒绝


class DataSourceSpec(IDMixin, TimestampMixin, Base):
    """数据源接入声明（草稿态 + 已确认态）。"""

    __tablename__ = "data_source_specs"

    # ── 声明主体（字段对齐 DataSource，确认后可直接映射）──
    code: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False,
        comment="建议 code（须与现有 registry 全树唯一，改名属破坏性变更）",
    )
    table_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="物理表名（须与 model.__tablename__ 一致）",
    )
    name_cn: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="中文名（须过前缀白名单 + 全树唯一 + 禁 code 原文）",
    )
    category: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="业务分组（须命中 CATEGORY_ORDER）",
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=999, nullable=False)
    time_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sn_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pk_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pk_strategy: Mapped[str] = mapped_column(
        String(32), default="primary_key", nullable=False,
        comment="primary_key/none/upsert（去重策略，红线，人工必核）",
    )
    sync_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 流转状态 ──
    status: Mapped[str] = mapped_column(
        String(16), default=SPEC_STATUS_DRAFT, nullable=False, index=True,
        comment="draft/confirmed/applied/rejected",
    )
    issue_no: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="落地工单号（确认后填写，按 issue-workflow）",
    )

    # ── AI 与校验产物 ──
    sample_summary: Mapped[list | None] = mapped_column(
        JSONB, nullable=True,
        comment="样例摘要：列名/类型/行数/每列前 N 个样例值（脱敏），仅供审计",
    )
    field_specs: Mapped[list | None] = mapped_column(
        JSONB, nullable=True,
        comment="字段语义映射数组（field_name/name_cn/semantic_role/confidence/evidence 等）",
    )
    dedup_recommendation: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="去重建议：{strategy, fields, rationale, requires_human_confirm}（红线，AI 永不自决）",
    )
    validator_report: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="校验报告：{pass:[], warn:[], fail:[]}，fail 未清零禁止确认",
    )
    apply_package: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="确认后生成的落地包（迁移建议/种子 JSON/测试清单），仅建议不执行",
    )

    # ── 审计 ──
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    confirmed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
