"""管理措施路由 - 审计/周报输出物,带到期回看

管理层(AdminOrSupervisor)维护;普通用户可查看。
每条措施带「预期目标+验证时间+验证方式」,到期回看(手动或自动扫描)。
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.measure import (
    Measure,
    MeasureSource,
    MeasureStatus,
    MeasureType,
    ReviewResult,
)
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/measures", tags=["管理措施"])


def _serialize(m: Measure) -> dict:
    return {
        "id": m.id,
        "title": m.title,
        "measure_type": m.measure_type,
        "source": m.source,
        "owner_id": m.owner_id,
        "related_report_id": m.related_report_id,
        "expected_goal": m.expected_goal,
        "verify_time": m.verify_time.isoformat() if m.verify_time else None,
        "verify_method": m.verify_method,
        "status": m.status,
        "valid_from": m.valid_from.isoformat() if m.valid_from else None,
        "valid_to": m.valid_to.isoformat() if m.valid_to else None,
        "review_result": m.review_result,
        "review_note": m.review_note,
        "reviewed_at": m.reviewed_at.isoformat() if m.reviewed_at else None,
        "reviewed_by": m.reviewed_by,
        "created_by": m.created_by,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


def _apply_status_filter(stmt, status: str | None):
    if not status:
        return stmt
    try:
        return stmt.where(Measure.status == MeasureStatus(status).value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的状态值: {status}")


@router.get("", response_model=R[PageBase], summary="管理措施列表")
def list_measures(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="状态: draft/executing/pending_review/effective/ineffective/archived"),
    measure_type: str | None = Query(None, description="类型: internal/external"),
    source: str | None = Query(None, description="来源: audit/weekly/incident/manual"),
    owner_id: int | None = None,
):
    stmt = select(Measure)
    stmt = _apply_status_filter(stmt, status)
    if measure_type:
        try:
            stmt = stmt.where(Measure.measure_type == MeasureType(measure_type).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的类型值: {measure_type}")
    if source:
        try:
            stmt = stmt.where(Measure.source == MeasureSource(source).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的来源值: {source}")
    if owner_id:
        stmt = stmt.where(Measure.owner_id == owner_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(Measure.verify_time.asc().nulls_last(), Measure.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize(m) for m in items], total=total or 0, page=page, page_size=page_size))


@router.get("/due-review", response_model=R[PageBase], summary="到期待回看措施")
def list_due_review(
    db: DBSession,
    current_user: AdminOrSupervisor,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """verify_time <= now 且状态为 执行中/待回看 的措施,按到期时间升序。
    供自动扫描与手动触发的回看队列消费。"""
    now = func.now()
    stmt = (
        select(Measure)
        .where(Measure.verify_time.isnot(None))
        .where(Measure.verify_time <= now)
        .where(Measure.status.in_([MeasureStatus.EXECUTING.value, MeasureStatus.PENDING_REVIEW.value]))
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(Measure.verify_time.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize(m) for m in items], total=total or 0, page=page, page_size=page_size))


@router.get("/{measure_id}", response_model=R, summary="措施详情")
def get_measure(measure_id: int, db: DBSession, current_user: CurrentUser):
    m = db.get(Measure, measure_id)
    if not m:
        raise HTTPException(status_code=404, detail="措施不存在")
    return R(data=_serialize(m))


@router.post("", response_model=R, summary="创建措施")
def create_measure(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    m = Measure(
        title=payload["title"],
        measure_type=MeasureType(payload.get("measure_type", "internal")).value,
        source=MeasureSource(payload.get("source", "manual")).value,
        owner_id=payload.get("owner_id"),
        related_report_id=payload.get("related_report_id"),
        expected_goal=payload.get("expected_goal"),
        verify_time=datetime.fromisoformat(payload["verify_time"]) if payload.get("verify_time") else None,
        verify_method=payload.get("verify_method"),
        status=MeasureStatus(payload.get("status", "draft")).value,
        valid_from=datetime.fromisoformat(payload["valid_from"]) if payload.get("valid_from") else None,
        valid_to=datetime.fromisoformat(payload["valid_to"]) if payload.get("valid_to") else None,
        created_by=current_user.username,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return R(data=_serialize(m))


@router.put("/{measure_id}", response_model=R, summary="更新措施")
def update_measure(measure_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    m = db.get(Measure, measure_id)
    if not m:
        raise HTTPException(status_code=404, detail="措施不存在")
    for k in ("title", "owner_id", "related_report_id", "expected_goal", "verify_method", "review_note"):
        if k in payload:
            setattr(m, k, payload[k])
    for k in ("verify_time", "valid_from", "valid_to"):
        if k in payload:
            setattr(m, k, datetime.fromisoformat(payload[k]) if payload[k] else None)
    if "measure_type" in payload:
        m.measure_type = MeasureType(payload["measure_type"]).value
    if "source" in payload:
        m.source = MeasureSource(payload["source"]).value
    if "status" in payload:
        m.status = MeasureStatus(payload["status"]).value
    db.commit()
    db.refresh(m)
    return R(data=_serialize(m))


@router.post("/{measure_id}/review", response_model=R, summary="措施到期回看")
def review_measure(measure_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    """记录回看结论,设回看时间/人,按结论流转状态。
    生效→已生效;部分生效→待回看(继续观察);未生效/需调整→未生效(转新措施或升级)。"""
    m = db.get(Measure, measure_id)
    if not m:
        raise HTTPException(status_code=404, detail="措施不存在")
    result = payload.get("review_result")
    if result:
        try:
            m.review_result = ReviewResult(result).value
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的回看结论: {result}")
    m.review_note = payload.get("review_note")
    m.reviewed_at = datetime.now()
    m.reviewed_by = current_user.username
    if m.review_result == ReviewResult.EFFECTIVE.value:
        m.status = MeasureStatus.EFFECTIVE.value
    elif m.review_result == ReviewResult.PARTIAL.value:
        m.status = MeasureStatus.PENDING_REVIEW.value
    elif m.review_result in (ReviewResult.INEFFECTIVE.value, ReviewResult.ADJUST.value):
        m.status = MeasureStatus.INEFFECTIVE.value
    db.commit()
    db.refresh(m)
    return R(data=_serialize(m))


@router.delete("/{measure_id}", response_model=R, summary="删除措施")
def delete_measure(measure_id: int, db: DBSession, current_user: AdminOrSupervisor):
    m = db.get(Measure, measure_id)
    if not m:
        raise HTTPException(status_code=404, detail="措施不存在")
    db.delete(m)
    db.commit()
    return R()
