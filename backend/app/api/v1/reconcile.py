"""对账接口(A-20260905-002 / A-20260906-002 LOCKED; 工单#17 / #22)

1. POST /reconcile/backfill-d-op-time      触发 D 路 op_time 回填(幂等)
2. GET  /reconcile/water-statement         退款记录报表逐笔勾稽视图(E 路水单)
3. GET  /reconcile/check/run               对账复核预览(不落库)
4. POST /reconcile/check/confirm           发起复核落库(仅留痕)
5. GET  /reconcile/check/list|detail       复核批次查询
6. POST /reconcile/check/{id}/conclusion   补/改结论备注
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.schemas.common import R

router = APIRouter(prefix="/reconcile", tags=["对账回填"])


# 复核结论枚举(与 reconcile_check.CONCLUSIONS 一致, 路由层校验用)
_CHECK_CONCLUSIONS = ("一致", "差异已确认", "存疑待查")


def _parse_dt(s: str | None, name: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"{name} 格式错误(应为 ISO 时间): {s}") from e


def _parse_win(start: str, end: str) -> tuple[datetime, datetime]:
    """解析并校验时间窗(北京时间; 支持 YYYY-MM-DD 自动补 00:00:00 / 23:59:59)"""
    d_start = _parse_dt(start, "start")
    d_end = _parse_dt(end, "end")
    if not d_start or not d_end:
        raise HTTPException(status_code=400, detail="start/end 必填")
    if d_start >= d_end:
        raise HTTPException(status_code=400, detail="start 必须早于 end")
    return d_start, d_end


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


# ─────────────────────────────────────────────────────────────
# 对账复核批次(A-20260906-002 LOCKED / #22): 仅留痕不参与统计
# ─────────────────────────────────────────────────────────────
@router.get("/check/run", response_model=R, summary="生成对账复核预览(不落库)")
def check_run(
    db: DBSession,
    _: CurrentUser,
    start: str = Query(..., description="开始时间(ISO, 北京时间)"),
    end: str = Query(..., description="结束时间(ISO, 北京时间)"),
    grain: str = Query("day", description="粒度 day/week"),
):
    if grain not in ("day", "week"):
        raise HTTPException(status_code=400, detail="grain 非法(day/week)")
    d_start, d_end = _parse_win(start, end)
    from app.services.reconcile_check import preview_check

    return R(data=preview_check(db, d_start, d_end, grain=grain))


@router.post("/check/confirm", response_model=R, summary="发起对账复核并落库留痕")
def check_confirm(
    db: DBSession,
    user: AdminOrSupervisor,
    start: str = Query(..., description="开始时间(ISO, 北京时间)"),
    end: str = Query(..., description="结束时间(ISO, 北京时间)"),
    grain: str = Query("day", description="粒度 day/week"),
    conclusion: str | None = Query(None, description="结论: 一致/差异已确认/存疑待查"),
    note: str | None = Query(None, description="复核备注"),
):
    if grain not in ("day", "week"):
        raise HTTPException(status_code=400, detail="grain 非法(day/week)")
    if conclusion and conclusion not in _CHECK_CONCLUSIONS:
        raise HTTPException(status_code=400, detail=f"conclusion 非法: {conclusion}")
    d_start, d_end = _parse_win(start, end)
    from app.services.reconcile_check import _ser_check, confirm_check

    check = confirm_check(db, user, d_start, d_end, grain=grain, conclusion=conclusion, note=note)
    return R(data=_ser_check(check))


@router.get("/check/list", response_model=R, summary="对账复核批次列表")
def check_list(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    conclusion: str | None = Query(None, description="过滤结论"),
):
    if conclusion and conclusion not in _CHECK_CONCLUSIONS:
        raise HTTPException(status_code=400, detail=f"conclusion 非法: {conclusion}")
    from app.services.reconcile_check import list_checks

    return R(data=list_checks(db, page=page, page_size=page_size, conclusion=conclusion))


@router.get("/check/detail", response_model=R, summary="对账复核批次详情")
def check_detail(
    db: DBSession,
    _: CurrentUser,
    id: int = Query(..., ge=1, description="复核批次 id"),
):
    from app.services.reconcile_check import _ser_check, get_check

    check = get_check(db, id)
    if not check:
        raise HTTPException(status_code=404, detail="复核批次不存在")
    return R(data=_ser_check(check))


@router.post("/check/{check_id}/conclusion", response_model=R, summary="补/改复核结论与备注")
def check_conclusion(
    db: DBSession,
    user: AdminOrSupervisor,
    check_id: int,
    conclusion: str | None = Query(None, description="结论: 一致/差异已确认/存疑待查"),
    note: str | None = Query(None, description="复核备注"),
):
    if conclusion and conclusion not in _CHECK_CONCLUSIONS:
        raise HTTPException(status_code=400, detail=f"conclusion 非法: {conclusion}")
    from app.services.reconcile_check import _ser_check, set_conclusion

    try:
        check = set_conclusion(db, user, check_id, conclusion=conclusion, note=note)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return R(data=_ser_check(check))


# ─────────────────────────────────────────────────────────────
# 逐笔勾稽明细核对 (A-20260906-002 v1.1 / #22): 仅打标不改源
# ─────────────────────────────────────────────────────────────
# 逐笔确认状态(与 reconcile_check.CONFIRM_STATUSES 一致)
_CHECK_LINE_STATUSES = ("已核对一致", "差异已确认", "存疑待查", "已排除重复", "待核对")


@router.get("/check/{check_id}/lines", response_model=R, summary="批次逐笔勾稽列表")
def check_lines(
    db: DBSession,
    _: CurrentUser,
    check_id: int,
    route: str | None = Query(None, description="过滤 auto_route: A/B/C/D/unmatched"),
    status: str | None = Query(None, description="过滤 confirm_status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    if route and route not in ("A", "B", "C", "D", "unmatched"):
        raise HTTPException(status_code=400, detail="route 非法")
    if status and status not in _CHECK_LINE_STATUSES:
        raise HTTPException(status_code=400, detail="status 非法")
    from app.services.reconcile_check import list_lines

    try:
        return R(data=list_lines(db, check_id, route=route, status=status, page=page, page_size=page_size))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/check/{check_id}/lines-summary", response_model=R, summary="批次逐笔确认进度")
def check_lines_summary(
    db: DBSession,
    _: CurrentUser,
    check_id: int,
):
    from app.services.reconcile_check import lines_summary

    try:
        return R(data=lines_summary(db, check_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/check/line-candidates", response_model=R, summary="E 笔候选匹配经营单明细")
def check_line_candidates(
    db: DBSession,
    _: CurrentUser,
    report_id: int = Query(..., ge=1, description="E 退款记录报表 id"),
):
    from app.models.refund_record_report import RefundRecordReport
    from app.services.reconcile_check import _route_candidates

    report = db.get(RefundRecordReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="E 退款记录报表不存在")
    cand = _route_candidates(db, report)
    return R(data={
        "report_id": report.id,
        "platform_order_no": report.platform_order_no,
        "refund_amount": float(report.refund_amount) if report.refund_amount is not None else None,
        "auto_route": cand["auto_route"],
        "candidates": cand["candidates"],
    })


@router.post("/check/{check_id}/line-confirm", response_model=R, summary="对单笔打确认状态(不改源)")
def check_line_confirm(
    db: DBSession,
    user: AdminOrSupervisor,
    check_id: int,
    report_id: int = Query(..., ge=1, description="E 退款记录报表 id"),
    confirm_status: str = Query(..., description="已核对一致/差异已确认/存疑待查/已排除重复/待核对"),
    confirm_note: str | None = Query(None, description="备注"),
):
    if confirm_status not in _CHECK_LINE_STATUSES:
        raise HTTPException(status_code=400, detail=f"confirm_status 非法: {confirm_status}")
    from app.services.reconcile_check import _ser_line, confirm_line

    try:
        line = confirm_line(db, user, check_id, report_id,
                            confirm_status=confirm_status, confirm_note=confirm_note)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return R(data=_ser_line(line))
