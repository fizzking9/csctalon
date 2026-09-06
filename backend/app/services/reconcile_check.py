"""对账复核服务 - ABCD/E 五路手动对账复核留痕(A-20260906-002 LOCKED / #22)

复用统一核算口径(_cash_routes_total) 与 E 路逐笔勾稽分类(water_statement 的
_build_route_maps/_classify_report), 保证复核快照与展示页同窗口口径一致。
复核批次落 reconcile_checks(仅留痕, 不参与任何统计聚合)。

功能:
1. preview_check(db, start, end, grain): 生成对账预览(不落库)
   - route_snapshot: _cash_routes_total 输出(五路+经营+实际+差异)
   - by_route_of_e:   E 水单勾稽归属(A/B/C/D/unmatched)
   - dupe_detect:     D 路同 order_no 多行/无SN乱年份检出(辅助复核)
2. confirm_check(db, user, ...): 发起复核, 落库 reconcile_checks
3. list_checks / get_check: 批次查询
4. set_conclusion(db, user, id, conclusion, note): 补/改结论与备注
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.manual_refund import ManualRefund
from app.models.reconcile_check import ReconcileCheck
from app.models.refund_record_report import RefundRecordReport

logger = logging.getLogger(__name__)

_TZ_CN = timezone(timedelta(hours=8))

# 与 analytics._route_e_cashflow_filter 一致(避免循环 import, 本地常量)
_E_ROUTE_SUCCESS_STATUSES = ("退款成功", "成功", "SUCCESS")

# 复核结论枚举(校验用)
CONCLUSIONS = ("一致", "差异已确认", "存疑待查")

# 残留脏行判定年份(与 reconcile.backfill 护栏一致: 无SN + 年份异常)
_CURRENT_YEARS = (2025, 2026)


def _to_utc_aware(dt: datetime) -> datetime:
    """参数按北京时间 → UTC aware(与 reconcile._to_utc_aware 同语义, 避免循环 import)"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_TZ_CN).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_cn(dt: datetime) -> datetime:
    """UTC aware → 北京时间(序列化用)"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ_CN)


# ─────────────────────────────────────────────────────────────
# 1. 对账预览(不落库)
# ─────────────────────────────────────────────────────────────
def _route_snapshot(db, start: datetime, end: datetime) -> dict:
    """五路合计 + 经营/实际/差异 时点快照(复用 _cash_routes_total 口径)"""
    from app.services.analytics import _cash_routes_total

    # _cash_routes_total 用 UTC aware 时间窗即可(底层按存储时间戳过滤)
    return _cash_routes_total(db, start=start, end=end)


def _e_by_route(db, start: datetime, end: datetime) -> dict:
    """E 水单勾稽归属(A/B/C/D/unmatched) 笔数金额(复用 water_statement 分类逻辑)"""
    from app.services.reconcile import _build_route_maps, _classify_report

    base_rows = db.scalars(
        select(RefundRecordReport).where(
            RefundRecordReport.refund_status.in_(_E_ROUTE_SUCCESS_STATUSES),
            RefundRecordReport.refund_time >= start,
            RefundRecordReport.refund_time <= end,
        )
    ).all()
    maps = _build_route_maps(db, start, end)
    by_route: dict[str, dict] = {
        "A": {"count": 0, "amount": 0.0},
        "B": {"count": 0, "amount": 0.0},
        "C": {"count": 0, "amount": 0.0},
        "D": {"count": 0, "amount": 0.0},
        "unmatched": {"count": 0, "amount": 0.0},
    }
    for r in base_rows:
        route = _classify_report(r, maps)
        bucket = by_route.setdefault(route, {"count": 0, "amount": 0.0})
        bucket["count"] += 1
        bucket["amount"] += float(r.refund_amount or 0)
    # 归一(仅保留 5 桶; unmatched 命名给前端)
    return {k: {"count": v["count"], "amount": round(v["amount"], 2)} for k, v in by_route.items()}


def _dupe_detect(db, start: datetime, end: datetime) -> dict:
    """D 路重复/脏数据检出(辅助复核; 不阻塞)"""
    # 同 order_no 多行(窗口内已退款, 按 COALESCE(op_time,register_time) 落窗)
    rows = db.execute(
        select(
            ManualRefund.order_no,
            func.count().label("n"),
            func.count(ManualRefund.device_sn).label("with_sn"),
            func.coalesce(func.sum(ManualRefund.refund_amount), 0).label("amt"),
        )
        .where(
            ManualRefund.is_refunded == "是",
            ManualRefund.order_no.is_not(None),
            ManualRefund.order_no != "",
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time).is_not(None),
            # 落窗过滤: 与第二段 no_sn_weird 口径一致(均在所选时间窗内), 避免"全域 vs 窗口"口径矛盾 (#21/#22 Reviewer W2)
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time) >= start,
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time) <= end,
        )
        .group_by(ManualRefund.order_no)
        .having(func.count() > 1)
    ).all()
    dup_amt = 0.0
    dup_groups = 0
    for _o, n, _ws, amt in rows:
        dup_groups += 1
        dup_amt += float(amt or 0) * (n - 1) / n  # 超额占比估算(每单多出 n-1/n)
    # 无SN+年份异常残留行(窗口内, 与治理脚本同口径; 行数可控, 逐行判年份可接受)
    residual_rows = 0
    residual_amt = 0.0
    for m in db.scalars(
        select(ManualRefund).where(
            ManualRefund.is_refunded == "是",
            (ManualRefund.device_sn.is_(None)) | (ManualRefund.device_sn == ""),
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time).is_not(None),
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time) >= start,
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time) <= end,
        )
    ).all():
        if m.register_time is None:
            continue
        try:
            yr = m.register_time.astimezone(_TZ_CN).year
        except (ValueError, OSError):
            continue
        if yr not in _CURRENT_YEARS:
            # 该行 COALESCE(op_time,register_time) 已落在窗口(上面 SQL 已过滤), 直接计
            residual_rows += 1
            residual_amt += float(m.refund_amount or 0)

    return {
        "dup_order_groups": dup_groups,
        "dup_amount_est": round(dup_amt, 2),
        "no_sn_weird_rows_in_win": residual_rows,
        "no_sn_weird_amt_in_win": round(residual_amt, 2),
        "note": "仅辅助提示(限所选窗口): dup_amount_est 为同单多行超额占比估算, no_sn_weird 为无SN+乱年份残留行(治理#21 目标)",
    }


def _gen_check_no(db, day: str) -> str:
    """生成复核单号 RC+YYYYMMDD+序号"""
    prefix = f"RC{day.replace('-', '')}"
    exists = db.scalar(
        select(func.count()).select_from(ReconcileCheck).where(
            ReconcileCheck.check_no.like(f"{prefix}%")
        )
    ) or 0
    return f"{prefix}{exists + 1:03d}"


def build_check_payload(db, start: datetime, end: datetime, grain: str) -> dict:
    """生成复核落库所需快照(route_snapshot / by_route_of_e / dupe_detect)"""
    start_u = _to_utc_aware(start)
    end_u = _to_utc_aware(end)
    return {
        "route_snapshot": _route_snapshot(db, start_u, end_u),
        "by_route_of_e": _e_by_route(db, start_u, end_u),
        "dupe_detect": _dupe_detect(db, start_u, end_u),
    }


def preview_check(db, start: datetime, end: datetime, grain: str = "day") -> dict:
    """生成对账预览(不落库)"""
    payload = build_check_payload(db, start, end, grain)
    return {
        "window": {
            "start": _to_cn(_to_utc_aware(start)).isoformat(),
            "end": _to_cn(_to_utc_aware(end)).isoformat(),
            "grain": grain,
        },
        **payload,
    }


# ─────────────────────────────────────────────────────────────
# 2. 复核批次落库 / 查询 / 改结论
# ─────────────────────────────────────────────────────────────
def _user_display_name(user) -> str | None:
    """取用户显示名(User.display_name → username 兜底)"""
    if user is None:
        return None
    return getattr(user, "display_name", None) or getattr(user, "username", None)


def confirm_check(db, user, start: datetime, end: datetime, grain: str = "day",
                  conclusion: str | None = None, note: str | None = None) -> ReconcileCheck:
    """发起复核: 冻结时点快照落库 reconcile_checks"""
    if conclusion and conclusion not in CONCLUSIONS:
        raise ValueError(f"conclusion 非法: {conclusion}, 应为 {CONCLUSIONS}")
    start_u = _to_utc_aware(start)
    end_u = _to_utc_aware(end)
    if start_u >= end_u:
        raise ValueError("start 必须早于 end")

    payload = build_check_payload(db, start_u, end_u, grain)
    check = ReconcileCheck(
        check_no=_gen_check_no(db, _to_cn(start_u).strftime("%Y-%m-%d")),
        window_start=start_u,
        window_end=end_u,
        grain=grain,
        route_snapshot=payload["route_snapshot"],
        by_route_of_e=payload["by_route_of_e"],
        dupe_detect=payload["dupe_detect"],
        conclusion=conclusion,
        note=note,
        reviewed_by=user.id if user else None,
        reviewed_name=_user_display_name(user),
        reviewed_at=datetime.now(timezone.utc),
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    return check


def list_checks(db, page: int = 1, page_size: int = 20, conclusion: str | None = None) -> dict:
    """复核批次列表(新→旧)"""
    q = select(ReconcileCheck)
    if conclusion:
        q = q.where(ReconcileCheck.conclusion == conclusion)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.order_by(ReconcileCheck.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_ser_check(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_check(db, check_id: int) -> ReconcileCheck | None:
    return db.get(ReconcileCheck, check_id)


def set_conclusion(db, user, check_id: int, conclusion: str | None = None,
                   note: str | None = None) -> ReconcileCheck:
    """补/改结论与备注(留痕: 记录复核人/时间)"""
    if conclusion and conclusion not in CONCLUSIONS:
        raise ValueError(f"conclusion 非法: {conclusion}, 应为 {CONCLUSIONS}")
    check = db.get(ReconcileCheck, check_id)
    if not check:
        raise ValueError("复核批次不存在")
    if conclusion is not None:
        check.conclusion = conclusion
    if note is not None:
        check.note = note
    check.reviewed_by = user.id if user else None
    check.reviewed_name = _user_display_name(user)
    check.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(check)
    return check


# ─────────────────────────────────────────────────────────────
# 序列化
# ─────────────────────────────────────────────────────────────
def _ser_check(r: ReconcileCheck) -> dict:
    return {
        "id": r.id,
        "check_no": r.check_no,
        "window_start": _to_cn(r.window_start).isoformat() if r.window_start else None,
        "window_end": _to_cn(r.window_end).isoformat() if r.window_end else None,
        "grain": r.grain,
        "route_snapshot": r.route_snapshot,
        "by_route_of_e": r.by_route_of_e,
        "dupe_detect": r.dupe_detect,
        "conclusion": r.conclusion,
        "note": r.note,
        "reviewed_by": r.reviewed_by,
        "reviewed_name": r.reviewed_name,
        "reviewed_at": _to_cn(r.reviewed_at).isoformat() if r.reviewed_at else None,
        "created_at": _to_cn(r.created_at).isoformat() if r.created_at else None,
    }
