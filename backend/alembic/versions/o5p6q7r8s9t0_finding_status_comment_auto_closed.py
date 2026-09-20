"""governance_findings.status 注释补 auto_closed (工单 #167)

重扫收敛（#167）给 finding 生命周期加了第 5 个状态 `auto_closed`
（重扫不再命中 → 自动收敛，**未执行任何治理动作**，区别于 resolved=已执行治理动作）。
状态值是 String(16) 无 CHECK 约束，**无数据变更**；本迁移只同步列注释，
避免库内 schema 自述与实际值域漂移。

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-09-20
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'o5p6q7r8s9t0'
down_revision: Union[str, None] = 'n4o5p6q7r8s9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COMMENT = ("生命周期: open(待研判)/judged(已研判)/resolved(已治理)/"
                "ignored(裁定非问题)/auto_closed(重扫不再命中, 自动收敛)")
_OLD_COMMENT = "生命周期: open(待研判)/judged(已研判)/resolved(已治理)/ignored(裁定非问题)"


def upgrade() -> None:
    op.alter_column(
        "governance_findings", "status",
        existing_type=sa.String(length=16), existing_nullable=False,
        comment=_NEW_COMMENT,
    )


def downgrade() -> None:
    op.alter_column(
        "governance_findings", "status",
        existing_type=sa.String(length=16), existing_nullable=False,
        comment=_OLD_COMMENT,
    )
