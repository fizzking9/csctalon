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
    status: str = Query("pending", pattern="^(all|pending|auto_fixed|ok|ignored|dup_residual)$"),
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


# ─────────────────────────────────────────────────────────────
# 拒收行(未采纳数据)管理 —— A-20260908-003 数据治理框架
# 独立 sub-router(prefix=/data-quality/rejected), 避免与上方 /{table} 通配冲突
# 语义: 外部数据未入库行按行显示; open(未判定)/discard 不纳入统计; keep 采纳
# ─────────────────────────────────────────────────────────────
rejected_router = APIRouter(prefix="/data-quality/rejected", tags=["数据质量-拒收行"])


@rejected_router.get("", response_model=R, summary="拒收行列表(未采纳数据按行查看)")
def rejected_list_endpoint(
    db: DBSession, _: CurrentUser,
    data_type: str | None = Query(None),
    source: str | None = Query(None),
    status: str | None = Query(None, pattern="^(open|keep|discard)$"),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    from app.services.ingest_rejected_service import list_rejected_rows

    return R(data=list_rejected_rows(
        db, data_type=data_type, source=source, status=status,
        category=category, page=page, page_size=page_size,
    ))


@rejected_router.get("/stats", response_model=R, summary="拒收行统计(各源/拒因/状态)")
def rejected_stats_endpoint(db: DBSession, _: CurrentUser):
    from app.services.ingest_rejected_service import rejected_stats

    return R(data=rejected_stats(db))


@rejected_router.post("/{row_id}/review", response_model=R, summary="人工判定拒收行(keep/discard)")
def rejected_review_endpoint(
    row_id: int, db: DBSession, current_user: CurrentUser, payload: dict = Body(...),
):
    from app.services.ingest_rejected_service import review_rejected_row

    action = payload.get("action")
    try:
        row = review_rejected_row(db, row_id, action, reviewer=str(current_user.username or current_user.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not row:
        raise HTTPException(status_code=404, detail="拒收行不存在")
    return R(data=row, message=f"已标记为 {action}")


@rejected_router.post("/batch-review", response_model=R, summary="批量判定拒收行(keep/discard)")
def rejected_batch_review_endpoint(
    db: DBSession, current_user: CurrentUser, payload: dict = Body(...),
):
    from app.services.ingest_rejected_service import batch_review_rejected_rows

    row_ids = payload.get("row_ids") or []
    action = payload.get("action")
    if not row_ids:
        raise HTTPException(status_code=400, detail="row_ids 不能为空")
    try:
        n = batch_review_rejected_rows(db, row_ids, action, reviewer=str(current_user.username or current_user.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data={"updated": n}, message=f"批量处理 {n} 行")


# ─────────────────────────────────────────────────────────────
# 质量处置(ignore/restore/suggestion) + 治理规则管理 —— A-20260909-001
# 独立 sub-router(prefix=/data-quality/disposition), 避免与上方 /{table} 通配冲突
# 语义: 已入库质量问题行的 L2 处置(源头打标 ignored/恢复) + L3 规则复用
# ─────────────────────────────────────────────────────────────
disposition_router = APIRouter(prefix="/data-quality/disposition", tags=["数据质量-治理处置"])


@disposition_router.post("/{table}/{pk}/ignore", response_model=R, summary="彻底忽略质量问题行(quality_status→ignored)")
def disposition_ignore_endpoint(table: str, pk: str, db: DBSession, current_user: CurrentUser,
                                payload: dict = Body(default={})):
    _get_table(table)
    from app.services.quality_disposition_service import quality_ignore

    op = str(current_user.username or current_user.id)
    try:
        data = quality_ignore(db, table, pk, operator=op, reason=payload.get("reason"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data=data, message=data.get("message", "已忽略"))


@disposition_router.post("/{table}/{pk}/restore", response_model=R, summary="恢复质量问题行(清除 ignored/dup_residual)")
def disposition_restore_endpoint(table: str, pk: str, db: DBSession, current_user: CurrentUser):
    _get_table(table)
    from app.services.quality_disposition_service import quality_restore

    op = str(current_user.username or current_user.id)
    try:
        data = quality_restore(db, table, pk, operator=op)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data=data, message=data.get("message", "已恢复"))


@disposition_router.get("/{table}/{pk}/suggestion", response_model=R, summary="单行治理建议(规则命中+E匹配提示)")
def disposition_suggestion_endpoint(table: str, pk: str, db: DBSession, _: CurrentUser):
    _get_table(table)
    from app.services.quality_disposition_service import quality_suggestion

    try:
        data = quality_suggestion(db, table, pk)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data=data)


# ── 治理规则 CRUD ────────────────────────────────────────────
@disposition_router.get("/rules", response_model=R, summary="治理规则列表")
def disposition_rules_endpoint(db: DBSession, _: CurrentUser,
                               table_code: str | None = Query(None),
                               enabled: bool | None = Query(None)):
    from app.services.quality_disposition_service import list_rules

    return R(data=list_rules(db, table_code=table_code, enabled=enabled))


@disposition_router.post("/rules", response_model=R, summary="新建治理规则")
def disposition_rule_create_endpoint(db: DBSession, current_user: CurrentUser, payload: dict = Body(...)):
    from app.services.quality_disposition_service import create_rule

    op = str(current_user.username or current_user.id)
    try:
        rule = create_rule(db, payload, operator=op)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data=rule, message="规则已创建")


@disposition_router.put("/rules/{rule_id}", response_model=R, summary="更新治理规则")
def disposition_rule_update_endpoint(rule_id: int, db: DBSession, current_user: CurrentUser,
                                     payload: dict = Body(...)):
    from app.services.quality_disposition_service import update_rule

    op = str(current_user.username or current_user.id)
    try:
        rule = update_rule(db, rule_id, payload, operator=op)
    except ValueError as e:
        raise HTTPException(status_code=404 if "不存在" in str(e) else 400, detail=str(e))
    return R(data=rule, message="规则已更新")


@disposition_router.delete("/rules/{rule_id}", response_model=R, summary="删除治理规则")
def disposition_rule_delete_endpoint(rule_id: int, db: DBSession, _: CurrentUser):
    from app.services.quality_disposition_service import delete_rule

    if not delete_rule(db, rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    return R(message="规则已删除")


@disposition_router.post("/rules/{rule_id}/apply-test", response_model=R, summary="规则试匹配(预览命中行,不执行)")
def disposition_rule_apply_test_endpoint(rule_id: int, db: DBSession, _: CurrentUser,
                                         limit: int = Query(100, ge=1, le=1000)):
    from app.services.quality_disposition_service import apply_rule_suggest

    try:
        data = apply_rule_suggest(db, rule_id, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data=data)


# ── A/D 真重复识别扫描(种子规则 ad_overlap_dup) ──────────────
@disposition_router.get("/scan/ad-overlap-dup", response_model=R, summary="扫描 A/D 真重复候选(同单+金额一致+30天窗)")
def disposition_scan_ad_overlap_endpoint(db: DBSession, _: CurrentUser,
                                         limit: int = Query(500, ge=1, le=10000)):
    from app.services.quality_disposition_service import scan_ad_overlap_dup

    return R(data=scan_ad_overlap_dup(db, limit=limit))


# ── D 路缺 SN 坏副本自动排除 (2026-09-09) ────────────────────
@disposition_router.post("/auto/d-no-sn-bad-dup", response_model=R, summary="D路缺SN坏副本自动标dup_residual(同单已由带SN行/E表计)")
def disposition_auto_d_no_sn_bad_dup_endpoint(db: DBSession, _: CurrentUser,
                                              limit: int = Query(2000, ge=1, le=10000),
                                              include_auto_fixed: bool = Query(False)):
    from app.services.quality_disposition_service import auto_dispose_d_no_sn_bad_dup

    try:
        data = auto_dispose_d_no_sn_bad_dup(db, limit=limit, include_auto_fixed=include_auto_fixed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data=data, message=f"已自动排除 {data.get('disposed', 0)} 行缺SN坏副本")


# ── E 空悬 → D 路台账对账回填 (2026-09-09) ──────────────────
@disposition_router.post("/auto/backfill-e-orphan-to-d", response_model=R,
                         summary="E空悬单回填D路台账(系统/自动原路退, INSERT manual_refunds 备注溯源)")
def disposition_backfill_e_orphan_to_d_endpoint(db: DBSession, _: CurrentUser,
                                                start: str | None = Query(None, description="北京日期 YYYY-MM-DD"),
                                                end: str | None = Query(None, description="北京日期 YYYY-MM-DD"),
                                                limit: int = Query(2000, ge=1, le=10000)):
    from app.services.quality_disposition_service import backfill_e_orphan_to_d

    try:
        data = backfill_e_orphan_to_d(db, start=start, end=end, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data=data, message=f"已回填 {data.get('inserted', 0)} 笔 E 空悬到 D 路台账(备注溯源)")


# ── D 路同单同金额重复自动排除 (2026-09-09) ──────────────────
@disposition_router.post("/auto/d-dup-same-amount", response_model=R,
                         summary="D路同单同金额重复自动标dup_residual(保留带SN/正常行, 副本剔除)")
def disposition_auto_d_dup_same_amount_endpoint(db: DBSession, _: CurrentUser,
                                                limit: int = Query(2000, ge=1, le=10000)):
    from app.services.quality_disposition_service import auto_dispose_d_dup_same_amount

    try:
        data = auto_dispose_d_dup_same_amount(db, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data=data, message=f"已自动排除 {data.get('disposed', 0)} 行 D 同单同金额重复副本")


# ── D 路"E 无对应金额笔"冗余剔除(逐笔对E + 时序护栏) (2026-09-09) ──
@disposition_router.post("/auto/d-no-e-match", response_model=R,
                         summary="D路冗余剔除(金额E无对应笔, 仅已对账日期; E滞后窗跳过)")
def disposition_auto_d_no_e_match_endpoint(db: DBSession, _: CurrentUser,
                                           limit: int = Query(2000, ge=1, le=10000)):
    from app.services.quality_disposition_service import auto_dispose_d_no_e_match

    try:
        data = auto_dispose_d_no_e_match(db, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return R(data=data, message=f"已剔除 {data.get('disposed', 0)} 行 D 冗余(E无对应, 已对账日期)")
