"""提示词版本补充变更依据字段 change_reason/references (工单 #178)

为 prompt_configs 增加「为什么改/解决什么问题」(change_reason) 与
「参考依据来源」(references) 两个字段，使版本账本可溯（评估中心建议/
用户评价/意见工单/文档链接等来源），解决人工意见与版本管理做闭环时
缺少「依据」落点的问题。

幂等：列已存在（本地 create_all 环境可能已建）则跳过，不报错；
适配 Alembic 与 Base.metadata.create_all 并存的升级规范。

Revision ID: e6f7a8b9c0d2
Revises: d4e5f6a7b8c0
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'e6f7a8b9c0d2'
down_revision: Union[str, None] = 'd4e5f6a7b8c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    for col, comment in (
        ("change_reason", "本版迭代依据：解决什么问题/为何改（新建版本时人工填写）"),
        ("references", "本版参考依据：评估中心建议/用户评价/意见工单/文档链接等来源"),
    ):
        if not _has_column("prompt_configs", col):
            op.add_column(
                "prompt_configs",
                sa.Column(col, sa.Text(), nullable=True, comment=comment),
            )


def downgrade() -> None:
    for col in ("references", "change_reason"):
        if _has_column("prompt_configs", col):
            op.drop_column("prompt_configs", col)