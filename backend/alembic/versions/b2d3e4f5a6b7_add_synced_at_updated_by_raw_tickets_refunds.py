"""add synced_at and updated_by_raw to service_tickets and refunds (P1-T4)

2026-08-06 新口径,拆分「记录导入时间」与「上游真实更新时间/更新人」,
解决原有 updated_at 与上游批量覆盖冲突、finish_time 89% 不可靠导致的统计口径问题。

两表各加 2 列:
  - synced_at        TIMESTAMPTZ  最后一次被导入/同步任务写入时间(每次 upsert 强制 now()),用于区分「上游真改了」vs「我们扫表覆盖」
  - updated_by_raw   VARCHAR(64)  上游(BMS/SCRM/导出)原始更新人/最后操作人,原样保留,不参与业务逻辑

Revision ID: b2d3e4f5a6b7
Revises: a1b2c3d4e5f7
Create Date: 2026-08-06 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2d3e4f5a6b7'
down_revision: Union[str, None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) service_tickets 加两列
    op.add_column(
        'service_tickets',
        sa.Column(
            'synced_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='最后一次被导入/同步任务写入时间(每次 upsert 强制 now())',
        ),
    )
    op.create_index(
        'ix_service_tickets_synced_at',
        'service_tickets',
        ['synced_at'],
        unique=False,
    )
    op.add_column(
        'service_tickets',
        sa.Column(
            'updated_by_raw',
            sa.String(length=64),
            nullable=True,
            comment='上游(BMS/SCRM)原始更新人/最后操作人,原样导入不参与业务逻辑',
        ),
    )
    op.create_index(
        'ix_service_tickets_updated_by_raw',
        'service_tickets',
        ['updated_by_raw'],
        unique=False,
    )

    # 2) refunds 加两列
    op.add_column(
        'refunds',
        sa.Column(
            'synced_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='最后一次被导入/同步任务写入时间(每次 upsert 强制 now())',
        ),
    )
    op.create_index(
        'ix_refunds_synced_at',
        'refunds',
        ['synced_at'],
        unique=False,
    )
    op.add_column(
        'refunds',
        sa.Column(
            'updated_by_raw',
            sa.String(length=64),
            nullable=True,
            comment='上游(BMS/SCRM)原始更新人/最后操作人,原样导入不参与业务逻辑',
        ),
    )
    op.create_index(
        'ix_refunds_updated_by_raw',
        'refunds',
        ['updated_by_raw'],
        unique=False,
    )


def downgrade() -> None:
    # refunds 先删
    op.drop_index('ix_refunds_updated_by_raw', table_name='refunds')
    op.drop_column('refunds', 'updated_by_raw')
    op.drop_index('ix_refunds_synced_at', table_name='refunds')
    op.drop_column('refunds', 'synced_at')

    # service_tickets 后删
    op.drop_index('ix_service_tickets_updated_by_raw', table_name='service_tickets')
    op.drop_column('service_tickets', 'updated_by_raw')
    op.drop_index('ix_service_tickets_synced_at', table_name='service_tickets')
    op.drop_column('service_tickets', 'synced_at')
