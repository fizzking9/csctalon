"""AI 数据治理框架: governance_findings + governance_rules 两表 (工单 #162)

设计: docs/design/AI数据治理框架-20260919.md §三（数据模型）

背景（用户 2026-09-19 提出）：数据治理现状是「人发现坏数据 → 写脚本 → 人工判断 → 改数据」，
每次都要走开发流程，成本高且易误判（C-P1-7 实证：人工扫描 5 个候选索引，**4 个是误报**）。
本框架改为「系统 AI 机制自动分析 → 给治理建议 → 分级执行」。

两表分工：
- `governance_findings` —— 探测层产出（**只存证据，不存结论**）：
  metrics/samples/repro_sql 三件套保证可审计；verdict/suggestion 等研判字段由 #164 回填。
- `governance_rules` —— 研判结论的沉淀：下次同类 finding 命中规则即**跳过 AI 调用**（省 LLM）。

⚠️ 不 seed：两表均由运行时产生（探测/研判），与 ai_reports 同策略。

Revision ID: m3n4o5p6q7r8
Revises: h3i4j5k6l7m8
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'm3n4o5p6q7r8'
down_revision: Union[str, None] = 'h3i4j5k6l7m8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 探测结果 ──────────────────────────────────────────────
    op.create_table(
        'governance_findings',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('detector', sa.String(length=32), nullable=False,
                  comment='探测器标识: unique_index_dup / row_count_jump / fk_orphan'),
        sa.Column('table_name', sa.String(length=64), nullable=False, comment='目标表名'),
        sa.Column('target', sa.String(length=128), nullable=False,
                  comment='目标列/索引名（D1 为索引名）'),
        sa.Column('severity_raw', sa.String(length=8), nullable=False,
                  server_default='warn',
                  comment='探测器给的原始严重度 info/warn/high（仅参考, 最终以研判 verdict 为准）'),
        sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='量化证据: {dup_groups, redundant_rows, total_rows, ratio, gap_kind, ...}'),
        sa.Column('samples', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='样本行（脱敏, ≤10 条, 供 AI 研判）'),
        sa.Column('repro_sql', sa.Text(), nullable=True,
                  comment='复现 SQL（可在库上直接执行核对 metrics 数字, 拒绝黑盒结论）'),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False, comment='探测时刻'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='open',
                  comment='生命周期: open(待研判)/judged(已研判)/resolved(已治理)/ignored(裁定非问题)'),
        # ── 研判层产物（#164 回填）──
        sa.Column('verdict', sa.String(length=24), nullable=True,
                  comment='研判结论: real_issue / allowed_by_business / uncertain'),
        sa.Column('verdict_reason', sa.Text(), nullable=True,
                  comment='AI 判据（须引用规范条款或表说明）'),
        sa.Column('suggestion', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='{action, params, impact, risk, requires_human_confirm}'),
        sa.Column('confidence', sa.Float(), nullable=True, comment='研判置信度 0.0~1.0'),
        sa.Column('judged_at', sa.DateTime(timezone=True), nullable=True, comment='研判时刻'),
        sa.Column('rule_id', sa.Integer(), nullable=True,
                  comment='命中的治理规则 id（规则直接判定时跳过 AI 调用）'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # 列表主路径: 按 (状态, 探测时间倒序) 取待处理 finding
    op.create_index('ix_governance_findings_status', 'governance_findings', ['status'])
    op.create_index('ix_governance_findings_detected_at', 'governance_findings', ['detected_at'])
    op.create_index('ix_governance_findings_detector', 'governance_findings', ['detector'])
    op.create_index('ix_governance_findings_table_name', 'governance_findings', ['table_name'])
    op.create_index('ix_governance_findings_target', 'governance_findings', ['target'])
    op.create_index('ix_governance_findings_verdict', 'governance_findings', ['verdict'])
    op.create_index('ix_governance_findings_rule_id', 'governance_findings', ['rule_id'])
    # 幂等去重/查重路径: 同一 (detector, 表, 目标) 的历史 finding
    op.create_index(
        'ix_governance_findings_dedup',
        'governance_findings',
        ['detector', 'table_name', 'target', sa.text('detected_at DESC')],
    )

    # ── 治理规则沉淀 ──────────────────────────────────────────
    op.create_table(
        'governance_rules',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('detector', sa.String(length=32), nullable=False, comment='适用探测器标识'),
        sa.Column('table_name', sa.String(length=64), nullable=False,
                  comment='适用表（支持通配 "*" = 任意表）'),
        sa.Column('target_pattern', sa.String(length=128), nullable=False,
                  comment='目标匹配（精确或正则, 如 uq_wallet_*）'),
        sa.Column('match_metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='额外匹配条件（如 {"ratio": {"gt": 0.5}}）; NULL=不限制'),
        sa.Column('verdict', sa.String(length=24), nullable=False,
                  comment='该情形下的判定: real_issue / allowed_by_business'),
        sa.Column('reason', sa.Text(), nullable=False,
                  comment='判据说明（人读 + 引用规范条款）'),
        sa.Column('disposition', sa.String(length=32), nullable=False, server_default='report_only',
                  comment='建议动作: report_only/mark_ignored/mark_dup_residual/delete_dups/create_index'),
        sa.Column('disposition_params', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='动作参数（如 delete_dups 的保留策略）'),
        sa.Column('auto_level', sa.String(length=20), nullable=False, server_default='L0_readonly',
                  comment='执行级别: L0_readonly/L1_reversible/L2_irreversible（L2 恒需人工确认）'),
        sa.Column('hit_count', sa.Integer(), nullable=False, server_default='0',
                  comment='命中计数（学习信号: 识别高频问题）'),
        sa.Column('last_hit_at', sa.DateTime(timezone=True), nullable=True, comment='最后命中时刻'),
        sa.Column('source_finding_id', sa.Integer(), nullable=True,
                  comment='来自哪次研判（可追溯）'),
        sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人（人工确认者）'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true', comment='是否启用'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # 规则命中路径: 按 (detector, 表, 启用) 取候选后匹配 target_pattern
    op.create_index('ix_governance_rules_detector', 'governance_rules', ['detector'])
    op.create_index('ix_governance_rules_table_name', 'governance_rules', ['table_name'])
    op.create_index('ix_governance_rules_target_pattern', 'governance_rules', ['target_pattern'])
    op.create_index('ix_governance_rules_verdict', 'governance_rules', ['verdict'])
    op.create_index('ix_governance_rules_disposition', 'governance_rules', ['disposition'])
    op.create_index('ix_governance_rules_auto_level', 'governance_rules', ['auto_level'])
    op.create_index('ix_governance_rules_enabled', 'governance_rules', ['enabled'])


def downgrade() -> None:
    op.drop_index('ix_governance_rules_enabled', table_name='governance_rules')
    op.drop_index('ix_governance_rules_auto_level', table_name='governance_rules')
    op.drop_index('ix_governance_rules_disposition', table_name='governance_rules')
    op.drop_index('ix_governance_rules_verdict', table_name='governance_rules')
    op.drop_index('ix_governance_rules_target_pattern', table_name='governance_rules')
    op.drop_index('ix_governance_rules_table_name', table_name='governance_rules')
    op.drop_index('ix_governance_rules_detector', table_name='governance_rules')
    op.drop_table('governance_rules')

    op.drop_index('ix_governance_findings_dedup', table_name='governance_findings')
    op.drop_index('ix_governance_findings_rule_id', table_name='governance_findings')
    op.drop_index('ix_governance_findings_verdict', table_name='governance_findings')
    op.drop_index('ix_governance_findings_target', table_name='governance_findings')
    op.drop_index('ix_governance_findings_table_name', table_name='governance_findings')
    op.drop_index('ix_governance_findings_detector', table_name='governance_findings')
    op.drop_index('ix_governance_findings_detected_at', table_name='governance_findings')
    op.drop_index('ix_governance_findings_status', table_name='governance_findings')
    op.drop_table('governance_findings')
