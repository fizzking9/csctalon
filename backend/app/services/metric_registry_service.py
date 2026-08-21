"""运营指标注册表服务 - 指标求值/四级分级/每日快照

指标口径必须与看板同源同口径: 优先复用 services/analytics.py 与 complaints 的既有计算,
预警监测与看板展示的是同一个数。

四级水位(方向由指标注册声明, 阈值表只存数值, 引擎按方向解释):
- higher_better(越高越好): value>=优秀线→excellent; value<告警线→critical; value<预警线→warning; 其余→green
- lower_better(越低越好): value<=优秀线→excellent; value>告警线→critical; value>预警线→warning; 其余→green
"""

import logging
from datetime import date as date_type, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.alert import AlertRule
from app.models.external_complaint import ExternalComplaint
from app.models.metric import MetricDailySnapshot, MetricRegistry, MetricThreshold
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.services.analytics import _complaint_source_type_case, _ticket_category_case, TZ, refund_recovery_analysis

logger = logging.getLogger(__name__)

# 已完成状态集合(积压口径, 与 complaints.py backlog 一致)
_DONE_STATUS_SET = {"已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止"}

# 指标方向常量
HIGHER = "higher_better"
LOWER = "lower_better"


def _window(period: str, d: date_type | None) -> tuple[datetime, datetime]:
    """按周期生成北京时间窗口 → naive UTC (与看板/投诉口径一致)"""
    target = d or datetime.now(TZ).date()
    if period == "week":
        start_d = target - timedelta(days=target.weekday())
    elif period == "month":
        start_d = target.replace(day=1)
    else:  # day
        start_d = target
    start = datetime.combine(start_d, time.min, tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)
    if period == "week":
        end = start + timedelta(days=7)
    elif period == "month":
        end = datetime.combine((start_d.replace(day=28) + timedelta(days=4)).replace(day=1), time.min, tzinfo=TZ) \
            .astimezone(timezone.utc).replace(tzinfo=None)
    else:
        end = start + timedelta(days=1)
    return start, end


# ---------- 指标计算函数(口径与看板一致) ----------


def _eval_recovery_rate(db: Session, ts: datetime, te: datetime) -> dict:
    """退款挽回率: 复用今日看板退款挽回专项口径(kpi.success_rate, 0~100)

    refund_recovery_analysis 的 start/end 为 'YYYY-MM-DD'(北京时间), 从窗口换算。
    """
    try:
        from datetime import timezone as _utc
        sh_start = ts.replace(tzinfo=_utc.utc).astimezone(TZ).strftime("%Y-%m-%d")
        sh_end = (te - timedelta(seconds=1)).replace(tzinfo=_utc.utc).astimezone(TZ).strftime("%Y-%m-%d")
        d = refund_recovery_analysis(db, start=sh_start, end=sh_end)
        kpi = d.get("kpi") or {}
        rate = kpi.get("success_rate")
        total_sn = kpi.get("total_sn") or 0
        # 无接待数据时 success_rate=0.0 是"数据缺失"而非业务恶化 → data_ok=False 防假告警
        return {"value": float(rate) if rate is not None else None, "data_ok": bool(total_sn > 0), "detail": f"接待SN {total_sn}"}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[metric] recovery_rate 求值异常: {e}")
        return {"value": None, "data_ok": False, "detail": f"求值异常: {e}"}


def _eval_refund_count(db: Session, ts: datetime, te: datetime) -> dict:
    """当日退款笔数: refund.created_at_raw 落在北京时间当日"""
    cnt = db.scalar(select(func.count()).select_from(Refund).where(
        Refund.created_at_raw >= ts, Refund.created_at_raw < te
    )) or 0
    return {"value": float(cnt), "data_ok": True, "detail": f"区间 {ts}~{te}"}


def _eval_ticket_backlog(db: Session, ts: datetime, te: datetime) -> dict:
    """投诉工单积压: 当前未完成投诉工单总数(不限日期, 与投诉专项积压同口径)"""
    stmt = select(func.count()).where(_ticket_category_case() == "投诉").where(
        or_(
            ServiceTicket.status.is_(None),
            ServiceTicket.status == "",
            ~ServiceTicket.status.in_(_DONE_STATUS_SET),
        )
    )
    cnt = db.scalar(stmt) or 0
    return {"value": float(cnt), "data_ok": True, "detail": "全量未完成投诉工单"}


def _eval_external_complaint_7d(db: Session, ts: datetime, te: datetime) -> dict:
    """近7天外部投诉量: external_complaints.complaint_date 近7天"""
    start7 = te - timedelta(days=7)
    cnt = db.scalar(select(func.count()).select_from(ExternalComplaint).where(
        ExternalComplaint.complaint_date >= start7, ExternalComplaint.complaint_date < te
    )) or 0
    return {"value": float(cnt), "data_ok": True, "detail": f"近7天 {start7.date()}~{te.date()}"}


def _eval_internal_complaint_daily(db: Session, ts: datetime, te: datetime) -> dict:
    """当日内部投诉量: 内部投诉类工单 register_time 当日"""
    stmt = (
        select(func.count())
        .where(_ticket_category_case() == "投诉")
        .where(_complaint_source_type_case() == "内部投诉")
        .where(ServiceTicket.register_time >= ts, ServiceTicket.register_time < te)
    )
    cnt = db.scalar(stmt) or 0
    return {"value": float(cnt), "data_ok": True, "detail": f"区间 {ts}~{te}"}


# 指标注册表(代码层): code → 元数据 + 求值函数
METRIC_DEFS: dict[str, dict[str, Any]] = {
    "recovery_rate": {
        "name_cn": "退款挽回率", "direction": HIGHER, "period": "day", "unit": "%",
        "data_sources": ["scrm_session", "cs_record", "refund"],
        "description": "当日退款挽回成功率(与今日看板退款挽回专项同口径)",
        "evaluate": _eval_recovery_rate,
    },
    "refund_count": {
        "name_cn": "当日退款笔数", "direction": LOWER, "period": "day", "unit": "笔",
        "data_sources": ["refund"], "description": "当日退款操作笔数(创建时间, 北京时间)",
        "evaluate": _eval_refund_count,
    },
    "ticket_backlog": {
        "name_cn": "投诉工单积压", "direction": LOWER, "period": "day", "unit": "个",
        "data_sources": ["service_tickets"], "description": "当前未完成投诉工单总数(含历史遗留)",
        "evaluate": _eval_ticket_backlog,
    },
    "external_complaint_7d": {
        "name_cn": "近7天外部投诉量", "direction": LOWER, "period": "day", "unit": "条",
        "data_sources": ["external_complaints"], "description": "近7天315/监督局外部投诉登记量",
        "evaluate": _eval_external_complaint_7d,
    },
    "internal_complaint_daily": {
        "name_cn": "当日内部投诉量", "direction": LOWER, "period": "day", "unit": "条",
        "data_sources": ["service_tickets"], "description": "当日内部投诉工单数(商品/服务投诉)",
        "evaluate": _eval_internal_complaint_daily,
    },
}


def get_threshold(db: Session, code: str) -> MetricThreshold | None:
    """当前生效阈值(valid_to IS NULL 的最新一条)"""
    return db.scalar(
        select(MetricThreshold)
        .where(MetricThreshold.metric_code == code, MetricThreshold.valid_to.is_(None))
        .order_by(MetricThreshold.id.desc())
        .limit(1)
    )


def classify(value: float | None, thr: MetricThreshold | None, direction: str) -> str | None:
    """四级分级: excellent/green/warning/critical; 无阈值或值缺失 → None(不参与)"""
    if value is None or thr is None:
        return None
    if direction == HIGHER:
        if thr.excellent_value is not None and value >= thr.excellent_value:
            return "excellent"
        if value < thr.critical_value:
            return "critical"
        if value < thr.warning_value:
            return "warning"
        return "green"
    # lower_better
    if thr.excellent_value is not None and value <= thr.excellent_value:
        return "excellent"
    if value > thr.critical_value:
        return "critical"
    if value > thr.warning_value:
        return "warning"
    return "green"


def evaluate_metric(db: Session, code: str, period_date: date_type | None = None) -> dict:
    """指标求值 + 分级(不写库)

    Returns: {metric_code, value, data_ok, level, ts, te, detail, threshold:{...}}
    """
    meta = METRIC_DEFS.get(code)
    if not meta:
        return {"metric_code": code, "value": None, "data_ok": False, "level": None, "detail": "未注册"}
    ts, te = _window(meta["period"], period_date)
    r = meta["evaluate"](db, ts, te)
    thr = get_threshold(db, code)
    level = classify(r.get("value"), thr, meta["direction"])
    thr_dict = None
    if thr:
        thr_dict = {
            "excellent_value": thr.excellent_value,
            "warning_value": thr.warning_value,
            "critical_value": thr.critical_value,
        }
    return {
        "metric_code": code,
        "value": r.get("value"),
        "data_ok": r.get("data_ok", True),
        "level": level,
        "ts": ts,
        "te": te,
        "detail": r.get("detail", ""),
        "threshold": thr_dict,
        "direction": meta["direction"],
        "period": meta["period"],
    }


def snapshot_metric(db: Session, code: str, period_date: date_type | None = None) -> dict:
    """求值并写每日快照(upsert: 同指标同日覆盖)"""
    r = evaluate_metric(db, code, period_date)
    if r.get("value") is None:
        r["level"] = "data_missing"
    row = db.scalar(
        select(MetricDailySnapshot).where(
            MetricDailySnapshot.metric_code == code,
            MetricDailySnapshot.period_date == (period_date or datetime.now(TZ).date()).isoformat(),
        )
    )
    if row:
        row.value = r.get("value")
        row.level = r.get("level")
        row.data_ok = r.get("data_ok", True)
        row.detail = r.get("detail")
    else:
        db.add(MetricDailySnapshot(
            metric_code=code,
            value=r.get("value"),
            period_date=(period_date or datetime.now(TZ).date()).isoformat(),
            level=r.get("level"),
            data_ok=r.get("data_ok", True),
            detail=r.get("detail"),
        ))
    db.flush()
    return r


def list_metrics_status(db: Session, period_date: date_type | None = None) -> list[dict]:
    """指标注册表 + 当前值/级别(供前端水位视图)"""
    out = []
    rows = db.scalars(select(MetricRegistry).order_by(MetricRegistry.id)).all()
    db_metas = {r.code: r for r in rows}
    for code, meta in METRIC_DEFS.items():
        db_meta = db_metas.get(code)
        if db_meta is not None and not db_meta.enabled:
            continue
        r = evaluate_metric(db, code, period_date)
        thr = get_threshold(db, code)
        out.append({
            "code": code,
            "name_cn": meta["name_cn"],
            "direction": meta["direction"],
            "period": meta["period"],
            "unit": meta["unit"],
            "description": meta["description"],
            "value": r.get("value"),
            "level": r.get("level"),
            "data_ok": r.get("data_ok", True),
            "threshold": r.get("threshold"),
            "threshold_id": thr.id if thr else None,
        })
    return out
