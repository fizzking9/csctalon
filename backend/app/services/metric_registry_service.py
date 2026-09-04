"""运营指标注册表服务 - 指标求值/四级分级/每日快照

指标口径必须与看板同源同口径: 优先复用 services/analytics.py 与 complaints 的既有计算,
预警监测与看板展示的是同一个数。

四级水位(方向由指标注册声明, 阈值表只存数值, 引擎按方向解释):
- higher_better(越高越好): value>=优秀线→excellent; value<告警线→critical; value<预警线→warning; 其余→green
- lower_better(越低越好): value<=优秀线→excellent; value>告警线→critical; value>预警线→warning; 其余→green
"""

import logging
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.bms_sync_log import BmsSyncLog
from app.models.cs_record import CsRecord
from app.models.external_complaint import ExternalComplaint
from app.models.metric import MetricDailySnapshot, MetricRegistry, MetricThreshold
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.services.analytics import (
    TZ,
    _complaint_source_type_case,
    _ticket_category_case,
    refund_recovery_analysis,
)

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


# ============================================================
# 采集健康指标(工单 #5 / A-20260904-001 LOCKED)
# 与业务指标不同: 入表量骤降/断流 = 要告警的故障信号, 不能被 data_ok 吞掉
# data_ok 三态: True=正常 / "no_sync"=当日无采集 / "sync_failed"=当日采集失败
# ============================================================
# 北京时间当日 08:00~次日 02:00 为计划采集小时(8-23+0); 5:00 补跑; 见 bms_scheduler
_PLANNED_HOUR_START = 8   # 白天首采 08:00
_PLANNED_HOUR_END = 26    # 次日 02:00(即 hour 0~2 归前一天, 用 24+hour 表达)
# no_sync 首采保护: 08:00 定时同步触发后需数分钟完成(浏览器启动+登录+抓取+导入),
# 扫描若在 8 点整先跑会误判"无采集" → no_sync 误报。此时间前不判 no_sync。
_NO_SYNC_GRACE_HOUR = 9   # 北京 09:00 前不做 no_sync 断流判定


def _sh_now() -> datetime:
    """当前北京时间(naive)"""
    return datetime.now(TZ).replace(tzinfo=None)


def _day_bounds_utc(d: date_type) -> tuple[datetime, datetime]:
    """北京时间某日 [00:00, 次日00:00) → naive UTC(与 _window 同口径)"""
    start = datetime.combine(d, time.min).replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1)


def _eval_ingest_volume_cs_record(db: Session, ts: datetime, te: datetime) -> dict:
    """采集健康: cs_record 当日入表量相对近7日(不含当日)同时段均值的比例(%).

    返回 data_ok:
      - True: 有采集、量正常或未跌破阈值
      - "no_sync": 当日尚未有任何该源采集尝试(scheduled/manual 均无) → 断流风险
      - "sync_failed": 当日存在 status='failed' 采集记录 → 采集失败
    value: ratio% (今日实际量 / 近7日同时段均值期望), 基线不足或无采集时为 None
    """
    today_sh = _sh_now().date()
    # ts/te 为 naive UTC(当日窗口); 换算回北京时间判断"当天"
    t_start_sh = ts.replace(tzinfo=timezone.utc).astimezone(TZ).date()
    period_is_today = (t_start_sh == today_sh)

    # 1. 当日实际入表量(operation_time 北京时间当日)
    today_cnt = db.scalar(
        select(func.count()).select_from(CsRecord).where(
            CsRecord.operation_time >= ts, CsRecord.operation_time < te
        )
    ) or 0

    # 2. 近7日基线(不含当日): 每日入表量 → 均值 + 有效天数
    base_days: list[int] = []
    for back in range(1, 8):
        d = t_start_sh - timedelta(days=back)
        ds, de = _day_bounds_utc(d)
        cnt = db.scalar(
            select(func.count()).select_from(CsRecord).where(
                CsRecord.operation_time >= ds, CsRecord.operation_time < de
            )
        ) or 0
        base_days.append(cnt)
    valid_base_days = sum(1 for c in base_days if c > 0)
    baseline = (sum(base_days) / len(base_days)) if base_days else 0.0

    # 3. 当日采集运行健康(查 bms_sync_logs: 该源当日 scheduled/manual 尝试)
    biz_start_utc = _day_bounds_utc(t_start_sh)[0]
    biz_end_utc = _day_bounds_utc(t_start_sh)[1]
    sync_rows = db.execute(
        select(BmsSyncLog.status, BmsSyncLog.trigger_type)
        .where(
            BmsSyncLog.data_type == "cs_record",
            BmsSyncLog.finished_at >= biz_start_utc,
            BmsSyncLog.finished_at < biz_end_utc,
        )
    ).all()
    sync_attempted = len(sync_rows) > 0
    sync_failed = any(r.status == "failed" for r in sync_rows)

    # 4. 归一系数: 当天已过多少计划采集小时(避免早上 8 点误报"今天量少")
    #    计划采集 08:00~02:00(+1天); 计算当天已完成的比例
    now_sh = _sh_now()
    hours_passed = 0.0
    if period_is_today:
        # 计算今天 08:00 起到现在的计划小时数(含 0~2 点归属)
        if now_sh.hour >= _PLANNED_HOUR_START:
            hours_passed = now_sh.hour - _PLANNED_HOUR_START + 1
        elif now_sh.hour < 3:  # 凌晨 0-2 点仍属前一天尾段, 今天还没开始
            hours_passed = 0.0
        else:  # 3-7 点: 今天还没开始白采(补跑在5点), 视为 0 期望避免误报
            hours_passed = 0.0
        total_hours = _PLANNED_HOUR_END - _PLANNED_HOUR_START  # 18h
        norm = min(hours_passed / total_hours, 1.0) if total_hours else 1.0
    else:
        norm = 1.0  # 历史日(如补看昨天): 全天已过

    # 5. 期望值 = 基线 × 归一系数; 比值
    expected = baseline * norm
    ratio = (today_cnt / expected * 100.0) if expected > 0 else None

    # 6. data_ok 判定(冷启动保护优先: 近7日有效基线 < 3 天 → 只记录不告警)
    data_ok: bool | str = True
    if valid_base_days < 3:
        # 冷启动: 基线未积累(LOCKED §六 风险缓解), 只记录不告警, 避免新部署误报
        data_ok = "cold_start"
    elif sync_failed:
        # 采集失败: 任何日期, 只要该日存在采集失败记录 → 采集故障信号
        data_ok = "sync_failed"
    elif period_is_today and not sync_attempted and now_sh.hour >= _NO_SYNC_GRACE_HOUR:
        # no_sync: 仅当天; 已过首采保护时间(默认 09:00, 给 8:00 同步留完成窗口防误报)
        data_ok = "no_sync"

    d_label = "今日" if period_is_today else t_start_sh.strftime("%m-%d")
    detail = (f"{d_label} {today_cnt} 条 / 近7日均值 {baseline:.0f} 条"
              f"(有效基线日 {valid_base_days}/7, "
              f"已过 {hours_passed:.0f}/{total_hours if period_is_today else '—'} 计划小时, "
              f"期望 {expected:.0f})")
    if sync_attempted:
        detail += f"; 采集尝试 {len(sync_rows)} 次, failed={sum(1 for r in sync_rows if r.status == 'failed')}"
    else:
        detail += "; 该日无采集记录"
    return {"value": ratio, "data_ok": data_ok, "detail": detail}


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
    # 采集健康(工单 #5): kind=ingest_health, 区别于业务指标
    "ingest_volume_cs_record": {
        "name_cn": "智能助手记录每日入表量", "direction": HIGHER, "period": "day", "unit": "%",
        "data_sources": ["cs_record", "bms_sync_logs"],
        "kind": "ingest_health",
        "description": "采集健康: cs_record 当日入表量相对近7日均值比例(骤降/断流告警, 非业务指标)",
        "evaluate": _eval_ingest_volume_cs_record,
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
    # data_ok 列是 Boolean; 采集健康指标可能返回字符串标记(no_sync/sync_failed),
    # 快照里归一化为 True(该状态由 detail 文本承载, 告警判定用 evaluate 返回值而非快照)
    data_ok_bool = r.get("data_ok", True)
    if not isinstance(data_ok_bool, bool):
        data_ok_bool = True
    row = db.scalar(
        select(MetricDailySnapshot).where(
            MetricDailySnapshot.metric_code == code,
            MetricDailySnapshot.period_date == (period_date or datetime.now(TZ).date()).isoformat(),
        )
    )
    if row:
        row.value = r.get("value")
        row.level = r.get("level")
        row.data_ok = data_ok_bool
        row.detail = r.get("detail")
    else:
        db.add(MetricDailySnapshot(
            metric_code=code,
            value=r.get("value"),
            period_date=(period_date or datetime.now(TZ).date()).isoformat(),
            level=r.get("level"),
            data_ok=data_ok_bool,
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
