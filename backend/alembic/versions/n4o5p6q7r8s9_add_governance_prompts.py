"""governance_prompts 研判提示词配置表 (工单 #164)

设计: docs/design/AI数据治理框架-20260919.md §5.3（提示词要点，存 DB 可迭代）

研判层（AI 治理分析师）提示词落库：
- 改判据不需要发版，直接改 DB 即生效；code 唯一标识一份提示词，version 留痕便于回滚；
- 规范原文段不落库（由 `governance_judge_service.build_spec_rules` 按常量生成注入），
  本表只存提示词骨架与铁律 —— 避免出现第二份 META 规范定义（A-20260910-004 漂移教训）。

本迁移建表 + 幂等 seed v1 两条提示词（重复 upgrade 不报错，不覆盖 admin 修改）。

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op

revision: str = 'n4o5p6q7r8s9'
down_revision: Union[str, None] = 'm3n4o5p6q7r8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 研判提示词 v1（与 governance_judge_service 的模块级默认提示词镜像）。
# 用 Python 参数化插入替代手写 SQL：内容含换行与中文引号，原样 SQL 字面量拼接易错。
_JUDGE_SYSTEM_V1 = (
    "你是「数据治理分析师」。系统会给你一批由**确定性探测器**算出的「结构性可疑」"
    "（findings：含表名、目标列/索引、量化证据 metrics、脱敏样本行、复现 SQL），"
    "请逐条判断「这是真的数据问题，还是业务允许的正常现象」，并给出治理建议。\n"
    "\n【项目规范原文（判据必须引用）】\n{spec_rules}\n"
    "\n【输出 JSON 结构】\n{output_schema}\n"
    "\n【铁律】\n"
    "1. 先分「业务允许 vs 数据错误」：PostgreSQL 普通唯一索引按 = 比较，NULL≠NULL —— "
    "可空列上出现多个 NULL **通常是业务允许**（如实时问答没有 task_id、草稿未分配 code），"
    "不要把「多个 NULL」当重复；只有组内**非 NULL 列**已足以判定同一实体、索引却拦不住时，"
    "才是真缺口（保护缺口）。\n"
    "2. 不可逆动作（删行 / 改值 / 建索引 / 改表结构）必须 requires_human_confirm=true（红线，AI 永不自决）。\n"
    "3. 判据必须引用规范条款或表说明；禁止编造业务结论（如「这是对账基准」）。\n"
    "4. 拿不准给 uncertain 且 confidence<0.7，不要硬判。\n"
    "5. 每条 finding 必须给出且只给出一条 judgment，finding_id 原样回填，禁止新增、合并或遗漏。"
)
_JUDGE_USER_V1 = (
    "【待研判的结构性可疑（共 {count} 条）】\n{findings}\n"
    "\n【表说明（data_sources.description，判定「业务允许与否」的依据）】\n{table_notes}\n"
    "\n请按系统提示词要求，只输出 JSON，禁止任何解释文字或 markdown 代码块标记。"
)


def upgrade() -> None:
    op.create_table(
        "governance_prompts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name_cn", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_governance_prompts_code"),
    )
    op.create_index("ix_governance_prompts_code", "governance_prompts", ["code"], unique=True)

    # 幂等 seed v1（ON CONFLICT DO NOTHING: 重复 upgrade 不报错, 不覆盖 admin 后续调整）
    governance_prompts = sa.table(
        "governance_prompts",
        sa.column("code", sa.String),
        sa.column("name_cn", sa.String),
        sa.column("content", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )
    op.execute(
        insert(governance_prompts)
        .values([
            {"code": "judge_system", "name_cn": "研判·系统提示",
             "content": _JUDGE_SYSTEM_V1, "version": 1, "is_active": True},
            {"code": "judge_user", "name_cn": "研判·用户提示",
             "content": _JUDGE_USER_V1, "version": 1, "is_active": True},
        ])
        .on_conflict_do_nothing(index_elements=["code"])
    )


def downgrade() -> None:
    op.drop_index("ix_governance_prompts_code", table_name="governance_prompts")
    op.drop_table("governance_prompts")
