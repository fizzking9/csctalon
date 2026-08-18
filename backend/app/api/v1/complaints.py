"""投诉统计路由(规划设计 §4.6 三源统一统计)

三源: 内部投诉(service_tickets) / 商户号投诉(service_tickets微信 + card_complaints卡商群) / 外部投诉(service_tickets平台 + external_complaints)。
复用 analytics 服务的口径 helper, 保证与既有看板一致。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import case, func, literal, or_, select

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.models.card_complaint import CardComplaint
from app.models.complaint_audit import ComplaintAudit
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.schemas.common import PageBase, R
from app.services.analytics import _complaint_source_type_case, _ticket_category_case

router = APIRouter(prefix="/complaints", tags=["投诉分析"])


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_dt_end(s: str | None) -> datetime | None:
    """end 边界解析: 若为纯日期(YYYY-MM-DD), 归一到当日 23:59:59.999999, 避免漏掉当天数据。"""
    if not s:
        return None
    s = s.strip()
    if len(s) == 10:
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
        except ValueError:
            return None
    return _parse_dt(s)


def _trend(rows, key="day"):
    return [{"day": str(r[0]), "count": int(r[1] or 0)} for r in rows]


@router.get("/summary", response_model=R, summary="三源投诉统计汇总")
def complaint_summary(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
):
    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    def _tk_where(stmt):
        stmt = stmt.where(_ticket_category_case() == "投诉")
        if ts:
            stmt = stmt.where(ServiceTicket.register_time >= ts)
        if te:
            stmt = stmt.where(ServiceTicket.register_time <= te)
        return stmt

    src_expr = _complaint_source_type_case()

    # ---- 内部投诉 (service_tickets) ----
    internal_total = db.scalar(_tk_where(select(func.count())).where(src_expr == "内部投诉")) or 0
    internal_by_cat = db.execute(
        _tk_where(select(ServiceTicket.service_item, func.count()).where(src_expr == "内部投诉"))
        .group_by(ServiceTicket.service_item)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    internal_by_handler = db.execute(
        _tk_where(select(ServiceTicket.handler, func.count()).where(src_expr == "内部投诉"))
        .where(ServiceTicket.handler.isnot(None))
        .group_by(ServiceTicket.handler)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    internal_trend = db.execute(
        _tk_where(select(func.date_trunc("day", ServiceTicket.register_time), func.count()).where(src_expr == "内部投诉"))
        .group_by(func.date_trunc("day", ServiceTicket.register_time))
        .order_by(func.date_trunc("day", ServiceTicket.register_time))
    ).all()

    # ---- 商户号投诉 (service_tickets 微信 + card_complaints) ----
    merchant_ticket = db.scalar(_tk_where(select(func.count())).where(src_expr == "商户号投诉")) or 0
    cc_stmt = select(func.count()).select_from(CardComplaint)
    if ts:
        cc_stmt = cc_stmt.where(CardComplaint.complaint_date >= ts)
    if te:
        cc_stmt = cc_stmt.where(CardComplaint.complaint_date <= te)
    cc_total = db.scalar(cc_stmt) or 0
    merchant_total = merchant_ticket + cc_total

    cc_by_cat = db.execute(
        select(CardComplaint.problem_category, func.count())
        .where(CardComplaint.problem_category.isnot(None))
        .group_by(CardComplaint.problem_category)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    merchant_by_cat = [{"name": r[0], "count": int(r[1] or 0)} for r in cc_by_cat]
    if merchant_ticket:
        merchant_by_cat.insert(0, {"name": "微信投诉(工单)", "count": int(merchant_ticket)})

    cc_by_handler = db.execute(
        select(CardComplaint.handler, func.count())
        .where(CardComplaint.handler.isnot(None))
        .group_by(CardComplaint.handler)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    merchant_by_handler = [{"name": r[0], "count": int(r[1] or 0)} for r in cc_by_handler]

    cc_trend = db.execute(
        select(func.date_trunc("day", CardComplaint.complaint_date), func.count())
        .group_by(func.date_trunc("day", CardComplaint.complaint_date))
        .order_by(func.date_trunc("day", CardComplaint.complaint_date))
    ).all()
    merchant_trend = _trend(cc_trend)

    # ---- 外部投诉 (service_tickets 平台 + external_complaints) ----
    external_ticket = db.scalar(_tk_where(select(func.count())).where(src_expr == "外部投诉")) or 0
    ec_stmt = select(func.count()).select_from(ExternalComplaint)
    if ts:
        ec_stmt = ec_stmt.where(ExternalComplaint.complaint_date >= ts)
    if te:
        ec_stmt = ec_stmt.where(ExternalComplaint.complaint_date <= te)
    ec_total = db.scalar(ec_stmt) or 0
    external_total = external_ticket + ec_total

    ec_by_cat = db.execute(
        select(ExternalComplaint.complaint_category, func.count())
        .where(ExternalComplaint.complaint_category.isnot(None))
        .group_by(ExternalComplaint.complaint_category)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    external_by_cat = [{"name": r[0], "count": int(r[1] or 0)} for r in ec_by_cat]
    if external_ticket:
        external_by_cat.insert(0, {"name": "平台投诉(工单)", "count": int(external_ticket)})

    ec_by_handler = db.execute(
        select(ExternalComplaint.follower, func.count())
        .where(ExternalComplaint.follower.isnot(None))
        .group_by(ExternalComplaint.follower)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    external_by_handler = [{"name": r[0], "count": int(r[1] or 0)} for r in ec_by_handler]

    ec_trend = db.execute(
        select(func.date_trunc("day", ExternalComplaint.complaint_date), func.count())
        .group_by(func.date_trunc("day", ExternalComplaint.complaint_date))
        .order_by(func.date_trunc("day", ExternalComplaint.complaint_date))
    ).all()
    external_trend = _trend(ec_trend)

    return R(
        data={
            "internal": {
                "total": int(internal_total),
                "by_category": [{"name": r[0] or "未标注", "count": int(r[1] or 0)} for r in internal_by_cat],
                "by_handler": [{"name": r[0], "count": int(r[1] or 0)} for r in internal_by_handler],
                "trend": _trend(internal_trend),
            },
            "merchant": {
                "total": int(merchant_total),
                "by_category": merchant_by_cat,
                "by_handler": merchant_by_handler,
                "trend": merchant_trend,
            },
            "external": {
                "total": int(external_total),
                "by_category": external_by_cat,
                "by_handler": external_by_handler,
                "trend": external_trend,
            },
        }
    )


# ---------- 投诉明细(三源统一) ----------


@router.get("/detail", response_model=R, summary="投诉明细列表(三源合并: 内部/商户号/外部)")
def complaint_detail(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="内部投诉/商户号投诉/外部投诉"),
    limit: int = Query(500, ge=1, le=2000),
):
    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    src_expr = _complaint_source_type_case()

    def _iso(dt):
        return dt.isoformat() if dt else None

    records: list[dict] = []

    # 1. 服务工单投诉 (内部/商户号/外部按 service_item 归一化)
    stmt = (
        select(
            ServiceTicket.ticket_no,
            ServiceTicket.device_sn,
            src_expr,
            ServiceTicket.service_item,
            ServiceTicket.remark,
            ServiceTicket.handler,
            ServiceTicket.register_time,
            ServiceTicket.status,
        )
        .where(_ticket_category_case() == "投诉")
    )
    if ts:
        stmt = stmt.where(ServiceTicket.register_time >= ts)
    if te:
        stmt = stmt.where(ServiceTicket.register_time <= te)
    if source_type:
        stmt = stmt.where(src_expr == source_type)
    for r in db.execute(stmt.order_by(ServiceTicket.register_time.desc().nulls_last())).all():
        records.append({
            "source": r[2] or "内部投诉",
            "no": r[0],
            "device_sn": r[1],
            "category": r[3],
            "problem": r[4],
            "handler": r[5],
            "time": _iso(r[6]),
            "status": r[7],
        })

    # 2. 外部投诉表 (监督局/315等平台投诉)
    if source_type is None or source_type == "外部投诉":
        ec_stmt = select(
            ExternalComplaint.complaint_no,
            ExternalComplaint.device_sn,
            ExternalComplaint.complaint_category,
            ExternalComplaint.problem,
            ExternalComplaint.follower,
            ExternalComplaint.complaint_date,
            ExternalComplaint.result,
        )
        if ts:
            ec_stmt = ec_stmt.where(ExternalComplaint.complaint_date >= ts)
        if te:
            ec_stmt = ec_stmt.where(ExternalComplaint.complaint_date <= te)
        for r in db.execute(ec_stmt.order_by(ExternalComplaint.complaint_date.desc().nulls_last())).all():
            records.append({
                "source": "外部投诉",
                "no": r[0],
                "device_sn": r[1],
                "category": r[2],
                "problem": r[3],
                "handler": r[4],
                "time": _iso(r[5]),
                "status": r[6],
            })

    # 3. 卡商群投诉 (商户号投诉)
    if source_type is None or source_type == "商户号投诉":
        cc_stmt = select(
            CardComplaint.id,
            CardComplaint.device_sn,
            CardComplaint.problem_category,
            CardComplaint.problem,
            CardComplaint.handler,
            CardComplaint.complaint_date,
            CardComplaint.result,
        )
        if ts:
            cc_stmt = cc_stmt.where(CardComplaint.complaint_date >= ts)
        if te:
            cc_stmt = cc_stmt.where(CardComplaint.complaint_date <= te)
        for r in db.execute(cc_stmt.order_by(CardComplaint.complaint_date.desc().nulls_last())).all():
            records.append({
                "source": "商户号投诉",
                "no": str(r[0]) if r[0] is not None else None,
                "device_sn": r[1],
                "category": r[2],
                "problem": r[3],
                "handler": r[4],
                "time": _iso(r[5]),
                "status": r[6],
            })

    # 合并后按时间降序
    records.sort(key=lambda x: x["time"] or "", reverse=True)
    return R(data={"items": records[:limit], "total": len(records)})


# ---------- 单个投诉审计(规划设计 §4.6) ----------


@router.get("/audits", response_model=R[PageBase], summary="投诉审计列表")
def list_audits(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source_type: str | None = None,
):
    stmt = select(ComplaintAudit)
    if source_type:
        stmt = stmt.where(ComplaintAudit.source_type == source_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(ComplaintAudit.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return R(
        data=PageBase(
            items=[
                {
                    "id": a.id,
                    "source_type": a.source_type,
                    "ref_id": a.ref_id,
                    "device_sn": a.device_sn,
                    "involved_persons": a.involved_persons,
                    "attribution": a.attribution,
                    "conclusion": a.conclusion,
                    "judgment": a.judgment,
                    "attachments": a.attachments,
                    "audited_by_name": a.audited_by_name,
                    "audited_at": a.audited_at.isoformat() if a.audited_at else None,
                }
                for a in items
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/audits", response_model=R, summary="新增投诉审计结论")
def create_audit(payload: dict, db: DBSession, current_user: ManagerUser):
    a = ComplaintAudit(
        source_type=payload.get("source_type", "internal"),
        ref_id=payload.get("ref_id"),
        device_sn=payload.get("device_sn"),
        involved_persons=payload.get("involved_persons"),
        attribution=payload.get("attribution"),
        conclusion=payload.get("conclusion"),
        judgment=payload.get("judgment"),
        attachments=payload.get("attachments"),
        audited_by=current_user.id,
        audited_by_name=current_user.display_name or current_user.username,
        audited_at=datetime.now(timezone.utc),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return R(data={"id": a.id, "conclusion": a.conclusion, "attribution": a.attribution})
