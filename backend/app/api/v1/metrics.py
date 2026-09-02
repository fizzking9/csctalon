"""运营指标路由 - 指标水位/四级级别/阈值参数配置(留痕)

指标口径与看板同源(metric_registry_service 复用 analytics 计算)。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.models.metric import MetricDailySnapshot, MetricThreshold
from app.schemas.common import R
from app.services.metric_registry_service import (
    METRIC_DEFS,
    evaluate_metric,
    get_threshold,
    list_metrics_status,
)

router = APIRouter(prefix="/metrics", tags=["运营指标"])


@router.get("", response_model=R, summary="指标注册表 + 当前值/级别(水位视图)")
def list_metrics(db: DBSession, _: CurrentUser):
    items = list_metrics_status(db)
    return R(data={"items": items, "total": len(items)})


@router.get("/{code}/current", response_model=R, summary="单指标当前值 + 级别 + 近30天快照")
def metric_current(code: str, db: DBSession, _: CurrentUser):
    if code not in METRIC_DEFS:
        raise HTTPException(status_code=404, detail="指标未注册")
    cur = evaluate_metric(db, code)
    thr = get_threshold(db, code)
    snapshots = db.scalars(
        select(MetricDailySnapshot)
        .where(MetricDailySnapshot.metric_code == code)
        .order_by(MetricDailySnapshot.period_date.desc())
        .limit(30)
    ).all()
    return R(data={
        "current": cur,
        "threshold": {
            "id": thr.id if thr else None,
            "excellent_value": thr.excellent_value if thr else None,
            "warning_value": thr.warning_value if thr else None,
            "critical_value": thr.critical_value if thr else None,
        },
        "snapshots": [
            {"period_date": s.period_date, "value": s.value, "level": s.level, "data_ok": s.data_ok}
            for s in snapshots
        ],
    })


@router.put("/{code}/thresholds", response_model=R, summary="配置指标阈值(留痕: 旧版本关闭 valid_to)")
def update_thresholds(code: str, payload: dict, db: DBSession, _: ManagerUser):
    if code not in METRIC_DEFS:
        raise HTTPException(status_code=404, detail="指标未注册")
    warning_value = payload.get("warning_value")
    critical_value = payload.get("critical_value")
    if warning_value is None or critical_value is None:
        raise HTTPException(status_code=400, detail="warning_value / critical_value 必填")
    now = datetime.now(timezone.utc)
    # 关闭当前生效版本(留痕)
    cur = get_threshold(db, code)
    if cur:
        cur.valid_to = now
        db.flush()
    thr = MetricThreshold(
        metric_code=code,
        excellent_value=payload.get("excellent_value"),
        warning_value=float(warning_value),
        critical_value=float(critical_value),
        valid_from=now,
        updated_by=getattr(_, "id", None),
    )
    db.add(thr)
    db.commit()
    return R(message="阈值已更新", data={"metric_code": code, "id": thr.id})


@router.get("/{code}/thresholds/history", response_model=R, summary="阈值历史(调参留痕)")
def threshold_history(code: str, db: DBSession, _: CurrentUser):
    rows = db.scalars(
        select(MetricThreshold)
        .where(MetricThreshold.metric_code == code)
        .order_by(MetricThreshold.id.desc())
        .limit(50)
    ).all()
    return R(data={"items": [
        {
            "id": t.id,
            "excellent_value": t.excellent_value,
            "warning_value": t.warning_value,
            "critical_value": t.critical_value,
            "valid_from": t.valid_from.isoformat() if t.valid_from else None,
            "valid_to": t.valid_to.isoformat() if t.valid_to else None,
        }
        for t in rows
    ]})
