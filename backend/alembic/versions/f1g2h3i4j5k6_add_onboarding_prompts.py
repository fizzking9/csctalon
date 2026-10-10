"""onboarding_prompts 起草提示词配置表 (工单 #156)

AI 数据接入助手「AI 起草层」提示词落库（#153 反馈闭环思路）：
- 修改起草提示词不需要发版，直接改 DB 即生效；
- code 唯一标识一份提示词（draft_system / draft_user），version 留痕便于追溯回滚；
- 规范原文段不落库（由代码按常量生成注入），本表只存提示词骨架与铁律。

本迁移建表 + 幂等 seed v1 两条提示词（重复 upgrade 不报错，不覆盖 admin 修改）。

Revision ID: f1g2h3i4j5k6
Revises: c6d7e8f9a0b1
Create Date: 2026-09-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op

revision: str = 'f1g2h3i4j5k6'
down_revision: Union[str, None] = 'c6d7e8f9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 起草提示词 v1 内容（含 {spec_rules}/{output_schema} 等占位符, 由起草服务格式化注入）。
# 用 Python 参数化插入替代手写 SQL: 内容含换行与中文引号, 原样 SQL 字面量拼接易错。
_DRAFT_SYSTEM_V1 = (
    "你是「数据源接入助手」。用户会给你一份业务数据样例（已脱敏，含表头与若干行数据）"
    "与业务提示，请把它翻译成符合本项目规范的「数据源接入声明」。\n"
    "\n【项目规范原文（必须逐条遵守）】\n{spec_rules}\n"
    "\n【输出 JSON 结构】\n{output_schema}\n"
    "\n【铁律】\n"
    "1. 只做语义推断，不做业务断言（禁止编造「这是对账基准」之类结论）。\n"
    "2. 金额列一律判 semantic_role 为 amount 且 unit 为「分」；拿不准时 confidence 给小于 0.7 而非硬猜。\n"
    "3. code 用英文蛇形命名（如 bms_order）；name_cn 用中文业务词且必须带域前缀（前缀·中文名）。\n"
    "4. 时间列判 semantic_role 为 time 并给出 time_zone_note（默认 Asia/Shanghai）；SN 列判 sn。\n"
    "5. dedup_recommendation 必须给出依据并置 requires_human_confirm 为 true（去重键是红线，AI 永不自决）。\n"
    "6. 拿不准语义的列进 unknown_fields，不进 field_specs 硬猜。"
)
_DRAFT_USER_V1 = (
    "【业务提示】\n"
    "- 表名/来源提示：{table_hint}\n"
    "- 业务背景：{business_note}\n"
    "\n【样例数据（已脱敏，共 {row_count} 行）】\n{samples}\n"
    "\n请按系统提示词要求，只输出 JSON 接入声明，禁止任何解释文字或 markdown 代码块标记。"
)


def upgrade() -> None:
    op.create_table(
        "onboarding_prompts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name_cn", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_onboarding_prompts_code"),
    )
    op.create_index("ix_onboarding_prompts_code", "onboarding_prompts", ["code"], unique=True)

    # 幂等 seed v1（ON CONFLICT DO NOTHING: 重复 upgrade 不报错, 不覆盖 admin 后续调整）
    onboarding_prompts = sa.table(
        "onboarding_prompts",
        sa.column("code", sa.String),
        sa.column("name_cn", sa.String),
        sa.column("content", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )
    op.execute(
        insert(onboarding_prompts)
        .values(
            [
                {
                    "code": "draft_system",
                    "name_cn": "起草·系统提示（角色+铁律+输出结构）",
                    "content": _DRAFT_SYSTEM_V1,
                    "version": 1,
                    "is_active": True,
                },
                {
                    "code": "draft_user",
                    "name_cn": "起草·用户提示（样例注入）",
                    "content": _DRAFT_USER_V1,
                    "version": 1,
                    "is_active": True,
                },
            ]
        )
        .on_conflict_do_nothing(index_elements=["code"])
    )


def downgrade() -> None:
    op.drop_index("ix_onboarding_prompts_code", table_name="onboarding_prompts")
    op.drop_table("onboarding_prompts")
