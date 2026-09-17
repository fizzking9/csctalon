"""feat: weekly/monthly 模板 trigger_at 调到凌晨 02:00 (工单 #133 批次 A2)

Revision ID: f8g9h0i1j2k3
Revises: e2f3a4b5c6d7
Create Date: 2026-09-17 22:30:00.000000

设计:
- 用户原话: "周报/月报这份报告只需要每天 0 点~8 点期间生成一份就可以全部命中,
  不需要当天每个小时都去做计算"。将 weekly/monthly 模板的 trigger_at 从白天 09:00
  调到凌晨 02:00, 避开 0 点数据同步高峰, 在安静时段完成预生成, 让早 8 点的查询
  全部命中预生成草稿。
- 仅调 weekly/monthly 两个模板; daily 模板保持 trigger_at=09:00, 因 daily 走
  refresh_on_sync 白天实时刷新, 凌晨不需要触发(trigger_at=09:00 仅作兜底)。
- 配合 #133 对 ai_report_worker.warm_idle 的修改: 移除白天 8-23 时段限制,
  让凌晨 trigger_at 能被扫描到。
- wday=1(周一)对应 weekly_compare_last: 每周一凌晨 2 点生成上周对比报告
  (compute_pregen_window 在今日是周一时会自动退化为取上周整天, 与本调度配合)
- day=1(每月 1 号)对应 monthly_overview: 每月 1 号凌晨 2 点生成上月整月总览
  (compute_pregen_window 在今日是 1 号时会自动退化为取上月整天)
"""
from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = 'f8g9h0i1j2k3'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    weekly_compare_last: trigger_at 1-09:00 → 1-02:00(每周一凌晨 2 点)
    monthly_overview:   trigger_at 1-09:00 → 1-02:00(每月 1 号凌晨 2 点)
    daily_general_morning: 保持 09:00(走 refresh_on_sync 白天实时刷新)
    """
    op.execute(
        text(
            "UPDATE ai_report_templates "
            "SET trigger_at = '1-02:00', updated_at = NOW() "
            "WHERE code IN ('weekly_compare_last', 'monthly_overview')"
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            "UPDATE ai_report_templates "
            "SET trigger_at = '1-09:00', updated_at = NOW() "
            "WHERE code IN ('weekly_compare_last', 'monthly_overview')"
        )
    )
