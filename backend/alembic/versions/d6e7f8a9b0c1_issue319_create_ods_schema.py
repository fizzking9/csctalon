"""#319 数据分层 M1: 创建 ods schema(信源表层)

背景: 系统 86 张表混居 public schema, 其中 21 张是 BMS/SCRM/企微等上游信源的
浅层拷贝(ods 性质), 与系统业务表(强逻辑/强约束)混杂, 边界不清。
本次分层(M1 建 schema → M2 移表 → M3 autogen 适配)把信源表归入 ods schema:
  - public = 系统业务库(宁缺毋滥, 强约束)
  - ods    = 信源直连层(浅层拷贝, 原始留痕)

裸 SQL 兼容: 不逐处改写 300+ 处历史裸 SQL, 由 database.py 连接级
`SET search_path TO public, ods` 兜底(见 app/core/database.py 注释)。

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-10-13 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e7f8a9b0c1"
down_revision: str = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ods")


def downgrade() -> None:
    # 仅当 ods 已空(M2 已回退)才允许删 schema, 防止连带丢表
    op.execute("DROP SCHEMA IF EXISTS ods")
