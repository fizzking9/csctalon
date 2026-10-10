"""AI 提示词评估中心: governance_rules 效果字段 + prompt_iterations 表 (工单 #173)

设计: docs/design/AI提示词评估中心-20260920.md §三（数据模型）

两块变更：
- `governance_rules` 扩效果回填字段 —— 规则级评估需要「执行成功/人工驳回/误报」
  三种效果信号，支撑规则健康度（fp_ratio > 0.5 → 建议停用/降级）。
- 新增 `prompt_iterations` 表 —— 半自动执行留痕：AI 建议 → 人工确认 → 应用，
  每次写入记录 谁/何时/old→new/状态机（proposed/confirmed/applied/rejected）。

⚠️ 不 seed：两表均由运行时产生（评估产建议 / 人工拍板写库）。

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 's9t0u1v2w3x4'
down_revision: Union[str, None] = 'r8s9t0u1v2w3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── governance_rules 效果回填（规则健康度信号）──────────────────
    op.add_column("governance_rules", sa.Column(
        "effect_executed", sa.Integer(), nullable=False, server_default="0",
        comment="按规则执行成功次数（execute 时 +1）"))
    op.add_column("governance_rules", sa.Column(
        "effect_rejected", sa.Integer(), nullable=False, server_default="0",
        comment="人工驳回次数（规则命中但人工否定建议）"))
    op.add_column("governance_rules", sa.Column(
        "effect_fp", sa.Integer(), nullable=False, server_default="0",
        comment="误报次数（规则命中但确认业务允许/无问题）"))
    op.add_column("governance_rules", sa.Column(
        "last_effect_at", sa.DateTime(timezone=True), nullable=True,
        comment="最近一次效果事件时间"))

    # ── prompt_iterations 半自动执行留痕 ────────────────────────────
    op.create_table(
        'prompt_iterations',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('scope', sa.String(length=32), nullable=False,
                  comment='建议类型: template_keyword/template_question/new_template/rule_adjust/anchor_guide'),
        sa.Column('target_id', sa.Integer(), nullable=True, comment='目标 id（模板 id / 规则 id）'),
        sa.Column('target_name', sa.String(length=255), nullable=False, comment='目标快照名'),
        sa.Column('suggestion', sa.Text(), nullable=False, comment='AI 建议内容（新关键词/新问句/规则调整）'),
        sa.Column('reason', sa.String(length=255), nullable=True, comment='建议理由（如「近7天快照3份0命中」）'),
        sa.Column('old_value', sa.Text(), nullable=True, comment='旧值（应用时对比）'),
        sa.Column('new_value', sa.Text(), nullable=True, comment='新值'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='proposed',
                  comment='状态机: proposed(评估产出)/confirmed(人工确认)/applied(已应用)/rejected(不采纳)'),
        sa.Column('result', sa.String(length=64), nullable=True,
                  comment='应用结果（success/skipped/error + 摘要）'),
        sa.Column('created_by', sa.Integer(), nullable=True, comment='建议产出人（评估触发者）'),
        sa.Column('confirmed_by', sa.Integer(), nullable=True, comment='确认人'),
        sa.Column('applied_by', sa.Integer(), nullable=True, comment='应用人'),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True, comment='确认时刻'),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True, comment='应用时刻'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()'), comment='建议产出时刻'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()'), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('ix_prompt_iterations_status', 'status'),
        sa.Index('ix_prompt_iterations_scope', 'scope'),
    )


def downgrade() -> None:
    op.drop_table('prompt_iterations')
    op.drop_column("governance_rules", "last_effect_at")
    op.drop_column("governance_rules", "effect_fp")
    op.drop_column("governance_rules", "effect_rejected")
    op.drop_column("governance_rules", "effect_executed")
