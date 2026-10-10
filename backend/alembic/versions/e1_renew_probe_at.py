"""add devices.renew_probe_at

Revision ID: e1_renew_probe_at
Revises: d3_profile_probe_at
Create Date: 2026-10-10

加 devices.renew_probe_at 字段(续费记录探针时间, #293)。

背景: 续费记录套餐兜底(mode="renew")与设备列表探针共用 profile_probe_at,
两类问题:
1. renew 扫描无法区分「已被续费探过(含miss)」与「仅设备列表探过」——
   miss 设备刷新 profile_probe_at 后在排序中形成「miss墙」, 挡住后续
   未扫描设备(2026-10-10 实测: 首批零填充即误判收敛, 94,570 台漏扫);
2. renew 写 profile_probe_at 会把设备推出设备列表 30 天扫描窗口, 两个
   探针语义被耦合。

修复: 独立 renew_probe_at; renew 扫描只取「未续费探过 OR 超30天」的
缺套餐设备, miss 打 renew 标记后不再挡道, target=0 才是真收敛。
"""

import sqlalchemy as sa

from alembic import op

revision = "e1_renew_probe_at"
down_revision = "d3_profile_probe_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "renew_probe_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="续费记录探针时间(#293): 套餐兜底查过的时刻, 含miss; renew扫描只看未探或超30天",
        ),
    )


def downgrade() -> None:
    op.drop_column("devices", "renew_probe_at")
