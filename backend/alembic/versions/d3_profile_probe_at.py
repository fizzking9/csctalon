"""add devices.profile_probe_at

Revision ID: d3_profile_probe_at
Revises: c2_drop_unused_idx_286
Create Date: 2026-10-10

加 devices.profile_probe_at 字段(档案探针时间, #293)。

背景: 设备档案 SN 补全(device_profile_enrich)以「缺字段」为扫描口径,
但两类设备永远无法出队——① BMS 查无此 SN 的 miss(短SN脏数据/已删设备,
约占 45%); ② hit 但无套餐等设备(current_combo_name 在 BMS 本就为空)。
miss 路径原不写任何标记 → 永久 miss 在每批堆积, 3~4 批后扫描配额被全部占满,
回填实际停滞(2026-10-10 生产实测: 批1 hit 11018 但仅 1073 台退出缺失集)。

修复: hit/miss 都写 profile_probe_at=now() 探针时间; 扫描只看
「未探针 OR 探针超过 30 天」(30 天复探窗口给后续新买套餐设备留刷新口)。
"""

import sqlalchemy as sa

from alembic import op

revision = "d3_profile_probe_at"
down_revision = "c2_drop_unused_idx_286"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "profile_probe_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="档案探针时间(#293): SN 补全探过的时刻, 含 miss; 扫描只看未探针或超30天",
        ),
    )
    # 存量已被探过的设备(active_flag 非空 = 日批导出/补全已写入过 BMS 数据)直接打标,
    # 避免上线后对 23 万已探设备重扫一遍; 日批导出负责其后续字段刷新
    op.execute("UPDATE devices SET profile_probe_at = now() WHERE active_flag IS NOT NULL AND active_flag != ''")


def downgrade() -> None:
    op.drop_column("devices", "profile_probe_at")
