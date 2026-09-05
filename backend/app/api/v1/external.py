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
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, DBSession
from app.api.v1.complaints import _BACKLOG_DONE_SET, _external_done_sns
from app.core.config import settings
from app.core.redis_client import cache_ttl_for, cached_call
from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint
from app.models.external_token import ExternalAccessToken
from app.models.person import Person
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.ticket import ServiceTicket
from app.models.user import User
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

# 投诉"已完成"状态集合 — 单一来源: 复用 complaints._BACKLOG_DONE_SET(避免两处集合漂移)
_DONE_STATUS = _BACKLOG_DONE_SET

# 归一化后可能判为"已完成"的非完成态集合(与 complaints._norm_status 语义一致, 2026-08-25):
#   处理中类 / 待处理类状态 + 空状态, 若存在真实完成(finish>=register+3min) → _norm_status 判为已完成 → 不进积压。
_NORM_DONE_CANDIDATE_STATUS = {
    # 处理中类
    "处理中", "跟进中", "跟进", "进行中",
    # 待处理类
    "待处理", "未处理", "待分派", "pending", "待跟进",
}

# 真实完成判定 = finish_time 晚于 register_time ≥ 3 分钟(SQL 层表达, 与 complaints._is_real_finish 一致)
_REAL_FINISH_INTERVAL_SECS = 180


def _now_cn() -> datetime:
    return datetime.now(TZ_CN)


def _valid_date_or_400(date: str | None) -> str | None:
    """校验 date 参数(YYYY-MM-DD)。非法 → 400(架构 §2.1/§八: 非法格式→400)。

    date 仅接受"纯日期 YYYY-MM-DD"(单日/窗口起点由 _resolve_windows 按北京时区解释);
    传 None 或非法格式统一抛 400, 避免落入聚合层 ValueError → 500。
    """
    if date is None:
        return None
    d = (date or "").strip()
    try:
        _dt.date.fromisoformat(d)
    except (ValueError, TypeError):
        raise HTTPException(400, f"date 参数非法: {date!r}, 应为 YYYY-MM-DD")
    return d


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
    date = _valid_date_or_400(date)
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
    date = _valid_date_or_400(date)
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
            "actual_cashflow": "真实现金流E=refund_record_reports 退款成功合计(对账基准, 2026-09-05 v1.1 起替代 merchant_refunds), 元",
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
    date = _valid_date_or_400(date)
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
    返回 start/end(YYYY-MM-DD, 供 analytics 按北京时区解释); grain 供 refund_decision_overview(daily→day, weekly/monthly→week)。
    """
    now = _now_cn()
    today = now.date()
    if period == "daily":
        d = _valid_date_or_400(date)
        d = _dt.date.fromisoformat(d) if d else today
        is_realtime = (d == today)
        return {
            "period": "daily", "date": d.isoformat(), "grain": "day",
            "date_start": f"{d.isoformat()}T00:00:00+08:00",
            "date_end": now.isoformat() if is_realtime else f"{d.isoformat()}T23:59:59+08:00",
            "is_realtime": is_realtime, "start": d.isoformat(), "end": d.isoformat(),
        }
    if period == "weekly":
        monday = today - _dt.timedelta(days=today.weekday())
        return {
            "period": "weekly", "grain": "week",
            "date_start": f"{monday.isoformat()}T00:00:00+08:00",
            "date_end": now.isoformat(), "is_realtime": True,
            "start": monday.isoformat(), "end": today.isoformat(),
        }
    month_start = today.replace(day=1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    is_realtime = today.day != last_day
    return {
        "period": "monthly", "grain": "week",
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
    # 涉诉率/四路涉诉金额: refund_decision_overview 同窗口(start/end 字符串), grain 按架构 §2.2 映射
    #   (daily→day, weekly/monthly→week); 走 cached_call 复用内部 refund:do:* 缓存(架构 §5.4)
    _do_key = f"refund:do:{start_s}:{end_s}:{win['grain']}"
    dec = cached_call(_do_key, cache_ttl_for(start_s, end_s, 60), A.refund_decision_overview, db, start_s, end_s, grain=win["grain"])
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
    """三源 × 登记/完成/积压(口径与 complaints.complaint_summary/backlog 严格一致)。

      registered = 窗口内登记的投诉工单(register_time∈窗口)
                   + 315/卡商补充表净新增(complaint_date∈窗口, 减 matched 到窗口内工单的重叠, 并入外部)
      handled    = 窗口内真实完成的投诉工单(finish_time∈窗口 且 finish >= register+3min,
                   即 complaints._is_real_finish 语义; 排除同瞬填充/批量覆盖)
      backlog    = 窗口内登记且"归一化后未闭环"的存量:
                   状态非已完成 且 非(状态∈处理中/待处理/空 但有真实完成≥3min → _norm_status=已完成)
                   且 非(外部投诉工单 SN 命中 315 已办结登记 → 视为已处理, _external_done_sns)
    """
    # 北京时区窗口 → naive UTC 边界(timestamptz 列比较正确性, 与 analytics 一致)
    ts, te = S.parse_bj_window(start_s, end_s)
    src = _src_case()
    cat = _cat_case()
    sources = ["内部投诉", "商户号投诉", "外部投诉"]
    # 真实完成(SQL 表达 = complaints._is_real_finish 的 ≥3min): epoch(finish - register) >= 180s
    #   注: 不能用 func.make_interval(secs=...) — SQLAlchemy 会把关键字当函数参数名导致编译 TypeError
    real_finish = ServiceTicket.finish_time.isnot(None) & ServiceTicket.register_time.isnot(None) & (
        func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= _REAL_FINISH_INTERVAL_SECS
    )
    by_source = []

    def _reg_conds(src_cond):
        conds = [cat == "投诉", src_cond, ServiceTicket.register_time.isnot(None)]
        if ts:
            conds.append(ServiceTicket.register_time >= ts)
        if te:
            conds.append(ServiceTicket.register_time <= te)
        return conds

    def _fin_conds(src_cond):
        conds = [cat == "投诉", src_cond, real_finish]
        if ts:
            conds.append(ServiceTicket.finish_time >= ts)
        if te:
            conds.append(ServiceTicket.finish_time <= te)
        return conds

    for source in sources:
        src_cond = src == source
        # registered: 窗口内登记(register_time)
        reg = db.scalar(
            select(func.count()).select_from(ServiceTicket).where(*_reg_conds(src_cond))
        ) or 0
        # handled: 窗口内真实完成(finish 落窗口 且 finish >= register+3min, 对齐 _is_real_finish)
        fin = db.scalar(
            select(func.count()).select_from(ServiceTicket).where(*_fin_conds(src_cond))
        ) or 0
        # backlog: 窗口内登记 且 归一化后未闭环
        #   ① 状态非已完成(含空)
        #   ② 状态∈{处理中/待处理/空} 但已有真实完成(≥3min) → _norm_status=已完成 → 剔除
        #   ③ 外部投诉工单 SN 命中 315 已办结登记(ext_done_sns) → 视为已处理 → 剔除
        bl_conds = _reg_conds(src_cond) + [
            or_(ServiceTicket.status.is_(None), ServiceTicket.status == "",
                ~ServiceTicket.status.in_(_DONE_STATUS)),
            ~and_(
                real_finish,
                or_(
                    ServiceTicket.status.is_(None),
                    ServiceTicket.status == "",
                    ServiceTicket.status.in_(_NORM_DONE_CANDIDATE_STATUS),
                ),
            ),
        ]
        if source == "外部投诉":
            # 315 登记已办结的 SN 对应的外部投诉工单(状态未同步)不进积压 — 与 complaints._query_backlog_rows 一致
            ext_done = _external_done_sns(db)
            if ext_done:
                bl_conds.append(or_(
                    ServiceTicket.device_sn.is_(None),
                    ~ServiceTicket.device_sn.in_(ext_done),
                ))
        bl = db.scalar(select(func.count()).select_from(ServiceTicket).where(*bl_conds)) or 0
        by_source.append({"source": source, "registered": int(reg), "handled": int(fin), "backlog": int(bl)})

    # 补充表(315/卡商) → 外部投诉 registered: 净新增 = 窗口内补充记录 - 已 matched 到"窗口内登记工单"的重叠
    #   (与 complaints.complaint_summary 的 total - overlap 同语义; 避免同一投诉在工单+补充表双计)
    def _suppl_overlap(model) -> int:
        """补充记录 matched_ticket_no 指向"窗口内登记的投诉工单"的条数(= 工单侧已计入 registered 的部分)"""
        sub_conds = [cat == "投诉", ServiceTicket.register_time.isnot(None)]
        if ts:
            sub_conds.append(ServiceTicket.register_time >= ts)
        if te:
            sub_conds.append(ServiceTicket.register_time <= te)
        sub = select(ServiceTicket.ticket_no).where(*sub_conds)
        conds = [model.complaint_date.isnot(None), model.matched_ticket_no.isnot(None),
                 model.matched_ticket_no.in_(sub)]
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        return db.scalar(select(func.count()).select_from(model).where(*conds)) or 0

    ext_reg = 0
    for model in (ExternalComplaint, CardComplaint):
        conds = [model.complaint_date.isnot(None)]
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        suppl_total = db.scalar(select(func.count()).select_from(model).where(*conds)) or 0
        ext_reg += int(suppl_total) - _suppl_overlap(model)
    # 外部投诉桶并入补充表净新增(不为负)
    for item in by_source:
        if item["source"] == "外部投诉":
            item["registered"] = int(item["registered"]) + max(int(ext_reg), 0)

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
        # 组织分配名单(person_specialty_assignments.is_current, 含主/辅专项)
        # ⚠️ 桶映射: BUCKET 键=6 个正式专项名(与 today_staff_efficiency 的 sp_*/pp_* 内容桶同名)。
        #   临时专项/名称不匹配的专项会兜底到"咨询接待"桶——与架构 §7.4 风险4 的"unassigned 兜底"理想态
        #   存在偏差; 当前 DB 无临时专项(6 个正式专项名完全匹配), 故维持现状, 若引入临时专项需改为内容桶映射。
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
        "complaint": ("registered=窗口内新登记(工单按 register_time; 315/卡商补充表按 complaint_date 并"
                        "入外部桶, 已 matched 到窗口内工单的去重); handled=窗口内真实完成(finish_time∈窗口 且"
                        "finish 晚于登记 ≥3min, 排除同瞬填充); backlog=窗口内登记且归一化后未闭环(状态非已完成,"
                        "排除有真实完成≥3min 的待处理; 315 已办结 SN 外部投诉视为已处理)"),
    }


# ─────────────────────────────────────────────────────────────────────
# 管理端点(内部 JWT AdminUser): token CRUD
# ─────────────────────────────────────────────────────────────────────
@router.get("/tokens", response_model=R, summary="外部只读 token 列表(内部)")
def list_tokens(db: DBSession, _: AdminUser):
    rows = db.scalars(select(ExternalAccessToken).order_by(ExternalAccessToken.id.desc())).all()
    # F-P2-5: created_by(username) → display_name 映射(创建人可读姓名), 找不到则保留 username
    unames = {t.created_by for t in rows if t.created_by}
    disp_map: dict[str, str] = {}
    if unames:
        for u in db.scalars(select(User).where(User.username.in_(unames))).all():
            disp_map[u.username] = u.display_name or u.qw_name or u.username
    items = []
    for t in rows:
        d = ExternalTokenOut.model_validate(t)
        # F-P2-5: created_by(username) → display_name; pydantic 用 model_copy 规范更新
        d = d.model_copy(update={"created_by_display": disp_map.get(t.created_by) if t.created_by else None})
        items.append(d)
    return R(data=items)


@router.post("/tokens", response_model=R, summary="生成外部只读 token(明文仅此一次, 内部)")
def create_token(payload: ExternalTokenCreate, db: DBSession, me: AdminUser):
    raw, row = S.generate_token(
        db, payload.name, payload.scopes,
        rate_limit_per_min=payload.rate_limit_per_min,
        expires_at=payload.expires_at,
        owner_note=payload.owner_note,
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
        "owner_note": row.owner_note,
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
    if payload.owner_note is not None:
        row.owner_note = payload.owner_note[:255]
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
