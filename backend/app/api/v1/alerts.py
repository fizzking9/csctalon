"""预警中心路由(规划设计 §八)

预警规则 CRUD + 预警记录列表 + 状态确认 + 指标阈值化扫描。
指标注册表模式(2026-08-21): 规则引用指标 + 多级阈值(优秀/预警/告警), 由
services/metric_registry_service.py 求值, 走四级分级/恢复自愈/通知分发。
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession, ManagerUser
from app.models.alert import Alert, AlertRule
from app.schemas.common import PageBase, R
from app.services.metric_registry_service import METRIC_DEFS, evaluate_metric, snapshot_metric
from app.services.notification_channel import dispatch_alert

router = APIRouter(prefix="/alerts", tags=["预警中心"])


# ---------- 规则 ----------


@router.get("/rules", response_model=R, summary="预警规则列表")
def list_rules(db: DBSession, _: CurrentUser):
    rows = db.scalars(select(AlertRule).order_by(AlertRule.id))
    return R(
        data=[
            {"id": r.id, "name": r.name, "type": r.type, "condition": r.condition, "threshold": r.threshold, "enabled": r.enabled}
            for r in rows
        ]
    )


@router.post("/rules", response_model=R, summary="新建预警规则")
def create_rule(payload: dict, db: DBSession, _: AdminUser):
    r = AlertRule(
        name=payload["name"],
        type=payload.get("type", "incident_surge"),
        condition=payload.get("condition"),
        threshold=payload.get("threshold"),
        enabled=bool(payload.get("enabled", True)),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return R(data={"id": r.id, "name": r.name})


@router.put("/rules/{rule_id}", response_model=R, summary="更新预警规则")
def update_rule(rule_id: int, payload: dict, db: DBSession, _: AdminUser):
    r = db.get(AlertRule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    for f in ("name", "type", "condition", "threshold", "enabled"):
        if f in payload:
            setattr(r, f, payload[f])
    db.commit()
    return R(message="更新成功")


@router.delete("/rules/{rule_id}", response_model=R, summary="删除预警规则")
def delete_rule(rule_id: int, db: DBSession, _: AdminUser):
    r = db.get(AlertRule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    db.delete(r)
    db.commit()
    return R()


# ---------- 预警记录 ----------


@router.get("", response_model=R[PageBase], summary="预警记录列表")
def list_alerts(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
):
    stmt = select(Alert)
    if status:
        stmt = stmt.where(Alert.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(Alert.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return R(
        data=PageBase(
            items=[
                {
                    "id": a.id,
                    "rule_id": a.rule_id,
                    "title": a.title,
                    "level": a.level,
                    "detail": a.detail,
                    "occurred_at": a.occurred_at.isoformat() if a.occurred_at else None,
                    "status": a.status,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "metric_code": a.metric_code,
                    "metric_value": a.metric_value,
                    "threshold_value": a.threshold_value,
                    "period_start": a.period_start.isoformat() if a.period_start else None,
                    "period_end": a.period_end.isoformat() if a.period_end else None,
                    "data_ok": a.data_ok,
                    "recovered_at": a.recovered_at.isoformat() if a.recovered_at else None,
                }
                for a in items
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/{alert_id}/status", response_model=R, summary="确认/忽略预警")
def set_alert_status(alert_id: int, payload: dict, db: DBSession, _: ManagerUser):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(status_code=404, detail="预警不存在")
    a.status = payload.get("status", "confirmed")
    db.commit()
    return R(message="已更新")


def _scan_metric_rule(db: DBSession, rule: AlertRule, now) -> tuple[int, list[str]]:
    """指标注册表模式扫描: 求值 → 四级分级 → 幂等/升级/恢复自愈 → 通知分发

    - 数据不完整(data_ok=False 或值缺失) → 不告警(防假告警), 仅写快照
    - 预警/告警 → 生成/升级 Alert + 站内信通知
    - 回稳(green/excellent) → 已有未恢复告警自动标记 recovered + 恢复通知

    Returns: (created_count, detail_log)
    """
    from app.models.alert import Alert as AlertModel

    meta = METRIC_DEFS.get(rule.metric_code or "")
    if not meta:
        return 0, [f"metric:{rule.metric_code}: 未注册"]
    r = evaluate_metric(db, rule.metric_code)
    snapshot_metric(db, rule.metric_code)
    level = r.get("level")
    value = r.get("value")
    data_ok = r.get("data_ok", True)
    unit = meta.get("unit", "")
    name = meta["name_cn"]
    period_start = r.get("ts")
    period_end = r.get("te")

    # 本区间未恢复告警(幂等/升级/恢复自愈锚点)
    existing = None
    if period_start:
        existing = db.scalar(
            select(AlertModel)
            .where(AlertModel.rule_id == rule.id, AlertModel.period_start == period_start, AlertModel.recovered_at.is_(None))
            .order_by(AlertModel.id.desc()).limit(1)
        )

    logs: list[str] = []
    if not data_ok or value is None:
        return 0, [f"metric:{rule.metric_code}: 数据不完整(值={value}), 跳过告警(防假告警)"]

    if level in ("warning", "critical"):
        is_critical = level == "critical"
        # 阈值以指标层配置为准(metric_thresholds, evaluate_metric 分级已按它判断)
        thr_map = r.get("threshold") or {}
        thr_val = thr_map.get("critical_value") if is_critical else thr_map.get("warning_value")
        lv_name = "告警" if is_critical else "预警"
        title = f"{name}{lv_name}: {value}{unit}(阈值 {thr_val}{unit})"
        content = f"指标「{name}」当前值 {value}{unit}, 触发{lv_name}线 {thr_val}{unit}。口径: {meta['description']}"
        if existing:
            if is_critical and existing.level != "critical":
                # 预警 → 告警 升级
                existing.level = "critical"
                existing.metric_value = value
                existing.threshold_value = thr_val
                existing.title = title
                existing.detail = content
                db.flush()
                dispatch_alert(db, existing.id, title, content, rule.channels, rule.recipient_roles, rule.silence_window)
                logs.append(f"metric:{rule.metric_code}: 升级为critical({value})")
                return 0, logs
            return 0, [f"metric:{rule.metric_code}: 同级别已有pending, 幂等跳过({value})"]
        a = AlertModel(rule_id=rule.id, title=title, level=level, detail=content, occurred_at=now,
                       status="pending", metric_code=rule.metric_code, metric_value=value,
                       threshold_value=thr_val, period_start=period_start, period_end=period_end, data_ok=data_ok)
        db.add(a)
        db.flush()
        dispatch_alert(db, a.id, title, content, rule.channels, rule.recipient_roles, rule.silence_window)
        return 1, [f"metric:{rule.metric_code}: 创建{lv_name}({value})"]

    # 恢复自愈: 回稳且有未恢复告警
    if existing and existing.status in ("pending", "confirmed"):
        existing.status = "recovered"
        existing.recovered_at = now
        db.flush()
        dispatch_alert(db, existing.id, f"{name}已恢复",
                       f"指标「{name}」当前值 {value}{unit}, 已回到正常区间。",
                       rule.channels, rule.recipient_roles, rule.silence_window)
        return 0, [f"metric:{rule.metric_code}: 恢复({value})"]
    return 0, [f"metric:{rule.metric_code}: 达标({value})"]


@router.post("/scan", response_model=R, summary="手动触发预警扫描(多规则)")
def scan_alerts(db: DBSession, _: ManagerUser):
    """扫描所有启用的规则并生成预警记录。支持:
    - 指标注册表模式(rule.metric_code): 四级阈值(优秀/预警/告警) + 恢复自愈 + 站内通知
    - 旧模式: refund_surge / ticket_backlog / incident_surge / complaint_surge(单一阈值)
    幂等: 同一规则同一日只生成一条 pending 预警(避免重复刷)。
    """
    from datetime import datetime, timedelta, timezone

    from app.models.cs_record import CsRecord
    from app.models.external_complaint import ExternalComplaint
    from app.models.incident import Incident
    from app.models.refund import Refund
    from app.models.ticket import ServiceTicket

    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
    day_start = now - timedelta(days=1)
    week_start = now - timedelta(days=7)

    # 幂等: 同 rule + 同日不重复(查 today 已存在 pending 的 rule_id)
    today_pending_rule_ids = set(
        db.scalars(
            select(Alert.rule_id).where(
                Alert.rule_id.isnot(None),
                Alert.status == "pending",
                Alert.occurred_at >= today_start,
            )
        )
    )

    rules = db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True)))
    created = 0
    detail_log = []

    for rule in rules:
        # 指标注册表模式: 内部自带区间幂等 + 升级 + 恢复自愈, 不受旧模式"当日pending跳过"影响
        if rule.metric_code:
            m_created, m_logs = _scan_metric_rule(db, rule, now)
            created += m_created
            detail_log.extend(m_logs)
            continue
        if rule.id in today_pending_rule_ids:
            continue  # 当日已生成 pending, 不重复
        rtype = (rule.type or "").lower()
        threshold = rule.threshold or 0

        try:
            if rtype == "refund_surge":
                cnt = db.scalar(select(func.count()).select_from(Refund).where(Refund.created_at >= day_start)) or 0
                if threshold and cnt >= threshold:
                    db.add(Alert(
                        rule_id=rule.id,
                        title=f"退款激增预警: 近24h退款 {cnt} 笔(阈值 {int(threshold)})",
                        level="high" if cnt >= threshold * 2 else "medium",
                        detail=f"规则「{rule.name}」触发: 近24h退款 {cnt} 笔",
                        occurred_at=now, status="pending",
                    ))
                    created += 1
                    detail_log.append(f"refund_surge: {cnt}>={int(threshold)}")

            elif rtype == "ticket_backlog":
                # 待处理工单数(ServiceTicket.status in 待处理/未结案)
                cnt = db.scalar(
                    select(func.count()).select_from(ServiceTicket).where(
                        ServiceTicket.status.in_(["待处理", "处理中", "未结案", "open", "pending"])
                    )
                ) or 0
                if threshold and cnt >= threshold:
                    db.add(Alert(
                        rule_id=rule.id,
                        title=f"工单积压预警: 当前待处理工单 {cnt} 个(阈值 {int(threshold)})",
                        level="high" if cnt >= threshold * 2 else "medium",
                        detail=f"规则「{rule.name}」触发: 待处理工单 {cnt} 个",
                        occurred_at=now, status="pending",
                    ))
                    created += 1
                    detail_log.append(f"ticket_backlog: {cnt}>={int(threshold)}")

            elif rtype == "incident_surge":
                cnt = db.scalar(select(func.count()).select_from(Incident).where(Incident.created_at >= today_start)) or 0
                if threshold and cnt >= threshold:
                    db.add(Alert(
                        rule_id=rule.id,
                        title=f"异常事件激增预警: 今日新增 {cnt} 条(阈值 {int(threshold)})",
                        level="high" if cnt >= threshold * 2 else "medium",
                        detail=f"规则「{rule.name}」触发: 今日新增异常 {cnt} 条",
                        occurred_at=now, status="pending",
                    ))
                    created += 1
                    detail_log.append(f"incident_surge: {cnt}>={int(threshold)}")

            elif rtype == "complaint_surge":
                # 近 7 天外部投诉总量
                cnt = db.scalar(select(func.count()).select_from(ExternalComplaint).where(ExternalComplaint.complaint_date >= week_start.date())) or 0
                if threshold and cnt >= threshold:
                    db.add(Alert(
                        rule_id=rule.id,
                        title=f"外部投诉激增预警: 近7天 {cnt} 条(阈值 {int(threshold)})",
                        level="high" if cnt >= threshold * 2 else "medium",
                        detail=f"规则「{rule.name}」触发: 近7天外部投诉 {cnt} 条",
                        occurred_at=now, status="pending",
                    ))
                    created += 1
                    detail_log.append(f"complaint_surge: {cnt}>={int(threshold)}")
        except Exception as e:  # noqa: BLE001 单条规则出错不影响其他规则
            detail_log.append(f"{rtype}: 扫描异常 {e}")
            continue

    db.commit()
    return R(data={"created": created, "details": detail_log})
