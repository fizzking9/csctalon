"""外部只读开放平台路由 (docs/arch/A-20260902-021 LOCKED v1.0)

对外只读接口(供 workbuddy 等外部工具用 token+scope 拉取经营数据), 与内部 JWT 体系隔离:
- 鉴权: Authorization: Bearer <token>, token 由管理员在"系统设置/外部接入"生成, 库中存 SHA-256 hash。
- 只读端点: reception / refund / complaint(单日, date 缺省今天) + brief(period 日/周/月)。
- 管理端点(内部 JWT AdminUser): token CRUD(生成仅显一次明文/吊销/改 scope/限频)。
- 限频: Redis 固定窗口(每 token 可配 rate_limit_per_min, 否则全局默认)。
- 审计: 每次外部拉取写 system_events(category=external)。

金额单位: 只读透传既有聚合, 单位=元(Numeric(12,2), 2位小数), 不做分/元换算。
所有统计口径复用 analytics / complaints 模块既有实现, 不重复造轮子。
"""

import calendar
import datetime as _dt
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, DBSession
from app.core.config import settings
from app.core.redis_client import cached_call
from app.models.external_token import ExternalAccessToken
from app.models.person import Person
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.ticket import ServiceTicket
from app.schemas.common import R
from app.schemas.external_token import (
    ExternalTokenCreate,
    ExternalTokenOut,
    ExternalTokenUpdate,
)
from app.services import analytics as A
from app.services import external_service as S

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external", tags=["外部只读开放平台"])

TZ_CN = _dt.timezone(_dt.timedelta(hours=8))

# 投诉工单"已完成"状态集合(与 complaints.complaint_summary 一致)
_DONE_STATUS = ("已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止")


def _now_cn() -> datetime:
    return datetime.now(TZ_CN)


# ─────────────────────────────────────────────────────────────────────
# 鉴权
# ─────────────────────────────────────────────────────────────────────
def _auth(db: Session, authorization: str | None) -> ExternalAccessToken:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    row = S.authenticate(db, token)
    S.check_rate_limit(row)
    S.touch_last_used(db, row)
    return row


def _src_case():
    """三源投诉分类 CASE(与 analytics._complaint_source_type_case 一致)"""
    return A._complaint_source_type_case()


def _cat_case():
    """投诉类工单判定(与 analytics._ticket_category_case 一致)"""
    return A._ticket_category_case()


# ─────────────────────────────────────────────────────────────────────
# 对外只读: 三核心单日端点
# ─────────────────────────────────────────────────────────────────────
def _kpi(db: Session, date: str | None):
    return cached_call(f"today:kpi:{date}:None:None", 60, A.today_kpi, db, date, None, None)


@router.get("/reception", response_model=R, summary="外部-当日/指定日接待量")
def external_reception(
    db: DBSession,
    date: str | None = Query(None, description="YYYY-MM-DD, 缺省=今天(北京时间)"),
    authorization: Annotated[str | None, Header()] = None,
):
    row = _auth(db, authorization)
    S.require_scope(row, "reception")
    S.record_external_access(db, row, "reception")
    kpi = _kpi(db, date)
    return R(data={
        "date": kpi.get("date"),
        "generated_at": _now_cn().isoformat(),
        "reception": kpi.get("reception"),
        "_spec": {"reception": "接待量=客服处理记录数(cs_record.operation_time 落当日); device_count=去重设备SN数"},
    })


@router.get("/refund", response_model=R, summary="外部-当日/指定日退款")
def external_refund(
    db: DBSession,
    date: str | None = Query(None, description="YYYY-MM-DD, 缺省=今天(北京时间)"),
    authorization: Annotated[str | None, Header()] = None,
):
    row = _auth(db, authorization)
    S.require_scope(row, "refund")
    S.record_external_access(db, row, "refund")
    kpi = _kpi(db, date)
    return R(data={
        "date": kpi.get("date"),
        "generated_at": _now_cn().isoformat(),
        "refund_count": kpi.get("refund_count"),
        "refund_amount": kpi.get("refund_amount"),
        "refund_done_today": kpi.get("refund_done_today"),
        "actual_cashflow": kpi.get("actual_cashflow"),
        "_spec": {
            "refund_count": "退款笔数=四路经营合计 A线上直退+B钱包取现+C闪猫+D手动 (按创建时间)",
            "refund_amount": "退款金额=四路经营合计, 单位=元, 2位小数",
            "refund_done_today": "当日完成退款=路线A status=已退款 且更新时间∈当日",
            "actual_cashflow": "真实现金流E=merchant_refunds SUCCESS合计(对账基准), 元",
        },
    })


@router.get("/complaint", response_model=R, summary="外部-当日/指定日投诉")
def external_complaint(
    db: DBSession,
    date: str | None = Query(None, description="YYYY-MM-DD, 缺省=今天(北京时间)"),
    authorization: Annotated[str | None, Header()] = None,
):
    row = _auth(db, authorization)
    S.require_scope(row, "complaint")
    S.record_external_access(db, row, "complaint")
    kpi = _kpi(db, date)
    return R(data={
        "date": kpi.get("date"),
        "generated_at": _now_cn().isoformat(),
        "complaint": kpi.get("complaint"),
        "_spec": {"complaint": "投诉量=service_tickets投诉 + external_complaints + card_complaints 三表并集(落当日)"},
    })


# ─────────────────────────────────────────────────────────────────────
# 对外只读: 一键经营简报 brief (period 日/周/月)
# ─────────────────────────────────────────────────────────────────────
@router.get("/brief", response_model=R, summary="外部-一键经营简报(日/周/月)")
def external_brief(
    db: DBSession,
    period: str = Query("daily", pattern="^(daily|weekly|monthly)$", description="daily/weekly/monthly"),
    date: str | None = Query(None, description="period=daily 时指定某整天 YYYY-MM-DD(缺省=今天实时)"),
    authorization: Annotated[str | None, Header()] = None,
):
    row = _auth(db, authorization)
    S.require_scope(row, "brief")
    S.record_external_access(db, row, f"brief:{period}")
    return R(data=_build_brief(db, period, date))


def _window(period: str, date: str | None) -> dict:
    """period/date → 时间窗口(架构 §2.2 口径)

    daily=当日0点起(实时), date 给定则该整天; weekly=整周(周一起); monthly=整月(未满月则1日到当前)。
    返回 start/end(YYYY-MM-DD, 供 analytics 按北京时区解释)。
    """
    now = _now_cn()
    today = now.date()
    if period == "daily":
        d = _dt.date.fromisoformat(date) if date else today
        is_realtime = (d == today)
        return {
            "period": "daily", "date": d.isoformat(),
            "date_start": f"{d.isoformat()}T00:00:00+08:00",
            "date_end": now.isoformat() if is_realtime else f"{d.isoformat()}T23:59:59+08:00",
            "is_realtime": is_realtime, "start": d.isoformat(), "end": d.isoformat(),
        }
    if period == "weekly":
        monday = today - _dt.timedelta(days=today.weekday())
        return {
            "period": "weekly",
            "date_start": f"{monday.isoformat()}T00:00:00+08:00",
            "date_end": now.isoformat(), "is_realtime": True,
            "start": monday.isoformat(), "end": today.isoformat(),
        }
    month_start = today.replace(day=1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    is_realtime = today.day != last_day
    return {
        "period": "monthly",
        "date_start": f"{month_start.isoformat()}T00:00:00+08:00",
        "date_end": now.isoformat() if is_realtime else f"{today.isoformat()}T23:59:59+08:00",
        "is_realtime": is_realtime,
        "start": month_start.isoformat(), "end": today.isoformat(),
    }


def _build_brief(db: Session, period: str, date: str | None) -> dict:
    win = _window(period, date)
    start_s, end_s = win["start"], win["end"]
    # KPI/退款四路/现金流: 复用 today_kpi(内部 _resolve_windows 已按北京时区解释 start/end;
    #   end==今天→实时截止 now; 否则整天; cash_routes_today 即同窗口四路+E)
    kpi = cached_call(f"today:kpi:None:{start_s}:{end_s}", 60, A.today_kpi, db, None, start_s, end_s)
    cash = kpi.get("cash_routes_today") or {}
    # 涉诉率: decision_overview 同窗口(start/end 字符串, 内部自行处理北京时区)
    dec = A.refund_decision_overview(db, start=start_s, end=end_s)
    summary = dec.get("summary", {}) or {}
    # 投诉增强(三源×registered/handled/backlog)
    complaint = _complaint_enhanced(db, start_s, end_s)
    # 接待专项增强(名单+会话+设备)
    reception_spec = _reception_specialty(db, start_s, end_s)
    return {
        "period": win["period"],
        "date": win.get("date"),
        "date_start": win["date_start"],
        "date_end": win["date_end"],
        "generated_at": _now_cn().isoformat(),
        "reception": {"total": kpi.get("reception"), "by_specialty": reception_spec},
        "complaint": complaint,
        "refund": {
            "total_amount": kpi.get("refund_amount"),
            "total_count": kpi.get("refund_count"),
            "routes": {
                "route_a": cash.get("route_a"),
                "route_b": cash.get("route_b"),
                "route_c": cash.get("route_c"),
                "route_d": cash.get("route_d"),
            },
            "actual_cashflow_e": cash.get("actual_cashflow") or kpi.get("actual_cashflow"),
            "a_route": {
                "litigation_ratio": {"value": summary.get("complaint_ratio"),
                                     "yoy_value": None, "yoy_pct": None},
                "litigation_amount": summary.get("complaint_amount"),
            },
        },
        "_spec": _spec_brief(),
    }


# ─────────────────────────────────────────────────────────────────────
# 投诉增强: 三源(内部/商户号/外部) × registered/handled/backlog
# ─────────────────────────────────────────────────────────────────────
def _complaint_enhanced(db: Session, start_s: str, end_s: str) -> dict:
    """三源 × 登记/完成/积压。

    口径(与 complaints.complaint_summary/backlog 一致):
      registered = 窗口内登记的投诉工单(register_time∈窗口) + 315/卡商补充表(complaint_date∈窗口, 并入外部)
      handled    = 窗口内真实完成的投诉工单(finish_time∈窗口 且 非"同瞬填充")
      backlog    = 当前未闭环存量: 窗口内登记且归一化状态非已完成(工单)
    """
    # 北京时区窗口 → naive UTC 边界(timestamptz 列比较正确性, 与 analytics 一致)
    ts, te = S.parse_bj_window(start_s, end_s)
    src = _src_case()
    cat = _cat_case()
    sources = ["内部投诉", "商户号投诉", "外部投诉"]
    by_source = []

    def _reg_conds(src_cond):
        conds = [cat == "投诉", src_cond, ServiceTicket.register_time.isnot(None)]
        if ts:
            conds.append(ServiceTicket.register_time >= ts)
        if te:
            conds.append(ServiceTicket.register_time <= te)
        return conds

    for source in sources:
        src_cond = src == source
        # registered: 窗口内登记(register_time)
        reg = db.scalar(
            select(func.count()).select_from(ServiceTicket).where(*_reg_conds(src_cond))
        ) or 0
        # handled: 窗口内真实完成(finish_time 落窗口且晚于登记, 排除同瞬填充)
        fin_conds = [cat == "投诉", src_cond, ServiceTicket.finish_time.isnot(None),
                     ServiceTicket.finish_time > ServiceTicket.register_time]
        if ts:
            fin_conds.append(ServiceTicket.finish_time >= ts)
        if te:
            fin_conds.append(ServiceTicket.finish_time <= te)
        fin = db.scalar(select(func.count()).select_from(ServiceTicket).where(*fin_conds)) or 0
        # backlog: 当前未闭环(状态非已完成)且窗口内登记
        bl_conds = _reg_conds(src_cond) + [
            or_(ServiceTicket.status.is_(None), ServiceTicket.status == "",
                ~ServiceTicket.status.in_(_DONE_STATUS))
        ]
        bl = db.scalar(select(func.count()).select_from(ServiceTicket).where(*bl_conds)) or 0
        by_source.append({"source": source, "registered": int(reg), "handled": int(fin), "backlog": int(bl)})

    # 补充表(315/卡商) → 外部投诉 registered(complaint_date 落窗口)
    from app.models.card_complaint import CardComplaint
    from app.models.external_complaint import ExternalComplaint
    ext_reg = 0
    for model in (ExternalComplaint, CardComplaint):
        conds = [model.complaint_date.isnot(None)]
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        ext_reg += db.scalar(select(func.count()).select_from(model).where(*conds)) or 0
    # 外部投诉桶并入补充表登记
    for item in by_source:
        if item["source"] == "外部投诉":
            item["registered"] = int(item["registered"]) + int(ext_reg)

    total = {
        "registered": sum(int(x["registered"]) for x in by_source),
        "handled": sum(int(x["handled"]) for x in by_source),
        "backlog": sum(int(x["backlog"]) for x in by_source),
    }
    return {"total": total, "by_source": by_source}


# ─────────────────────────────────────────────────────────────────────
# 接待专项增强: 各正式专项 名单 + 会话数 + 设备数
# ─────────────────────────────────────────────────────────────────────
def _reception_specialty(db: Session, start_s: str, end_s: str) -> list[dict]:
    """名单=组织分配(person_specialty_assignments.is_current); 会话/设备=内容分桶(today_staff_efficiency)。"""
    specs = list(db.scalars(
        select(Specialty).where(Specialty.is_active.is_(True)).order_by(Specialty.display_order, Specialty.name)
    ))
    if not specs:
        return []
    eff = A.today_staff_efficiency(db, start=start_s, end=end_s, aggregate_by="person", topn=300)
    person_rows = {r.get("name"): r for r in (eff.get("data") or []) if not r.get("is_virtual")}
    BUCKET = {
        "咨询接待": ("sp_zixun", "pp_zixun"),
        "套餐专项": ("sp_taocan", "pp_taocan"),
        "退款挽回": ("sp_recovery", "pp_recovery"),
        "退款处理": ("sp_processing", "pp_processing"),
        "内部投诉": ("sp_internal", "pp_internal"),
        "外部投诉": ("sp_external", "pp_external"),
    }
    result = []
    for s in specs:
        assignments = list(db.scalars(
            select(PersonSpecialtyAssignment).where(
                PersonSpecialtyAssignment.specialty_id == s.id,
                PersonSpecialtyAssignment.is_current.is_(True),
            )
        ))
        pids = [a.person_id for a in assignments]
        persons = {}
        if pids:
            persons = {p.id: p for p in db.scalars(select(Person).where(Person.id.in_(pids))).all()}
        members = []
        sessions = 0
        devices = 0
        for a in assignments:
            p = persons.get(a.person_id)
            if p is None:
                continue
            members.append({
                "name": p.display_name,
                "position": p.position,
                "group_name": p.group_name,
                "is_primary": a.is_primary,
                "workload_ratio": a.workload_ratio,
            })
            sp_field, pp_field = BUCKET.get(s.name, BUCKET["咨询接待"])
            rec = person_rows.get(p.display_name) or {}
            sessions += int(rec.get(sp_field) or 0)
            devices += int(rec.get(pp_field) or 0)
        lead = None
        if s.current_lead:
            lead = {"name": s.current_lead.display_name, "role": "lead"}
        elif s.current_deputy_lead:
            lead = {"name": s.current_deputy_lead.display_name, "role": "deputy"}
        result.append({
            "specialty": s.name,
            "is_temporary": s.is_temporary,
            "lead": lead,
            "members": members,
            "sessions": {"value": sessions},
            "devices": {"value": devices},
        })
    return result


def _spec_brief() -> dict:
    return {
        "money_unit": "金额单位=元(Numeric(12,2), 2位小数)",
        "period": "daily=当日0点起; weekly=整周(周一起); monthly=整月(未满月则1日到当前)",
        "litigation_ratio": "A路直退涉诉率=涉诉金额/A路(线上直退)金额×100; 涉诉=SN关联投诉工单(外部>商户号>内部) OR 备注含投诉标识; 清退不算",
        "complaint": "registered=窗口登记; handled=窗口完成(有效finish); backlog=当前未闭环(状态非已完成)",
    }


# ─────────────────────────────────────────────────────────────────────
# 管理端点(内部 JWT AdminUser): token CRUD
# ─────────────────────────────────────────────────────────────────────
@router.get("/tokens", response_model=R, summary="外部只读 token 列表(内部)")
def list_tokens(db: DBSession, _: AdminUser):
    rows = db.scalars(select(ExternalAccessToken).order_by(ExternalAccessToken.id.desc())).all()
    return R(data=[ExternalTokenOut.model_validate(t) for t in rows])


@router.post("/tokens", response_model=R, summary="生成外部只读 token(明文仅此一次, 内部)")
def create_token(payload: ExternalTokenCreate, db: DBSession, me: AdminUser):
    raw, row = S.generate_token(
        db, payload.name, payload.scopes,
        rate_limit_per_min=payload.rate_limit_per_min,
        expires_at=payload.expires_at,
        created_by=me.username,
    )
    return R(data={
        "id": row.id,
        "name": row.name,
        "token": raw,  # 明文仅此一次
        "token_prefix": row.token_prefix,
        "scopes": row.scopes,
        "rate_limit_per_min": row.rate_limit_per_min,
        "expires_at": row.expires_at,
    })


@router.patch("/tokens/{token_id}", response_model=R, summary="更新外部只读 token(内部)")
def update_token(token_id: int, payload: ExternalTokenUpdate, db: DBSession, _: AdminUser):
    row = db.get(ExternalAccessToken, token_id)
    if not row:
        raise HTTPException(404, "token 不存在")
    if payload.name is not None:
        row.name = payload.name[:64]
    if payload.scopes is not None:
        invalid = [s for s in payload.scopes if s not in S.SCOPES]
        if invalid:
            raise HTTPException(400, f"非法 scope: {', '.join(invalid)}")
        row.scopes = list(dict.fromkeys(payload.scopes))
    if payload.rate_limit_per_min is not None:
        row.rate_limit_per_min = payload.rate_limit_per_min
    if payload.enabled is not None:
        row.enabled = payload.enabled
    db.commit()
    return R(data=ExternalTokenOut.model_validate(row))


@router.post("/tokens/{token_id}/revoke", response_model=R, summary="吊销外部只读 token(内部)")
def revoke_token(token_id: int, db: DBSession, me: AdminUser):
    ok = S.revoke_token(db, token_id, revoked_by=me.username)
    if not ok:
        raise HTTPException(404, "token 不存在")
    return R(data={"ok": True})


@router.get("/config", response_model=R, summary="外部只读接口启用状态(内部)")
def external_config(db: DBSession, _: AdminUser):
    return R(data={
        "enabled": settings.EXTERNAL_API_ENABLED,
        "default_rate_limit_per_min": settings.EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN,
        "scopes": list(S.SCOPES),
    })
