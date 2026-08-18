"""数据质量规则路由(规划设计 §8.4 占位)"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.data_quality import DataQualityRule
from app.schemas.common import R

router = APIRouter(prefix="/data-quality", tags=["数据质量"])


@router.get("/rules", response_model=R, summary="数据质量规则列表")
def list_rules(db: DBSession, _: CurrentUser):
    rows = db.scalars(select(DataQualityRule).order_by(DataQualityRule.id))
    return R(
        data=[
            {"id": r.id, "name": r.name, "target": r.target, "condition": r.condition, "description": r.description, "enabled": r.enabled}
            for r in rows
        ]
    )


@router.post("/rules", response_model=R, summary="新建数据质量规则")
def create_rule(payload: dict, db: DBSession, _: AdminUser):
    r = DataQualityRule(
        name=payload["name"],
        target=payload.get("target"),
        condition=payload.get("condition"),
        description=payload.get("description"),
        enabled=bool(payload.get("enabled", True)),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return R(data={"id": r.id, "name": r.name})


@router.put("/rules/{rule_id}", response_model=R, summary="更新数据质量规则")
def update_rule(rule_id: int, payload: dict, db: DBSession, _: AdminUser):
    r = db.get(DataQualityRule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    for f in ("name", "target", "condition", "description", "enabled"):
        if f in payload:
            setattr(r, f, payload[f])
    db.commit()
    return R(message="更新成功")


@router.delete("/rules/{rule_id}", response_model=R, summary="删除数据质量规则")
def delete_rule(rule_id: int, db: DBSession, _: AdminUser):
    r = db.get(DataQualityRule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    db.delete(r)
    db.commit()
    return R()
