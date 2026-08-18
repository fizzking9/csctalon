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


@router.post("/scan", response_model=R, summary="手动触发预警扫描(占位)")
def scan_alerts(db: DBSession, _: ManagerUser):
    """首版占位: 扫描规则并生成预警记录。当前仅做退款激增的简化检测。"""
    from datetime import datetime, timedelta, timezone
    from app.models.refund import Refund

    created = 0
    now = datetime.now(timezone.utc)
    day_start = now - timedelta(days=1)
    # 简化: 检查最近1天退款单量, 超过阈值则预警(阈值取配置或默认)
    rules = db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True), AlertRule.type == "refund_surge"))
    for rule in rules:
        cnt = db.scalar(select(func.count()).select_from(Refund).where(Refund.created_at >= day_start)) or 0
        threshold = rule.threshold or 100
        if cnt >= threshold:
            db.add(
                Alert(
                    rule_id=rule.id,
                    title=f"退款激增预警: 近24小时退款 {cnt} 笔(阈值 {int(threshold)})",
                    level="high" if cnt >= threshold * 2 else "medium",
                    detail=f"规则「{rule.name}」触发",
                    occurred_at=now,
                    status="pending",
                )
            )
            created += 1
    db.commit()
    return R(data={"created": created})
