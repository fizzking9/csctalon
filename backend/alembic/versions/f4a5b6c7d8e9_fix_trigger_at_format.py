"""fix: weekly/monthly 模板 trigger_at 格式从 `1:09:00` 改为 `1-09:00` (工单 #126 P0-3)

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-09-16 23:30:00.000000

设计:
- 原 seed (#124) weekly/monthly trigger_at 存为 "1:09:00", 与 HH:MM 的 `:` 分隔符
  冲突, 视觉上像时长(1小时9分0秒)而非"周一9点"。
- 新格式:
  * daily   = "HH:MM"           (如 "09:00", 不变)
  * weekly  = "wday-HH:MM"      (如 "1-09:00" = 周一9点, wday=1..7 ISO)
  * monthly = "day-HH:MM"       (如 "1-09:00" = 每月1号9点, day=1..31)
  用 `-` 分隔前缀(wday/day)与 HH:MM, 与 daily 的纯 `HH:MM` 区分明显。
- 兼容性: schedule_due_templates 解析逻辑同时支持新旧格式(见 ai_report_worker.py),
  本 migration 修正已 seed 的数据; admin 手动新建的模板若用旧格式仍可工作。
- 幂等: 仅 UPDATE weekly/monthly 模板的 trigger_at 字段, 不动其他字段。
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # weekly 模板: "1:09:00" → "1-09:00" (wday-HH:MM)
    # 只替换 trigger_at 含两个 `:` 且 frequency=weekly 的行(避免误伤 daily)
    op.execute("""
        UPDATE ai_report_templates
        SET trigger_at = split_part(trigger_at, ':', 1) || '-' ||
                         split_part(trigger_at, ':', 2) || ':' ||
                         split_part(trigger_at, ':', 3)
        WHERE frequency = 'weekly'
          AND trigger_at ~ '^[0-9]+:[0-9]{2}:[0-9]{2}$'
    """)
    # monthly 模板: 同样规则
    op.execute("""
        UPDATE ai_report_templates
        SET trigger_at = split_part(trigger_at, ':', 1) || '-' ||
                         split_part(trigger_at, ':', 2) || ':' ||
                         split_part(trigger_at, ':', 3)
        WHERE frequency = 'monthly'
          AND trigger_at ~ '^[0-9]+:[0-9]{2}:[0-9]{2}$'
    """)


def downgrade() -> None:
    # 回滚: "1-09:00" → "1:09:00" (恢复旧格式)
    op.execute("""
        UPDATE ai_report_templates
        SET trigger_at = replace(trigger_at, '-', ':')
        WHERE frequency IN ('weekly', 'monthly')
          AND trigger_at ~ '^[0-9]+-[0-9]{2}:[0-9]{2}$'
    """)
