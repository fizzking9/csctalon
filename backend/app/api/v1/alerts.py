"""预警中心路由(规划设计 §八)

预警规则 CRUD + 预警记录列表 + 状态确认。
异常自动识别的扫描逻辑首版留占位(规则表可配置, 扫描由调度器/手动触发)。
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession, ManagerUser
from app.models.alert import Alert, AlertRule
from app.schemas.common import PageBase, R

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


@router.post("/scan", response_model=R, summary="手动触发预警扫描(多规则)")
def scan_alerts(db: DBSession, _: ManagerUser):
    """扫描所有启用的规则并生成预警记录。支持:
    - refund_surge: 近 24h 退款单量超阈值
    - ticket_backlog: 当前待处理工单数超阈值
    - incident_surge: 当日新增异常事件超阈值
    - complaint_surge: 近 N 天外部投诉量超阈值
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
