# -*- coding: utf-8 -*-
"""#294 采集健康告警 / 采集类事件的补数对账收口

为什么需要本模块(自愈缺陷):
- 预警扫描(alerts._scan_metric_rule)的"恢复自愈"只锚定**当前求值周期**的告警;
  跨天后老业务日的 pending 告警不再被重评, 补数发生在更晚日期时永远滞留。
- bms 熔断自愈(bms_scheduler.reset_halt)只认当前 biz_key, 且依赖进程内存
  _halted_types; 容器重启后标记丢失, 已落库 Incident 成为孤儿。
- 数据到达日志按"同步执行时间"落账, 而补数动作常发生在缺失日的次日,
  按日志窗口重评历史业务日无法识别"数据已补上"。

收口入口(均在补数成功后调用):
- 增量带窗口补跑(bms SyncTaskManager): period_keys 传补跑窗口覆盖的业务日,
  补了哪天就关哪天;
- 全量快照推送(shared_doc 文档同步): period_keys=None —— 全量快照成功的通道语义
  是"文档内全部历史数据此刻已在库", 故收口该源全部未恢复告警。

边界:
- 只操作系统自动登记的数据采集行(Alert.metric_code=data_arrival_* 未恢复;
  Incident category='数据采集' 未闭环), 人工确认/闭环的行一律不动;
- 本模块只 add/flush, **不 commit**, 由调用方决定提交时机;
- Incident 用完整标题精确匹配(exact_title), 避免 refund 误关联
  shanmao_refund/refund_record_report 等子串源。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.services.ingest_incident_service import (
    biz_day,
    close_ingest_incidents,
)

logger = logging.getLogger(__name__)

_PREFIX = "data_arrival_"


def recover_arrival_alerts(
    db,
    *,
    data_types: list[str],
    period_keys: "set[str] | list[str] | None" = None,
    now: "datetime | None" = None,
    note: str,
) -> dict:
    """把指定数据源未恢复的 data_arrival 告警收口, 并平息同源 Incident。

    Args:
        data_types: 数据源 code 列表(不含 data_arrival_ 前缀),
                    如 ['external_complaint', 'card_complaint']。
        period_keys: 仅收口这些上海业务日(YYYY-MM-DD);
                     None = 该源全部历史业务日(全量快照通道语义)。
        now: 收口时间(UTC), 默认当前。
        note: 写入 Incident.review_note 的留痕说明。

    Returns:
        {"recovered_alerts": int, "closed_incidents": int}
    """
    from app.models.alert import Alert

    if not data_types:
        return {"recovered_alerts": 0, "closed_incidents": 0}

    metric_codes = [f"{_PREFIX}{dt}" for dt in data_types]
    allowed_periods = set(period_keys) if period_keys is not None else None
    now = now or datetime.now(timezone.utc)

    rows = db.scalars(
        select(Alert).where(
            Alert.metric_code.in_(metric_codes),
            Alert.recovered_at.is_(None),
            # confirmed=人工已认领但未闭环, 补数到达同样收口(留痕可追溯)
            Alert.status.in_(("pending", "confirmed")),
        )
    ).all()

    recovered_alerts = 0
    closed_incidents = 0
    for alert in rows:
        day = biz_day(alert.period_start)
        if not day:
            continue
        if allowed_periods is not None and day not in allowed_periods:
            continue
        alert.status = "recovered"
        alert.recovered_at = now
        recovered_alerts += 1
        # 同一业务日尝试两条登记键: 告警链(data_arrival_<dt>)与熔断链(<dt>)
        bare_code = alert.metric_code[len(_PREFIX):] if alert.metric_code.startswith(_PREFIX) else alert.metric_code
        for code in (alert.metric_code, bare_code):
            closed_incidents += close_ingest_incidents(
                db,
                metric_code=code,
                period_key=day,
                now=now,
                note=note,
                exact_title=True,
            )

    if recovered_alerts:
        db.flush()
        logger.info(
            "[ingest_reconcile] 收口采集告警 %d 条, 平息事件 %d 条 (sources=%s, periods=%s)",
            recovered_alerts, closed_incidents, ",".join(data_types),
            "全部(快照)" if allowed_periods is None else ",".join(sorted(allowed_periods)),
        )
    return {"recovered_alerts": recovered_alerts, "closed_incidents": closed_incidents}
