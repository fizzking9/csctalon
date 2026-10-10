"""today 主题域包 (拆分自原 today.py, 工单 #226)。

原 today.py(3574 行)按主题拆分为 6 个子模块:
- _kpi.py:       today_kpi
- _trend.py:     today_hourly_trend
- _reception.py: today_reception_breakdown
- _refund.py:    _ensure_service_tickets_for_unmatched_refunds + today_refund_breakdown
- _queue.py:     _recurring_matches_today + today_queue
- _staff.py:     today_staff_efficiency

本 __init__.py 是薄 facade, re-export 全部 8 个顶层名字, 保证
analytics/__init__.py 第 193-202 行 `from app.services.analytics.today import ...`
零改动可用。新功能请写入对应主题子模块, 勿在本层堆积逻辑。
"""
# ruff: noqa: F401, I001  # 拆分自原 today.py(#226), 子模块按主题排列
from app.services.analytics.today._kpi import today_kpi
from app.services.analytics.today._trend import today_hourly_trend
from app.services.analytics.today._reception import today_reception_breakdown
from app.services.analytics.today._refund import (
    _ensure_service_tickets_for_unmatched_refunds,
    today_refund_breakdown,
)
from app.services.analytics.today._queue import (
    _recurring_matches_today,
    today_queue,
)
from app.services.analytics.today._staff import today_staff_efficiency

__all__ = [
    "today_kpi",
    "today_hourly_trend",
    "today_reception_breakdown",
    "today_refund_breakdown",
    "today_queue",
    "today_staff_efficiency",
    "_ensure_service_tickets_for_unmatched_refunds",
    "_recurring_matches_today",
]
