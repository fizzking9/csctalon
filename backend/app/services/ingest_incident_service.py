"""采集类 Incident 自动登记/平息 (工单 #203)

把 api/v1/alerts.py 的 _auto_register_ingest_incident / _close_ingest_incident
下沉到 service 层: alerts 路由(指标扫描 data_arrival 链路)与 bms_scheduler
(采集熔断链路, #203 接线)共用**同一套幂等键**, 保证"同源同业务日只登记一次"。

幂等约定(与既有 data_arrival 链路完全一致, 不得单独变更):
    category = "数据采集"
    created_by = "system-alert"
    detail  ilike %业务日%      (period_key, 如 2026-10-07)
    title   ilike %metric_code% (数据源名)

事务纪律: 本模块只 add/flush, **不 commit** —— 由调用方决定提交时机
(alerts 路径随请求事务提交; scheduler 路径独立 session 显式 commit)。
时区口径: 业务日一律按 Asia/Shanghai 换算(数据时间字段使用规范), 不得直接 strftime。
"""

import logging
from datetime import datetime
from datetime import timezone as _dt_timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

_TZ_CN = ZoneInfo("Asia/Shanghai")

# 幂等/归类常量(与 alerts.py 既有 Incident 约定对齐)
_INCIDENT_CATEGORY = "数据采集"
_INCIDENT_CREATOR = "system-alert"


def biz_day(dt: datetime | None) -> str | None:
    """把 naive/aware UTC 时间转成上海业务日 YYYY-MM-DD(与 alerts._period_biz_day 同款)。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt_timezone.utc)
    return dt.astimezone(_TZ_CN).strftime("%Y-%m-%d")


def register_ingest_incident(
    db,
    *,
    metric_code: str,
    period_key: str | None,
    detail: str,
    subject: str | None = None,
    occurred_at: datetime | None = None,
    metric_value: float | None = None,
    impact: str | None = None,
    now: datetime | None = None,
) -> int | None:
    """登记一条采集类 Incident(幂等: 同 metric_code + 同业务日已登记则跳过)。

    返回新 Incident id; 幂等跳过返回 None。
    severity: metric_value 为 None(无法量化/熔断级)或 < 40 → "高", 否则 "中"
    (沿袭 data_arrival 链路既有分级)。
    """
    from app.models.incident import Incident as IncidentModel

    try:
        if period_key:
            dup = db.scalar(
                select(func.count())
                .select_from(IncidentModel)
                .where(
                    IncidentModel.category == _INCIDENT_CATEGORY,
                    IncidentModel.created_by == _INCIDENT_CREATOR,
                    IncidentModel.detail.ilike(f"%{period_key}%"),
                    IncidentModel.title.ilike(f"%{metric_code}%"),
                )
            )
            if dup:
                return None  # 已登记, 幂等跳过
        i = IncidentModel(
            title=f"数据采集异常: {metric_code} {period_key or ''}",
            subject=subject or f"{metric_code} 采集异常",
            detail=detail,
            impact=impact or "该数据源当日数据缺口(影响接待量/退款挽回等分析口径)",
            occurred_at=occurred_at or now or datetime.now(_dt_timezone.utc),
            status="registered",
            category=_INCIDENT_CATEGORY,
            department="数据采集",
            severity="高" if (metric_value is None or (metric_value or 0) < 40) else "中",
            is_recurring=False,
            created_by=_INCIDENT_CREATOR,
        )
        db.add(i)
        db.flush()
        # 通知管理员/主管(事故登记知情; 仅 add/flush, 随调用方事务提交)
        try:
            from app.services.notifier import notify_all_admins_and_supervisors

            notify_all_admins_and_supervisors(
                db,
                type="incident",
                title=f"数据采集异常已登记: {metric_code}",
                content=i.detail or "",
                ref_type="incident",
                ref_id=str(i.id),
            )
        except Exception:  # noqa: BLE001 通知失败不影响登记
            logger.warning("[ingest_incident] 登记通知失败(忽略): %s", metric_code)
        return i.id
    except Exception as e:  # noqa: BLE001 登记失败不影响调用方主流程
        logger.warning("[ingest_incident] Incident 登记失败(忽略): %s(%s)", metric_code, e)
        return None


def close_ingest_incidents(
    db,
    *,
    metric_code: str,
    period_key: str | None,
    now: datetime,
    note: str,
) -> int:
    """平息同源同业务日的未闭环 Incident(恢复自愈), 返回平息条数。

    只处理 is_closed=False 的行(已人工闭环的不动); 平息记 review_note 留痕。
    """
    from app.models.incident import Incident as IncidentModel

    if not period_key:
        return 0
    try:
        rows = db.scalars(
            select(IncidentModel)
            .where(
                IncidentModel.category == _INCIDENT_CATEGORY,
                IncidentModel.created_by == _INCIDENT_CREATOR,
                IncidentModel.is_closed.is_(False),
                IncidentModel.detail.ilike(f"%{period_key}%"),
                IncidentModel.title.ilike(f"%{metric_code}%"),
            )
        ).all()
        for i in rows:
            i.status = "calmed"      # 已平息
            i.is_closed = True
            i.closed_at = now
            i.review_note = (f"{i.review_note} | " if i.review_note else "") + f"{note} {now.isoformat()}"
        if rows:
            db.flush()
        return len(rows)
    except Exception as e:  # noqa: BLE001 平息失败不影响调用方主流程
        logger.warning("[ingest_incident] Incident 平息失败(忽略): %s(%s)", metric_code, e)
        return 0
