"""add rejected/validation fields to sync logs (#42)

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-09-10 17:30:00.000000

背景(#42 abc-c, 2026-09-10):
  服务层已产出 `rejected`(未采纳行数)与 `validation`(入库前校验问题清单),
  但**同步日志表未持久化**这两类信息 → 采集日志/推送页只能看到「总行/新增/更新/失败」,
  「有多少行被拒收待人工判定」「有多少校验告警」在历史记录里查不到。

本次为两张同步日志表各补 3 列:
  rejected            Integer         未采纳已落拒收表的行数(待人工判定)
  validation_count    Integer         入库前校验问题条数(只读 advisory)
  validation_severity JSON            按处置级别聚合 {"block": n, "warn": m}

为什么单开 validation_count 而不复用 error 列:
  error 的语义是"同步失败原因"; 校验告警不是失败(推送可 success 且有告警),
  混入会让"错误"列在成功记录里显示文本(#42 复审 N-2 曾因此回退)。

⚠️ 纯加列(可空, 无默认), 对既有数据与接口**零影响**:
  存量行为 NULL; 列表接口统一以 `or 0` / `or {}` 兜底。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'e9f0a1b2c3d4'
down_revision: Union[str, None] = 'd8e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 表名 → 该表已有的列前缀(便于人读, 也用于注释)
_TABLES = ('complaint_sync_logs', 'manual_refund_sync_logs')

_COLUMNS = (
    ('rejected', sa.Integer(), '未采纳已落拒收表的行数(待人工判定)'),
    ('validation_count', sa.Integer(), '入库前校验问题条数(只读 advisory)'),
    ('validation_severity', sa.JSON(), '校验问题按处置级别聚合({"block": n, "warn": m})'),
)


def _column_exists(table_name: str, column_name: str) -> bool:
    """列存在性检查(幂等升级规范)

    已有表环境可能由 Base.metadata.create_all() 建表(含新列), 直接 add_column 会报
    DuplicateColumn → 必须先查 information_schema。
    限定 table_schema='public' 避免多 schema 同名表产生假阳性。
    """
    conn = op.get_bind()
    return bool(conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c)"
    ), {"t": table_name, "c": column_name}).scalar())


def upgrade() -> None:
    for table in _TABLES:
        for name, coltype, comment in _COLUMNS:
            if _column_exists(table, name):
                continue
            op.add_column(table, sa.Column(name, coltype, nullable=True, comment=comment))


def downgrade() -> None:
    for table in _TABLES:
        for name, _coltype, _comment in _COLUMNS:
            if _column_exists(table, name):
                op.drop_column(table, name)
