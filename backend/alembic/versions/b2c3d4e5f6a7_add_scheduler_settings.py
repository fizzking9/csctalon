"""add scheduler_settings table (#51)

Revision ID: b2c3d4e5f6a7
Revises: f0a1b2c3d4e5
Create Date: 2026-09-10 20:10:00.000000

背景(#51, A-20260910-006 LOCKED v1.0):
  高频采集节拍原为代码常量 `bms_scheduler.DAYTIME_HOURS = {8..23, 0}`(每小时),
  管理员无法在界面调整。本表把「起止时段 + 间隔」DB 化, 支持页面自助修改且无需重启。

新增表 `scheduler_settings`(**单行表**, id 恒为 1):
  high_freq_start_hour       Integer  高频时段起(含), 默认 8
  high_freq_end_hour         Integer  高频时段止(不含), 默认 24 (表示至当日 24:00)
  high_freq_interval_minutes Integer  间隔(分钟), 默认 30
  updated_by / created_at / updated_at

默认值 = 目标态: 8:00~24:00 每 30 分钟(**32 槽/天**, 原 17 次/天)。

⚠️ 纯新增表, 对既有表/接口零影响。
⚠️ seed 幂等: 若 id=1 已存在则跳过(不覆盖用户改动, 遵循审计项 C-P2-2)。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'f0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = 'scheduler_settings'
_SINGLETON_ID = 1


def _table_exists(table_name: str) -> bool:
    """表存在性检查(幂等升级规范: create_all 环境可能已建表)。"""
    bind = op.get_bind()
    sql = sa.text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t"
    )
    return bind.execute(sql, {"t": table_name}).scalar() is not None


def upgrade() -> None:
    if not _table_exists(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('high_freq_start_hour', sa.Integer(), nullable=False,
                      server_default=sa.text('8'), comment='高频时段起(含), 北京时区小时 0~23'),
            sa.Column('high_freq_end_hour', sa.Integer(), nullable=False,
                      server_default=sa.text('24'), comment='高频时段止(不含), 1~24; 24 表示至当日 24:00'),
            sa.Column('high_freq_interval_minutes', sa.Integer(), nullable=False,
                      server_default=sa.text('30'), comment='采集间隔(分钟), 5~180'),
            sa.Column('updated_by', sa.String(length=64), nullable=True, comment='最后修改人账号'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            # DB 层最后防线(应用层已有 400 校验, 此处防手工改库写入非法值)
            sa.CheckConstraint(
                "high_freq_start_hour >= 0 AND high_freq_start_hour <= 23",
                name="ck_scheduler_settings_start_hour",
            ),
            sa.CheckConstraint(
                "high_freq_end_hour >= 1 AND high_freq_end_hour <= 24",
                name="ck_scheduler_settings_end_hour",
            ),
            sa.CheckConstraint(
                "high_freq_interval_minutes >= 5 AND high_freq_interval_minutes <= 180",
                name="ck_scheduler_settings_interval",
            ),
        )

    _seed_default_row()


def _seed_default_row() -> None:
    """幂等灌入默认行(id=1): 8:00~24:00 每 30 分钟(**已存在则跳过**)。"""
    bind = op.get_bind()
    exists = bind.execute(
        sa.text(f"SELECT 1 FROM {_TABLE} WHERE id = :i"), {"i": _SINGLETON_ID}
    ).scalar()
    if exists:
        return
    bind.execute(
        sa.text(
            f"INSERT INTO {_TABLE} "
            "(id, high_freq_start_hour, high_freq_end_hour, high_freq_interval_minutes, updated_by) "
            "VALUES (:i, 8, 24, 30, 'system')"
        ),
        {"i": _SINGLETON_ID},
    )


def downgrade() -> None:
    if _table_exists(_TABLE):
        op.drop_table(_TABLE)
