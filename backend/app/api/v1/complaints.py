"""投诉统计路由(规划设计 §4.6 三源统一统计)

三源: 内部投诉(service_tickets) / 商户号投诉(service_tickets微信 + card_complaints卡商群) / 外部投诉(service_tickets平台 + external_complaints)。
复用 analytics 服务的口径 helper, 保证与既有看板一致。
"""

from datetime import datetime, timezone, timedelta as _timedelta

from fastapi import APIRouter, Query
from sqlalchemy import case, func, or_, select, and_

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.models.card_complaint import CardComplaint
from app.models.complaint_audit import ComplaintAudit
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.schemas.common import PageBase, R
from app.services.analytics import _complaint_source_type_case, _ticket_category_case, TZ

router = APIRouter(prefix="/complaints", tags=["投诉分析"])


def _parse_dt(s: str | None) -> datetime | None:
    """解析时间字符串为 naive UTC datetime (用于与timestamptz列比较).
    - YYYY-MM-DD → 北京时间00:00 → 转naive UTC
    - ISO含时区 → 直接转UTC后去tz
    - ISO不含时区 → 视为北京时间,转UTC
    """
    if not s:
        return None
    s = s.strip()
    try:
        if len(s) == 10:
            # 纯日期 YYYY-MM-DD: 视为北京时间00:00
            d = datetime.strptime(s, "%Y-%m-%d")
            dt_sh = d.replace(tzinfo=TZ)
            return dt_sh.astimezone(timezone.utc).replace(tzinfo=None)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # 无时区信息,视为北京时间
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _parse_dt_end(s: str | None) -> datetime | None:
    """end 边界解析: 若为纯日期(YYYY-MM-DD), 归一到北京时间当日 23:59:59.999999 → 转naive UTC."""
    if not s:
        return None
    s = s.strip()
    if len(s) == 10:
        try:
            d = datetime.strptime(s, "%Y-%m-%d")
            dt_sh = d.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=TZ)
            return dt_sh.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return None
    return _parse_dt(s)


def _trend(rows, key="day"):
    return [{"day": str(r[0]), "count": int(r[1] or 0)} for r in rows]


def _day_trunc(col):
    """按 Asia/Shanghai 时区截断到日, 避免 UTC 凌晨被算到前一天.
    与 analytics.py 的 func.date_trunc("day", func.timezone("Asia/Shanghai", col)) 同口径.
    所有投诉日期字段(register_time/complaint_date)都是 timestamptz,
    直接 date_trunc('day', col) 会按 UTC 截断, 导致 CST 00:00~07:59 的记录落到前一天。"""
    return func.date_trunc("day", func.timezone("Asia/Shanghai", col))


def _handler_fallback_cols():
    """工单处理人多级fallback: handler(已指派并填写) → assignee(派单分配) → updated_by_raw(上游最后操作人) → registrant(登记人)
    场景: 历史工单 handler 字段未规范化填写, 但实际上有最后操作人 updated_by_raw 或 assignee/registrant
    仅用于统计汇总与展示, 不改变 DB 原始值。"""
    return case(
        (ServiceTicket.handler.isnot(None) & (ServiceTicket.handler != ""), ServiceTicket.handler),
        (ServiceTicket.assignee.isnot(None) & (ServiceTicket.assignee != ""), ServiceTicket.assignee),
        (ServiceTicket.updated_by_raw.isnot(None) & (ServiceTicket.updated_by_raw != ""), ServiceTicket.updated_by_raw),
        else_=ServiceTicket.registrant,
    )


# 系统自动登记人关键词(非真实处理人)
_SYS_HANDLERS = {"wechat", "sys", "客户", "customer", "system", "sns", ""}


def _has_real_handler(handler_val: str | None) -> bool:
    """判断是否有真实处理人(排除系统自动登记值 wechat/sys/客户 等)."""
    if not handler_val:
        return False
    v = handler_val.strip().lower()
    if not v:
        return False
    if v in _SYS_HANDLERS:
        return False
    return True


def _is_real_finish(finish_time, register_time) -> bool:
    """判断finish_time是否是真实完成时间.
    系统创建工单时会将finish_time默认设置为register_time(或相差几秒),
    只有当finish_time明显晚于register_time(>=3分钟)时才认为真实完成."""
    if finish_time is None or register_time is None:
        return False
    try:
        delta = finish_time - register_time
        return delta.total_seconds() >= 180  # 3分钟以上视为真实完成
    except Exception:
        return False


def _norm_status(source: str, status_val: str | None, finish_time, register_time, handler_val: str | None) -> str:
    """投诉状态归一化显示,空值根据 finish_time/handler 推断,避免大量"待处理"误报.
    返回语义: 已完成/处理中/待处理/待跟进 等。

    规则:
    1. 显式已完成类状态 → 已完成
    2. 显式处理中类状态 → 处理中
    3. 待处理类状态:
       - 有真实处理人(非wechat/sys/客户) → 处理中
       - 有真实finish_time(晚于登记时间3分钟+) → 已完成
       - 否则 → 待处理
    4. 无原始状态值: 根据 finish/handler/source 推断
    """
    has_handler = _has_real_handler(handler_val)
    has_real_finish = _is_real_finish(finish_time, register_time)
    if status_val:
        v = status_val.strip()
        if v in ("已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止"):
            return "已完成"
        if v in ("处理中", "跟进中", "跟进", "进行中"):
            return "处理中"
        if v in ("待处理", "未处理", "待分派", "pending", "待跟进"):
            if has_handler:
                return "处理中"
            if has_real_finish:
                return "已完成"
            return "待处理"
        return v
    # 无状态原始值, 推断
    if has_real_finish:
        return "已完成"
    if has_handler:
        return "处理中"
    if source == "外部投诉":
        return "待跟进"
    if source == "商户号投诉":
        return "待跟进"
    return "待处理"


def _norm_handler(h: str | None) -> str:
    """归一化处理人: 系统值/空值 → '未分配', 企微前缀('企微-xxx') → 纯姓名."""
    if h is None:
        return "未分配"
    v = str(h).strip()
    if not v or v.lower() in _SYS_HANDLERS:
        return "未分配"
    # 去企微前缀
    if v.startswith("企微-"):
        v = v[3:]
    return v or "未分配"


def _to_sh_iso(dt) -> str | None:
    """将 datetime (naive UTC 或 tz-aware) 转为北京时间 ISO 字符串 (naive, 无时区后缀)."""
    if dt is None:
        return None
    import datetime as _dt_mod
    if dt.tzinfo is None:
        # naive UTC → 视为UTC
        dt = dt.replace(tzinfo=timezone.utc)
    dt_sh = dt.astimezone(TZ)
    return dt_sh.strftime("%Y-%m-%d %H:%M")


@router.get("/summary", response_model=R, summary="三源投诉统计汇总(工单为主,卡商/外部投诉为前期参考补充)")
def complaint_summary(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
):
    """投诉统计汇总接口。

    口径说明(需求变更):
    - 三大分类(内部/商户号/外部)的 **总数/分类分布/处理人分布/趋势** 四要素
      全部以 service_tickets(工单) 为唯一主数据来源。
      这是运营实际考核与追踪的"主台账"。
    - card_complaints(卡商群) / external_complaints(监督局-315) 仅作为
      "前期参考补充"——帮助识别"可能还没落成工单、因此工单口径里暂时缺失"
      的早期投诉线索。它们在返回值中单独位于 merchant.supplementary /
      external.supplementary 子对象,绝不合并入主口径。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    def _time_filter(reg_col, fin_col):
        """生成时间过滤条件:register_time在范围内 OR finish_time在范围内(真实完成).
        与 complaint_detail 保持一致口径,解决历史工单今日完成不出现在统计里的问题."""
        conds = []
        reg_c = []
        if ts:
            reg_c.append(reg_col >= ts)
        if te:
            reg_c.append(reg_col <= te)
        fin_c = [fin_col.isnot(None)]
        if ts:
            fin_c.append(fin_col >= ts)
        if te:
            fin_c.append(fin_col <= te)
        return or_(and_(*reg_c), and_(*fin_c))

    def _tk_where(stmt, use_action_time: bool = True):
        stmt = stmt.where(_ticket_category_case() == "投诉")
        if ts or te:
            if use_action_time:
                # KPI/分类/处理人: 包含今日新增 + 今日完成
                stmt = stmt.where(_time_filter(ServiceTicket.register_time, ServiceTicket.finish_time))
            else:
                # trend: 按登记时间统计每日新增
                if ts:
                    stmt = stmt.where(ServiceTicket.register_time >= ts)
                if te:
                    stmt = stmt.where(ServiceTicket.register_time <= te)
        return stmt

    src_expr = _complaint_source_type_case()
    handler_expr = _handler_fallback_cols()

    # ---- 内部投诉 (仅工单) ----
    internal_total = db.scalar(_tk_where(select(func.count())).where(src_expr == "内部投诉")) or 0
    internal_by_cat = db.execute(
        _tk_where(select(ServiceTicket.service_item, func.count()).where(src_expr == "内部投诉"))
        .group_by(ServiceTicket.service_item)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    internal_by_handler = db.execute(
        _tk_where(select(handler_expr, func.count()).where(src_expr == "内部投诉"))
        .where(handler_expr.isnot(None))
        .group_by(handler_expr)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    internal_trend = db.execute(
        _tk_where(select(_day_trunc(ServiceTicket.register_time), func.count()).where(src_expr == "内部投诉"), use_action_time=False)
        .group_by(_day_trunc(ServiceTicket.register_time))
        .order_by(_day_trunc(ServiceTicket.register_time))
    ).all()

    # ---- 商户号投诉 (主: service_tickets 微信类工单; 参考补充: card_complaints 卡商群) ----
    merchant_ticket_total = db.scalar(_tk_where(select(func.count())).where(src_expr == "商户号投诉")) or 0
    merchant_by_cat = db.execute(
        _tk_where(select(ServiceTicket.service_item, func.count()).where(src_expr == "商户号投诉"))
        .group_by(ServiceTicket.service_item)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    merchant_by_handler = db.execute(
        _tk_where(select(handler_expr, func.count()).where(src_expr == "商户号投诉"))
        .where(handler_expr.isnot(None))
        .group_by(handler_expr)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    merchant_trend = db.execute(
        _tk_where(select(_day_trunc(ServiceTicket.register_time), func.count()).where(src_expr == "商户号投诉"), use_action_time=False)
        .group_by(_day_trunc(ServiceTicket.register_time))
        .order_by(_day_trunc(ServiceTicket.register_time))
    ).all()

    # ---- 外部投诉 (service_tickets 平台类工单: 工信部/12315/消协等) ----
    external_ticket_total = db.scalar(_tk_where(select(func.count())).where(src_expr == "外部投诉")) or 0
    external_by_cat = db.execute(
        _tk_where(select(ServiceTicket.service_item, func.count()).where(src_expr == "外部投诉"))
        .group_by(ServiceTicket.service_item)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    external_by_handler = db.execute(
        _tk_where(select(handler_expr, func.count()).where(src_expr == "外部投诉"))
        .where(handler_expr.isnot(None))
        .group_by(handler_expr)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    external_trend = db.execute(
        _tk_where(select(_day_trunc(ServiceTicket.register_time), func.count()).where(src_expr == "外部投诉"), use_action_time=False)
        .group_by(_day_trunc(ServiceTicket.register_time))
        .order_by(_day_trunc(ServiceTicket.register_time))
    ).all()

    def _handler_rows(rows):
        """归一化处理人后聚合 by_handler 结果,过滤系统值."""
        agg: dict[str, int] = {}
        for r in rows:
            nm = _norm_handler(r[0])
            if nm == "未分配":
                continue
            agg[nm] = agg.get(nm, 0) + int(r[1] or 0)
        return [{"name": k, "count": v} for k, v in sorted(agg.items(), key=lambda x: -x[1])]

    # ---- 积压未处理投诉(全量未完成, 不限今日) ----
    # 口径: 投诉类工单, status 不在已完成类集合, 且无真实 finish_time
    _done_status_set = {"已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止"}
    _backlog_stmt = (
        select(func.count())
        .where(_ticket_category_case() == "投诉")
        .where(
            or_(
                ServiceTicket.status.is_(None),
                ServiceTicket.status == "",
                ~ServiceTicket.status.in_(_done_status_set),
            )
        )
    )
    # 额外排除: finish_time明显晚于register_time的(虽status未更新但实际已完成)
    backlog_total = db.scalar(_backlog_stmt) or 0
    # 按分类统计积压
    _backlog_cat_stmt = (
        select(ServiceTicket.service_item, func.count())
        .where(_ticket_category_case() == "投诉")
        .where(
            or_(
                ServiceTicket.status.is_(None),
                ServiceTicket.status == "",
                ~ServiceTicket.status.in_(_done_status_set),
            )
        )
        .group_by(ServiceTicket.service_item)
        .order_by(func.count().desc())
        .limit(20)
    )
    backlog_by_cat = [
        {"name": r[0] or "未标注", "count": int(r[1] or 0)}
        for r in db.execute(_backlog_cat_stmt).all()
    ]

    # ---- 补充明细数据源统计(315外部投诉 / 卡商群投诉,workbuddy推送) ----
    # 口径: 两张补充表按投诉日期统计总数与"已关联服务工单"数。
    # 关联 = 按 device_sn 匹配到外部投诉类工单(matched_ticket_no 非空)。
    # 它们是对 service_tickets 工单口径的更详细补充, 不并入主口径三源合计。
    def _suppl_stats(model, date_col, matched_col):
        """补充表统计: 总数(时间范围内) + 已关联工单数(同一时间范围)"""
        conds: list = []
        if ts:
            conds.append(date_col >= ts)
        if te:
            conds.append(date_col <= te)
        total = db.scalar(select(func.count()).select_from(model).where(*conds)) or 0
        matched = db.scalar(select(func.count()).select_from(model).where(matched_col.isnot(None), *conds)) or 0
        return {"total": int(total), "matched": int(matched)}

    supplementary = {
        "external": _suppl_stats(ExternalComplaint, ExternalComplaint.complaint_date, ExternalComplaint.matched_ticket_no),
        "card": _suppl_stats(CardComplaint, CardComplaint.complaint_date, CardComplaint.matched_ticket_no),
    }

    return R(
        data={
            "supplementary": supplementary,
            "backlog": {
                "total": int(backlog_total),
                "by_category": backlog_by_cat,
            },
            "internal": {
                "total": int(internal_total),
                "by_category": [{"name": r[0] or "未标注", "count": int(r[1] or 0)} for r in internal_by_cat],
                "by_handler": _handler_rows(internal_by_handler),
                "trend": _trend(internal_trend),
            },
            "merchant": {
                "total": int(merchant_ticket_total),
                "by_category": [{"name": r[0] or "未标注", "count": int(r[1] or 0)} for r in merchant_by_cat],
                "by_handler": _handler_rows(merchant_by_handler),
                "trend": _trend(merchant_trend),
            },
            "external": {
                "total": int(external_ticket_total),
                "by_category": [{"name": r[0] or "未标注", "count": int(r[1] or 0)} for r in external_by_cat],
                "by_handler": _handler_rows(external_by_handler),
                "trend": _trend(external_trend),
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
    include_supplementary: bool = Query(False, description="为true时按设备SN附加315/卡商补充明细(关联合并)"),
):
    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    src_expr = _complaint_source_type_case()
    handler_expr = _handler_fallback_cols()

    records: list[dict] = []

    # 1. 服务工单投诉 (主台账) → is_primary=True, 前端排序在前
    #    查询范围: register_time在区间内(今日新增) OR finish_time在区间内(今日完成)
    #    解决历史登记但今日才处理完成的工单不出现在明细里的问题
    stmt = (
        select(
            ServiceTicket.ticket_no,
            ServiceTicket.device_sn,
            src_expr,
            ServiceTicket.service_item,
            ServiceTicket.remark,
            handler_expr,        # 工单 handler: 多级 fallback, 不再只看 handler 字段
            ServiceTicket.register_time,
            ServiceTicket.status,
            ServiceTicket.finish_time,
        )
        .where(_ticket_category_case() == "投诉")
    )
    # finish_time 在区间内
    # 说明: 如果finish_time ≈ register_time(系统默认值),二者几乎相同,
    # 此时 register_time 和 finish_time 要么同时在范围内,要么同时不在,不会漏掉也不会重复计算.
    finish_conds = [ServiceTicket.finish_time.isnot(None)]
    time_conds = []
    if ts:
        time_conds.append(ServiceTicket.register_time >= ts)
        finish_conds.append(ServiceTicket.finish_time >= ts)
    if te:
        time_conds.append(ServiceTicket.register_time <= te)
        finish_conds.append(ServiceTicket.finish_time <= te)
    finish_in_range = and_(*finish_conds)
    if time_conds:
        reg_in_range = and_(*time_conds)
        stmt = stmt.where(or_(reg_in_range, finish_in_range))
    else:
        stmt = stmt.where(finish_in_range)
    if source_type:
        stmt = stmt.where(src_expr == source_type)
    # 按"动作时间"排序: 用 GREATEST(register_time, COALESCE(finish_time, register_time)) 近似最近动作时间
    action_time = func.greatest(
        ServiceTicket.register_time,
        func.coalesce(ServiceTicket.finish_time, ServiceTicket.register_time),
    )
    for r in db.execute(stmt.order_by(action_time.desc().nulls_last())).all():
        source = r[2] or "内部投诉"
        raw_status = r[7]
        raw_handler = r[5]
        finish = r[8]
        register = r[6]
        # 展示时间:取最近动作时间(完成优先于登记,但如果完成时间不合理则用登记时间)
        display_dt = register
        if _is_real_finish(finish, register):
            # finish 在查询区间内(今日完成)或register也在区间内(今日登记今日完成):取较晚者
            if finish and register and finish > register:
                display_dt = finish
        records.append({
            "source": source,
            "source_role": "primary",
            "is_primary": True,
            "no": r[0],
            "device_sn": r[1],
            "category": r[3],
            "problem": r[4],
            "handler": _norm_handler(raw_handler),
            "time": _to_sh_iso(display_dt),
            "register_time": _to_sh_iso(register),
            "finish_time": _to_sh_iso(finish) if _is_real_finish(finish, register) else None,
            "status": _norm_status(source, raw_status, finish, register, raw_handler),
        })

    # ---- 关联合并: 按设备SN附加 315外部投诉/卡商群投诉 补充明细 ----
    # 两张补充表(workbuddy推送)是对工单口径的更详细描述(投诉问题/处理明细/跟进人等),
    # 核心匹配键为 device_sn, 一个SN可对应多条补充记录(用户可能多次投诉)。
    # 默认不附加(避免大页负载), 显式 include_supplementary=true 时附加。
    if include_supplementary:
        sns = {r["device_sn"] for r in records if r.get("device_sn")}
        if sns:
            suppl_by_sn: dict[str, list[dict]] = {}

            def _collect(model, sn_field_name: str, source_name, limit_per_sn=10):
                rows = db.scalars(
                    select(model).where(getattr(model, sn_field_name).in_(sns)).order_by(model.complaint_date.desc().nullslast())
                ).all()
                for c in rows:
                    sn = getattr(c, sn_field_name)
                    if not sn:
                        continue
                    suppl_by_sn.setdefault(sn, [])
                    if len(suppl_by_sn[sn]) >= limit_per_sn:
                        continue
                    suppl_by_sn[sn].append({
                        "source": source_name,
                        "complaint_no": getattr(c, "complaint_no", None),
                        "complaint_date": _to_sh_iso(c.complaint_date),
                        "complainant": c.complainant,
                        "complainant_phone": c.complainant_phone,
                        "platform": getattr(c, "platform", None),
                        "complaint_group": getattr(c, "complaint_group", None),
                        "problem": c.problem,
                        "solution": c.solution,
                        "result": c.result,
                        "handler": getattr(c, "handler", None) or getattr(c, "follower", None),
                        "handle_detail": getattr(c, "handle_detail", None) or getattr(c, "handle_result", None),
                        "handle_time": _to_sh_iso(c.handle_time) if hasattr(c, "handle_time") else None,
                        "matched_ticket_no": c.matched_ticket_no,
                    })

            _collect(ExternalComplaint, "device_sn", "315外部投诉")
            _collect(CardComplaint, "device_sn", "卡商群投诉")
            for r in records:
                r["supplementary"] = suppl_by_sn.get(r.get("device_sn"), [])

    # 按动作时间降序排列
    records.sort(key=lambda x: x.get("time") or "", reverse=True)
    return R(data={"items": records[:limit], "total": len(records)})


# ---------- 补充明细(315外部投诉 / 卡商群投诉,workbuddy推送) ----------


@router.get("/supplementary", response_model=R, summary="投诉补充明细列表(315/卡商,workbuddy推送)")
def complaint_supplementary(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="external=315外部投诉/card=卡商群投诉, 缺省查全部"),
    matched: bool | None = Query(None, description="true=仅已关联工单, false=仅未关联"),
    limit: int = Query(500, ge=1, le=2000),
):
    """查询两张投诉补充表明细(按投诉日期过滤), 支持按来源/关联状态筛选。

    数据由 workbuddy 定期推送, 是对 service_tickets 工单口径的更详细补充。
    关联 = 按 device_sn 匹配到外部投诉类工单(matched_ticket_no 非空)。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    def _items(model, sn_field, source_name):
        stmt = select(model).where(True)
        if ts:
            stmt = stmt.where(model.complaint_date >= ts)
        if te:
            stmt = stmt.where(model.complaint_date <= te)
        if matched is True:
            stmt = stmt.where(model.matched_ticket_no.isnot(None))
        elif matched is False:
            stmt = stmt.where(model.matched_ticket_no.is_(None))
        rows = db.scalars(stmt.order_by(model.complaint_date.desc().nullslast())).all()
        return [
            {
                "source": source_name,
                "complaint_no": getattr(c, "complaint_no", None),
                "complaint_date": _to_sh_iso(c.complaint_date),
                "complaint_group": getattr(c, "complaint_group", None),
                "complainant": c.complainant,
                "complainant_phone": c.complainant_phone,
                "device_sn": getattr(c, sn_field, None),
                "platform": getattr(c, "platform", None),
                "problem": c.problem,
                "problem_category": getattr(c, "problem_category", None) or getattr(c, "complaint_category", None),
                "solution": c.solution,
                "result": c.result,
                "handler": getattr(c, "handler", None) or getattr(c, "follower", None),
                "handle_detail": getattr(c, "handle_detail", None) or getattr(c, "handle_result", None),
                "handle_time": _to_sh_iso(c.handle_time) if hasattr(c, "handle_time") else None,
                "matched_ticket_no": c.matched_ticket_no,
            }
            for c in rows
        ]

    items: list[dict] = []
    if source_type in (None, "external", "315"):
        items.extend(_items(ExternalComplaint, "device_sn", "315外部投诉"))
    if source_type in (None, "card", "卡商"):
        items.extend(_items(CardComplaint, "device_sn", "卡商群投诉"))
    # 按投诉日期降序(主排序), 无日期的排末尾
    items.sort(key=lambda x: (x.get("complaint_date") is None, x.get("complaint_date") or ""), reverse=True)
    return R(data={"items": items[:limit], "total": len(items)})


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
