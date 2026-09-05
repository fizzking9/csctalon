"""对账回填接口(A-20260905-002 LOCKED / 工单#17)

1. POST /reconcile/backfill-d-op-time   触发 D 路 op_time 回填(幂等)
2. GET  /reconcile/water-statement      退款记录报表逐笔勾稽视图(E 路水单)
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.schemas.common import R

router = APIRouter(prefix="/reconcile", tags=["对账回填"])


def _parse_dt(s: str | None, name: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"{name} 格式错误(应为 ISO 时间): {s}") from e


@router.post("/backfill-d-op-time", response_model=R, summary="D路手动退款 op_time 回填(幂等)")
def backfill_d_op_time(db: DBSession, _: AdminOrSupervisor, limit: int = Query(5000, ge=1, le=50000)):
    """用退款记录报表的精确退款时间, 回填 D 路(manual_refunds)缺失/倒挂的 op_time。

    仅唯一候选才写; 多候选标 pending 人工。幂等可重复执行。
    """
    from app.services.reconcile import backfill_d_op_time as _do

    stats = _do(db, limit=limit)
    return R(data=stats)


@router.get("/water-statement", response_model=R, summary="退款记录报表逐笔勾稽(E路水单)")
def water_statement(
    db: DBSession,
    _: CurrentUser,
    start: str = Query(..., description="开始时间(ISO, 北京时间)"),
    end: str = Query(..., description="结束时间(ISO, 北京时间)"),
    status: str = Query("all", description="过滤: all/matched/A/B/C/D/unmatched"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    if status not in ("all", "matched", "A", "B", "C", "D", "unmatched"):
        raise HTTPException(status_code=400, detail="status 非法")
    d_start = _parse_dt(start, "start")
    d_end = _parse_dt(end, "end")
    if not d_start or not d_end:
        raise HTTPException(status_code=400, detail="start/end 必填")
    if d_start >= d_end:
        raise HTTPException(status_code=400, detail="start 必须早于 end")

    from app.services.reconcile import water_statement as _do

    return R(data=_do(db, d_start, d_end, status=status, page=page, page_size=page_size))
