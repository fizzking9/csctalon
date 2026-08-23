"""数据质量规则路由(规划设计 §8.4 占位) + 通用数据质量审核(L3 人工审核)"""

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DBSession, get_current_user, get_db
from app.models.data_quality import DataQualityRule
from app.models.user import User
from app.schemas.common import R
from app.services import quality_check as QC

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


# ─────────────────────────────────────────────────────────────
# 通用数据质量审核 (L3 人工审核) —— 支持 refund/ticket/wallet/shanmao/manual_refund
# 数据由导入时 L2 自动检测写入 quality_status/quality_note, 也可通过 scan 对存量重检
# ─────────────────────────────────────────────────────────────

def _get_table(table: str) -> dict:
    try:
        return QC.get_table_config(table)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tables", response_model=R, summary="数据质量-支持审核的表列表(含各状态统计)")
def quality_tables(db: DBSession, _: CurrentUser):
    data = []
    for code, cfg in QC.QUALITY_TABLES.items():
        s = QC.quality_summary(db, code)
        data.append({
            "code": code,
            "label": cfg["label"],
            "pk_field": cfg["pk_field"],
            "pk_label": cfg["pk_label"],
            "fixable_fields": cfg["fixable_fields"],
            "pending": s["pending"],
            "auto_fixed": s["auto_fixed"],
            "ok": s["ok"],
            "total": s["total"],
        })
    return R(data=data)


@router.get("/{table}/list", response_model=R, summary="数据质量-表单质量记录列表")
def quality_list_endpoint(
    table: str, db: DBSession, _: CurrentUser,
    status: str = Query("pending", pattern="^(all|pending|auto_fixed|ok)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    _get_table(table)
    return R(data=QC.quality_list(db, table, status, page, page_size))


@router.post("/{table}/{pk}/confirm", response_model=R, summary="数据质量-人工确认通过(置ok)")
def quality_confirm_endpoint(table: str, pk: str, db: DBSession, _: CurrentUser):
    _get_table(table)
    return R(data=QC.quality_confirm(db, table, pk))


@router.post("/{table}/{pk}/fix", response_model=R, summary="数据质量-人工修正字段(置ok)")
def quality_fix_endpoint(
    table: str, pk: str, payload: dict = Body(...),
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    _get_table(table)
    return R(data=QC.quality_fix(db, table, pk, payload))


@router.post("/{table}/scan", response_model=R, summary="数据质量-存量数据重检(全表跑校验规则,管理员)")
def quality_scan_endpoint(table: str, db: DBSession, _: AdminUser,
                          limit: int = Query(0, ge=0, le=1_000_000)):
    _get_table(table)
    return R(data=QC.quality_scan(db, table, limit=limit))
