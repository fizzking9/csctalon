"""#332 T5: dim_device_merchant 加仲裁标记列 arbiter_pick

背景(tasks.md T5 取舍决策): 同 SN 同时段多商户(overlap)时, 消费侧需一个
**可仲裁标记** 决定优先展示哪家。实现时二选一(维表加列 vs 消费侧 JOIN devices
现算)—— 选「维表加列」: 仲裁依据 devices.merchant_name 只在构建期稳定可得,
物化后消费侧 as-of JOIN 无需再回查设备档案, 口径集中在维表一处。

- arbiter_pick BOOLEAN NOT NULL DEFAULT FALSE: 仅对 conflict_flag='overlap'
  的分段可能有意义; TRUE 表示该分段所属商户被设备档案 merchant_name 仲裁选中
  (精确命中商户名优先, 其次命中集团名, 每 SN 至多 1 家)。

Revision ID: a332b05d6e7f
Revises: f332a01b2c3d
Create Date: 2026-10-16 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a332b05d6e7f"
down_revision: str = "f332a01b2c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dim_device_merchant",
        sa.Column(
            "arbiter_pick", sa.Boolean(), nullable=False, server_default=sa.text("false"),
            comment="仲裁选中: TRUE=同 SN overlap 分段中被设备档案 merchant_name 选中的商户分段",
        ),
    )


def downgrade() -> None:
    op.drop_column("dim_device_merchant", "arbiter_pick")
