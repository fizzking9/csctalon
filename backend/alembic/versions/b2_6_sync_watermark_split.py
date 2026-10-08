"""R-P1-16 (#241): 文档采集拆分「尝试心跳 last_attempt_at」与「成功水位 last_sync_at」

Revision ID: b2_6_sync_watermark_split
Revises: b2_5_long_report_tokens
Create Date: 2026-10-09 15:00:00.000000

背景:
- doc_sync_sources / doc_sync_configs 的 last_sync_at 历史上成败都刷新, 同时充当
  调度节流基准与看板成功水位 → 失败被当成功: 失败源静默等满整个 interval(默认 60min,
  > 高频槽距 30min)才重试, 看板水位也被失败污染(R-P1-16)。
- 本迁移新增 last_attempt_at(最近尝试, 无论成败); 应用层此后只在成功时刷 last_sync_at。

存量回填:
- last_attempt_at = last_sync_at(旧值本质是「最近一次记录时间」)。回填使加列后所有源
  不会因 attempt 为 NULL 而在部署瞬间全部到期、并发触发 playwright; 未跑过的行
  (last_sync_at IS NULL)仍保持 NULL → 首次恒到期。
- 无法从历史数据恢复 failed 行「真正的上次成功时间」, 不做猜测; 此后新失败不再污染水位。

downgrade: 纯新增列, 直接 DROP 即可安全回退(仅丢失新引入的尝试时间)。
"""
from typing import Sequence, Union

from sqlalchemy import Column, DateTime, text

from alembic import op

revision: str = 'b2_6_sync_watermark_split'
down_revision: Union[str, None] = 'b2_5_long_report_tokens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'doc_sync_sources',
        # 最近一次尝试时间(调度节流心跳, 无论成败)
        Column('last_attempt_at', DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'doc_sync_configs',
        Column('last_attempt_at', DateTime(timezone=True), nullable=True),
    )
    # 存量回填: 旧 last_sync_at 即最近记录时间; 仅回填非空行(空行保持 NULL=首次到期)
    op.execute(text(
        "UPDATE doc_sync_sources SET last_attempt_at = last_sync_at "
        "WHERE last_attempt_at IS NULL AND last_sync_at IS NOT NULL"
    ))
    op.execute(text(
        "UPDATE doc_sync_configs SET last_attempt_at = last_sync_at "
        "WHERE last_attempt_at IS NULL AND last_sync_at IS NOT NULL"
    ))


def downgrade() -> None:
    op.drop_column('doc_sync_configs', 'last_attempt_at')
    op.drop_column('doc_sync_sources', 'last_attempt_at')
