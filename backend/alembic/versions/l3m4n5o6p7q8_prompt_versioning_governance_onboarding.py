"""governance_prompts/onboarding_prompts 升级多版本行模式 (提示词全量版本化)

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6p7
Create Date: 2026-09-23 22:30:00.000000

背景(用户 2026-09-23 要求「提示词所有更新版本化管理, 附注解决什么问题/影响什么」):
- prompt_configs(#178 生成/摘取侧) 已是多版本行+change_reason+激活留痕, 达标。
- governance_prompts(#164 研判侧) / onboarding_prompts(#156 起草侧) 仍是单行 UPDATE 覆盖,
  无 change_reason, 旧版本丢失不可回滚; judge prompt v1→v2→v3 迁移直接覆盖单行。
- 本迁移把两张表升级到与 prompt_configs 同模式: 多版本行 + is_active 部分唯一索引,
  新增 change_reason/references/activated_at/activated_by 字段。

升级后行为零回归: 现有 v1 单行保留为 is_active=true/version=1, load 逻辑(WHERE code AND
is_active=true)不变, 仍取到该行。今后迭代走「INSERT 新版本行+激活」(不覆盖旧行, 可回滚)。

历史 v1/v2 旧版本行已丢失, 不追溯补; 只保证今后可回滚。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'l3m4n5o6p7q8'
down_revision: Union[str, None] = 'k2l3m4n5o6p7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("governance_prompts", "onboarding_prompts")


def upgrade() -> None:
    for tbl in _TABLES:
        # 1. 加字段: change_reason / references / activated_at / activated_by
        op.add_column(tbl, sa.Column("change_reason", sa.Text(), nullable=True))
        op.add_column(tbl, sa.Column("references", sa.Text(), nullable=True))
        op.add_column(tbl, sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(tbl, sa.Column("activated_by", sa.BigInteger(), nullable=True))

        # 2. 删旧 code UNIQUE 约束 + unique index(建表迁移 n4o5/f1g2 各建了一个)
        #    用 IF EXISTS 防环境差异(约束名/索引名不一致时不卡住, 只跳过)
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS uq_{tbl}_code")
        op.execute(f"DROP INDEX IF EXISTS ix_{tbl}_code")

        # 3. 重建 code 为普通(非唯一)索引 —— 一个 code 现在允许多版本行
        op.create_index(f"ix_{tbl}_code", tbl, ["code"], unique=False)

        # 4. 建部分唯一索引: 同一 code 至多一个 is_active=true 的生效版本
        op.create_index(
            f"uq_{tbl}_active_code", tbl, ["code"],
            unique=True, postgresql_where=sa.text("is_active"),
        )


def downgrade() -> None:
    for tbl in _TABLES:
        # 撤销部分唯一索引 + 非唯一 code 索引, 恢复 unique
        op.drop_index(f"uq_{tbl}_active_code", table_name=tbl)
        op.drop_index(f"ix_{tbl}_code", table_name=tbl)
        op.create_index(f"ix_{tbl}_code", tbl, ["code"], unique=True)
        op.create_unique_constraint(f"uq_{tbl}_code", tbl, ["code"])
        # 删字段
        for col in ("activated_by", "activated_at", "references", "change_reason"):
            op.drop_column(tbl, col)
