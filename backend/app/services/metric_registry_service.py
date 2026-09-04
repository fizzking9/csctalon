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
from app.models.complaint_sync_log import ComplaintSyncLog
from app.models.external_complaint import ExternalComplaint
from app.models.manual_refund_sync_log import ManualRefundSyncLog
from app.models.metric import MetricDailySnapshot, MetricRegistry, MetricThreshold
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.upload import UploadRecord
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

def _sh_now() -> datetime:
    """当前北京时间(naive)"""
    return datetime.now(TZ).replace(tzinfo=None)


def _day_bounds_utc(d: date_type) -> tuple[datetime, datetime]:
    """北京时间某日 [00:00, 次日00:00) → naive UTC(与 _window 同口径)"""
    start = datetime.combine(d, time.min).replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1)


# ============================================================
# 采集健康: 数据到达心跳 (工单 #6 / A-20260904-001 v1.2 LOCKED)
# 判定 = 该数据源当日是否有任一渠道的到达记录(主动抓取 success / 被动推送 /
#         手动导入 imported), 不看业务数据量(接待量波动大, 不做判据)。
# data_ok: True / "sync_failed" / "push_failed" / "no_arrival" / "cold_start"
# ============================================================
# 被动推送域: 白天(09-23)预期每 1-2h 一次, 连续 ≥N 小时无推送视为停滞(参数化)
PUSH_STALL_HOURS = 4      # 白天连续几小时无被动推送视为异常(可配置)
PUSH_DAY_START = 9        # 被动推送判定白昼起点(北京时间)
PUSH_DAY_END = 23         # 被动推送判定白昼终点
_NO_ARRIVAL_GRACE_HOUR = 9  # 主动抓取: 9 点前不判 no_arrival(给 8:00 同步留窗口)

# 被动推送源 → 其推送日志表 (data_source.code → 日志模型)
#   external_complaint/card_complaint → complaint_sync_logs(一次推 315+卡商)
#   manual_refund                      → manual_refund_sync_logs
_PUSH_LOG_TYPES: dict[str, str] = {
    "external_complaint": "complaint",
    "card_complaint": "complaint",
    "manual_refund": "manual_refund",
}


def _is_push_type(data_type: str) -> bool:
    """该 data_type 是否被动推送源(由 data_sources.sync_source=workbuddy/tencent_doc 推导)。
    兼容: manual_refund 不在 data_sources 的 bms 分组, 单独识别。"""
    return data_type in _PUSH_LOG_TYPES


def _eval_data_arrival(db: Session, data_type: str, ts: datetime, te: datetime) -> dict:
    """数据到达健康: 该 data_type 当日是否有任一渠道到达记录(不看业务量).

    渠道(按源类型):
      - 主动抓取源(bms/scrm): bms_sync_logs/scrm_sync_logs status=success;
                              manual_refund 无此渠道
      - 被动推送源(workbuddy): complaint_sync_logs/manual_refund_sync_logs
                              (success/partial 都算"推了")
      - 手动导入(所有源):     upload_records(source IN manual/bms, status=imported)
                              —— 手动补导 = 数据已到, 关键兜底不误报
    异常信号:
      - sync_failed  = 当日主动抓取存在 status='failed'
      - push_failed  = 当日被动推送存在 status='failed'
      - no_arrival   = 已过预期窗口仍无任何到达(且非 cold_start)
    value: 距最近一次到达的小时数(浮点, 展示用; 无到达为 None)
    """
    t_start_sh = ts.replace(tzinfo=timezone.utc).astimezone(TZ).date()
    now_sh = _sh_now()
    period_is_today = (t_start_sh == now_sh.date())
    d_label = "今日" if period_is_today else t_start_sh.strftime("%m-%d")
    biz_start = _day_bounds_utc(t_start_sh)[0]
    biz_end = _day_bounds_utc(t_start_sh)[1]
    is_push = _is_push_type(data_type)

    # 该源是否有被动推送日志(统一到"complaint"表时, external/card 共用, 去重计数)
    push_log_model = _PUSH_LOG_TYPES.get(data_type)
    push_status = []  # 当日该源的推送 status 列表
    manual_imported = False

    # 1. 主动抓取渠道 (非推送源才有; manual_refund/complaint 无主动抓取)
    sync_success = False
    sync_failed = False
    if not is_push:
        # bms_sync_logs; ticket 有 ticket_finish 伴生别名
        # C-1 修复: sync_failed 仅当"当日完全无 success 且存在 failed"(真断流)才报;
        #           success+failed 并存 = 到达但部分失败, 不算数据未到达, 不报 critical
        types = [data_type]
        if data_type == "ticket":
            types.append("ticket_finish")
        rows = db.execute(
            select(BmsSyncLog.status)
            .where(BmsSyncLog.data_type.in_(types), BmsSyncLog.finished_at >= biz_start, BmsSyncLog.finished_at < biz_end)
        ).all()
        sync_success = any(r.status == "success" for r in rows)
        any_failed = any(r.status == "failed" for r in rows)
        sync_failed = (not sync_success) and any_failed  # 无 success 且有 failed → 真断流

    # 2. 被动推送渠道
    push_failed = False
    if is_push:
        if push_log_model == "complaint":
            rows = db.execute(
                select(ComplaintSyncLog.status)
                .where(ComplaintSyncLog.finished_at >= biz_start, ComplaintSyncLog.finished_at < biz_end)
            ).all()
        elif push_log_model == "manual_refund":
            rows = db.execute(
                select(ManualRefundSyncLog.status)
                .where(ManualRefundSyncLog.finished_at >= biz_start, ManualRefundSyncLog.finished_at < biz_end)
            ).all()
        else:
            rows = []
        push_status = [r.status for r in rows]
        sync_success = any(s in ("success", "partial") for s in push_status)  # partial 也算推了
        # 同 C-1 语义: 仅当当日完全无 success/partial 且有 failed 才报 push_failed
        push_failed = (not sync_success) and any(s == "failed" for s in push_status)

    # 3. 手动导入兜底(所有源): upload_records 当日有 imported(限 manual/bms 来源, 对齐设计)
    up_cnt = db.scalar(
        select(func.count()).select_from(UploadRecord).where(
            UploadRecord.data_type == data_type,
            UploadRecord.status == "imported",
            UploadRecord.source.in_(("manual", "bms")),
            UploadRecord.finished_at >= biz_start,
            UploadRecord.finished_at < biz_end,
        )
    ) or 0
    manual_imported = up_cnt > 0

    reached = sync_success or manual_imported

    # 4. data_ok 判定
    data_ok: bool | str = True
    reason_parts = []
    if is_push and push_failed:
        data_ok = "push_failed"
        reason_parts.append("推送失败")
    elif (not is_push) and sync_failed:
        data_ok = "sync_failed"
        reason_parts.append("抓取失败")
    elif not reached:
        # 未到达: 区分"预期窗口是否已过"避免过早误报
        if is_push:
            # 被动推送(W-1 修复): 判定对象 = "今天是否到达过".
            # 今日已有推送(sync_success)已在上层处理(reached=True); 到这说明今日无推送.
            # 仅白天时段 + 距当天首采窗口(09:00)已过 ≥4h 才判 no_arrival, 避免清晨/夜间误报.
            if period_is_today and PUSH_DAY_START <= now_sh.hour <= PUSH_DAY_END:
                hours_since_start = now_sh.hour - PUSH_DAY_START  # 从 9 点起已过小时
                if hours_since_start >= PUSH_STALL_HOURS:
                    data_ok = "no_arrival"
                    reason_parts.append(f"今日白天 {PUSH_STALL_HOURS}h 无任何推送")
            else:
                data_ok = True  # 夜间/非今天: 不判(等待窗口)
        else:
            # 主动抓取: 过 9 点首采保护后才判 no_arrival
            if period_is_today and now_sh.hour >= _NO_ARRIVAL_GRACE_HOUR:
                data_ok = "no_arrival"
                reason_parts.append("当日无到达且无手动导入")
            else:
                data_ok = True

    # 组装 detail
    detail = f"{d_label} {data_type}"
    if sync_success:
        detail += " 已到达(抓取/推送 success)"
    if manual_imported:
        detail += f" + 手动导入 {up_cnt} 次"
    if not reached:
        detail += " 未到达"
    if is_push and push_failed:
        detail += "; 推送失败记录存在"
    elif (not is_push) and sync_failed:
        detail += "; 抓取失败记录存在"
    if push_status:
        detail += f"; 推送[{','.join(push_status[:5])}]"
    if reason_parts:
        detail += "; " + "; ".join(reason_parts)
    return {"value": None, "data_ok": data_ok, "detail": detail}


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


def _resolve_metric_meta(code: str) -> tuple[dict | None, str | None]:
    """解析指标元数据。

    支持两类:
    - 精确注册: METRIC_DEFS[code](业务指标 + 历史 ingest_volume)
    - 注册表驱动(方案A, 工单#6): code 形如 data_arrival_<data_type>, 由
      sync_source_configs/data_sources 动态发现的源, 不预注册到 METRIC_DEFS,
      统一走 _eval_data_arrival 求值(按 data_type 查渠道到达日志)。
    Returns: (meta, data_type)  data_type 仅对 data_arrival_* 非 None
    """
    meta = METRIC_DEFS.get(code)
    if meta:
        return meta, None
    if code.startswith("data_arrival_"):
        data_type = code[len("data_arrival_"):]
        if data_type:
            return {
                "name_cn": f"{data_type} 数据到达健康",
                "direction": HIGHER, "period": "day", "unit": "",
                "data_sources": ["bms_sync_logs", "upload_records", "complaint_sync_logs", "manual_refund_sync_logs"],
                "kind": "ingest_health",
                "description": "采集健康(注册表驱动): 该源当日是否有任一渠道(success/推送/手动导入)到达记录",
                "evaluate": None,  # 统一走 _eval_data_arrival
            }, data_type
    return None, None


def evaluate_metric(db: Session, code: str, period_date: date_type | None = None) -> dict:
    """指标求值 + 分级(不写库)

    Returns: {metric_code, value, data_ok, level, ts, te, detail, threshold:{...}}
    """
    meta, data_type = _resolve_metric_meta(code)
    if not meta:
        return {"metric_code": code, "value": None, "data_ok": False, "level": None, "detail": "未注册"}
    ts, te = _window(meta["period"], period_date)
    if data_type is not None:
        r = _eval_data_arrival(db, data_type, ts, te)
    else:
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
