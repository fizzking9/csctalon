"""投诉统计路由(规划设计 §4.6 三源统一统计)

三源: 内部投诉(service_tickets) / 商户号投诉(service_tickets微信 + card_complaints卡商群) / 外部投诉(service_tickets平台 + external_complaints)。
复用 analytics 服务的口径 helper, 保证与既有看板一致。
"""

from collections import Counter
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

    # ---- 补充明细数据源统计(315外部投诉 / 卡商群投诉,workbuddy推送) ----
    # 口径: 两张补充表按投诉日期统计总数与"已关联服务工单"数。
    # 关联 = 按 device_sn 匹配到对应工单(matched_ticket_no 非空)。
    # 补充表与工单是同一批投诉的两种视角, 先统计以便三源总数合并去重。
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

    # ---- 三源总数合并补充表(去重) ----
    # 补充表(workbuddy推送)是对应主台账的更详细描述: 315→外部投诉工单, 卡商→商户号投诉(微信)工单。
    # 合并口径: 补充记录若已关联到"本统计范围内"的工单(matched_ticket_no 对应工单的
    # register_time 或真实 finish_time 在范围内), 视为与工单同一条(去重), 不计入补充部分;
    # 未落工单的补充记录计入对应来源总数。
    def _overlap_suppl(model, src_expr_val):
        """本范围内 已关联工单且工单也在统计范围内 的补充表条数(=工单已计入、补充需去重的部分)

        有日期范围时, 补充记录关联的工单必须 register/finish 也在范围内才算已计入;
        无范围(全量查询)时, 关联工单一定在全量工单中, 全部 matched 补充都去重。
        """
        conds = []
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        t_sub = (
            select(ServiceTicket.ticket_no)
            .where(_ticket_category_case() == "投诉")
            .where(_complaint_source_type_case() == src_expr_val)
        )
        if ts or te:
            t_sub = t_sub.where(_time_filter(ServiceTicket.register_time, ServiceTicket.finish_time))
        return (
            db.scalar(
                select(func.count())
                .select_from(model)
                .where(*conds)
                .where(model.matched_ticket_no.isnot(None))
                .where(model.matched_ticket_no.in_(t_sub))
            )
            or 0
        )

    external_total = int(external_ticket_total) + supplementary["external"]["total"] - _overlap_suppl(ExternalComplaint, "外部投诉")
    merchant_total = int(merchant_ticket_total) + supplementary["card"]["total"] - _overlap_suppl(CardComplaint, "商户号投诉")

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
                "total": int(merchant_total),
                "by_category": [{"name": r[0] or "未标注", "count": int(r[1] or 0)} for r in merchant_by_cat],
                "by_handler": _handler_rows(merchant_by_handler),
                "trend": _trend(merchant_trend),
            },
            "external": {
                "total": int(external_total),
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

    # 被投诉商户主体名集合(registrant 去重): 用于过滤上游把商户号名称误写入 handler 的脏数据
    # 商户号投诉的"处理人"必须是客服人员, 不能是商户主体名。
    entity_set: set[str] = set()
    if not source_type or source_type == "商户号投诉":
        entity_set = set(
            db.scalars(
                select(ServiceTicket.registrant)
                .where(_ticket_category_case() == "投诉")
                .where(src_expr == "商户号投诉")
                .where(ServiceTicket.registrant.isnot(None), ServiceTicket.registrant != "")
                .distinct()
            ).all()
        )

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
            ServiceTicket.registrant,  # 被投诉商户主体名(商户号投诉场景; 内部/外部投诉时多为处理人或空)
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
            # 商户号投诉: handler 若落在被投诉主体名集合 → 该行无真实处理人(上游脏数据)
            "handler": "未分配" if (source == "商户号投诉" and raw_handler in entity_set) else _norm_handler(raw_handler),
            "entity": r[9],  # 被投诉商户主体名(registrant)
            "time": _to_sh_iso(display_dt),
            "register_time": _to_sh_iso(register),
            "finish_time": _to_sh_iso(finish) if _is_real_finish(finish, register) else None,
            "status": _norm_status(source, raw_status, finish, register, raw_handler),
        })

    # ---- 关联合并: 按设备SN附加 315外部投诉/卡商群投诉 补充明细 ----
    # 两张补充表(workbuddy推送)是对工单口径的更详细描述(投诉问题/处理明细/跟进人等),
    # 核心匹配键为 device_sn, 一个SN可对应多条补充记录(用户可能多次投诉)。
    # 默认不附加(避免大页负载), 显式 include_supplementary=true 时附加。
    # 已关联工单的按SN附加到对应工单行; 未关联工单(或SN为空)的生成独立行,
    # 使运营看板/今日看板也能看到 workbuddy 推送但尚未落成工单的投诉线索。
    if include_supplementary:
        extra_rows: list[dict] = []

        def _collect(model, sn_field_name: str, source_name, limit_per_sn=10):
            stmt = select(model)
            if ts:
                stmt = stmt.where(model.complaint_date >= ts)
            if te:
                stmt = stmt.where(model.complaint_date <= te)
            rows = db.scalars(stmt.order_by(model.complaint_date.desc().nullslast())).all()
            for c in rows:
                sn = getattr(c, sn_field_name)
                suppl_item = {
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
                }
                # 命中工单SN → 附加到对应工单行(补充说明)
                targets = [r for r in records if r.get("device_sn") == sn] if sn else []
                if targets:
                    for rec in targets:
                        rec.setdefault("supplementary", [])
                        if len(rec["supplementary"]) >= limit_per_sn:
                            continue
                        rec["supplementary"].append(suppl_item)
                    continue
                # 未关联工单(或SN为空) → 独立行, 归入对应三源口径(315→外部投诉, 卡商→商户号投诉)
                is_315 = source_name == "315外部投诉"
                main_source = "外部投诉" if is_315 else "商户号投诉"
                extra_rows.append({
                    "source": main_source,
                    "source_role": "supplementary",
                    "is_primary": False,
                    "no": suppl_item["complaint_no"] or suppl_item["matched_ticket_no"],
                    "device_sn": sn,
                    # 分类显示平台类型(315表: 全国12315平台/公共服务平台等) / 投诉群(卡商表), 与工单 service_item 口径对齐
                    "category": getattr(c, "platform", None) if is_315 else getattr(c, "complaint_group", None),
                    "problem": c.problem,
                    "handler": _norm_handler(suppl_item["handler"]),
                    "time": suppl_item["handle_time"] or suppl_item["complaint_date"],
                    "register_time": suppl_item["complaint_date"],
                    "finish_time": suppl_item["handle_time"],
                    "status": c.result or "补充记录",
                    "supplementary": [suppl_item],
                    "suppl_finish_time": suppl_item["handle_time"],
                    "suppl_problem": c.problem,
                    "suppl_count": 1,
                })

        _collect(ExternalComplaint, "device_sn", "315外部投诉")
        _collect(CardComplaint, "device_sn", "卡商群投诉")

        # 工单行便捷合并字段: 补充表里更详细的 完成时间/投诉内容(供明细表直接展示)
        for r in records:
            suppl = r.get("supplementary") or []
            r["supplementary"] = suppl
            if suppl:
                times = [s.get("handle_time") for s in suppl if s.get("handle_time")]
                r["suppl_finish_time"] = max(times) if times else None
                r["suppl_problem"] = next((s.get("problem") for s in suppl if s.get("problem")), None)
                r["suppl_count"] = len(suppl)

        records.extend(extra_rows)

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
                "company_entity": getattr(c, "company_entity", None),
                "solution": c.solution,
                "result": c.result,
                "handler": getattr(c, "handler", None) or getattr(c, "follower", None),
                "handle_detail": getattr(c, "handle_detail", None) or getattr(c, "handle_result", None),
                "handle_time": _to_sh_iso(c.handle_time) if hasattr(c, "handle_time") else None,
                "matched_ticket_no": c.matched_ticket_no,
                "created_at": _to_sh_iso(c.created_at) if hasattr(c, "created_at") else None,
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


# ---------- 积压未处理投诉 审核分析 ----------


# 积压口径统一 (与 /detail 的 _norm_status 完全一致):
# 积压 = 投诉类工单, 归一化状态 != '已完成'。
# SQL 粗过滤排除显式完成类状态, Python 层用 _norm_status 兜底(排除"待处理/空状态但存在真实完成时间")。
_BACKLOG_DONE_SET = {"已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止"}
# 积压龄期分桶 (按登记时间距今天数)
_BACKLOG_AGING_BUCKETS = [("今天", 0, 0), ("1-3天", 1, 3), ("4-7天", 4, 7), ("8-14天", 8, 14), (">14天", 15, None)]


@router.get("/backlog", response_model=R, summary="积压未处理投诉审核分析(统一口径: 归一化状态非已完成)")
def complaint_backlog(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="内部投诉/商户号投诉/外部投诉"),
    status: str | None = Query(None, description="归一化状态: 处理中/待处理/待跟进"),
    category: str | None = Query(None, description="按工单服务项(分类)过滤"),
    has_handler: bool | None = Query(None, description="true=已分派(有真实处理人) / false=待分派(无处理人)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = Query("aging_asc", description="aging_asc=最旧积压优先(默认) / register_desc / register_asc"),
):
    """积压未处理投诉的查询/统计/审核分析。

    与 /summary、/analysis 的积压口径不同点: 本接口统一用 _norm_status 归一化判定,
    排除"状态仍为待处理但实际已真实完成(finish_time 晚于登记 3 分钟)"的工单,
    避免统计数与明细表对不上。
    统计维度(不受分页影响): 来源/分类/龄期/处理人/待分派/超期/已核查。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    src_expr = _complaint_source_type_case()

    def _fb_handler(t: ServiceTicket) -> str | None:
        """Python 侧处理人 fallback, 与 _handler_fallback_cols 一致: handler→assignee→updated_by_raw→registrant"""
        for v in (t.handler, t.assignee, t.updated_by_raw, t.registrant):
            if v is not None and str(v).strip():
                return str(v)
        return None

    def _aging_days(reg) -> int | None:
        if reg is None:
            return None
        dt = reg
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(TZ).date() - dt.astimezone(TZ).date()).days

    # SQL 粗过滤: 投诉类 + 非显式完成状态(空/未填/非完成集合); 有登记时间范围则按登记时间收窄
    stmt = (
        select(ServiceTicket, src_expr)
        .where(_ticket_category_case() == "投诉")
        .where(
            or_(
                ServiceTicket.status.is_(None),
                ServiceTicket.status == "",
                ~ServiceTicket.status.in_(_BACKLOG_DONE_SET),
            )
        )
        .order_by(ServiceTicket.register_time.asc().nulls_last())
    )
    if ts:
        stmt = stmt.where(ServiceTicket.register_time >= ts)
    if te:
        stmt = stmt.where(ServiceTicket.register_time <= te)

    # 商户号投诉: 被投诉主体名集合(registrant 去重), 与 /detail 一致——
    # 上游把商户号名称误写入 handler 时, 该行视为无真实处理人(未分派)
    entity_set: set[str] = set()
    if not source_type or source_type == "商户号投诉":
        entity_set = set(db.scalars(
            select(ServiceTicket.registrant)
            .where(_ticket_category_case() == "投诉")
            .where(src_expr == "商户号投诉")
            .where(ServiceTicket.registrant.isnot(None), ServiceTicket.registrant != "")
            .distinct()
        ).all())

    # Python 归一化兜底, 保证与明细口径 100% 一致
    backlog: list[dict] = []
    for t, src in db.execute(stmt).all():
        norm = _norm_status(src, t.status, t.finish_time, t.register_time, _fb_handler(t))
        if norm == "已完成":
            continue
        raw_h = _fb_handler(t)
        norm_h = _norm_handler(raw_h)
        if src == "商户号投诉" and raw_h in entity_set:
            norm_h = "未分配"  # 上游把被投诉主体名写入处理人 → 无真实处理人
        backlog.append({
            "ticket_no": t.ticket_no,
            "device_sn": t.device_sn,
            "source": src,
            "category": t.service_item or "未标注",
            "problem": t.remark,
            "handler": norm_h,
            "status": norm,
            "register_time": _to_sh_iso(t.register_time),
            "finish_time": _to_sh_iso(t.finish_time) if _is_real_finish(t.finish_time, t.register_time) else None,
            "aging_days": _aging_days(t.register_time),
        })

    # 筛选(与分页解耦, 统计基于筛选后的全量)
    if source_type:
        backlog = [r for r in backlog if r["source"] == source_type]
    if status:
        backlog = [r for r in backlog if r["status"] == status]
    if category:
        backlog = [r for r in backlog if r["category"] == category]
    if has_handler is True:
        backlog = [r for r in backlog if r["handler"] != "未分配"]
    elif has_handler is False:
        backlog = [r for r in backlog if r["handler"] == "未分配"]

    # 排序
    if sort == "register_desc":
        backlog.sort(key=lambda r: r["register_time"] or "", reverse=True)
    elif sort == "register_asc":
        backlog.sort(key=lambda r: r["register_time"] or "")
    else:  # aging_asc: 最旧积压优先 (登记时间升序)
        backlog.sort(key=lambda r: (r["register_time"] is None, r["register_time"] or ""))

    # 已核查标识: 核查表里按 device_sn 或 ref_id(工单号) 关联
    audited_sns = set(db.scalars(select(ComplaintAudit.device_sn).where(ComplaintAudit.device_sn.isnot(None))).all())
    audited_refs = set(db.scalars(
        select(ComplaintAudit.ref_id).where(ComplaintAudit.ref_id.isnot(None), ComplaintAudit.ref_id != "")
    ).all())

    def _audited(r: dict) -> bool:
        return bool(r["device_sn"] in audited_sns or r["ticket_no"] in audited_refs)

    # 统计维度
    by_source = [{"name": k, "count": v} for k, v in sorted(Counter(r["source"] for r in backlog).items(), key=lambda x: -x[1])]
    by_category = [
        {"name": k, "count": v}
        for k, v in sorted(Counter(r["category"] for r in backlog).items(), key=lambda x: -x[1])[:15]
    ]
    aging_counter = Counter()
    for r in backlog:
        ad = r["aging_days"]
        if ad is None:
            continue  # 无登记时间, 不进龄期分桶
        bucket = next((b for b, lo, hi in _BACKLOG_AGING_BUCKETS if lo <= ad and (hi is None or ad <= hi)), ">14天")
        aging_counter[bucket] += 1
    by_aging = [{"name": b, "count": aging_counter.get(b, 0)} for b, _, _ in _BACKLOG_AGING_BUCKETS]
    by_handler = [
        {"name": k, "count": v}
        for k, v in sorted(Counter(r["handler"] for r in backlog).items(), key=lambda x: -x[1])[:15]
    ]
    unassigned = sum(1 for r in backlog if r["handler"] == "未分配")
    overdue = sum(1 for r in backlog if r["aging_days"] is not None and r["aging_days"] >= 8)  # 登记满7天未完成
    audited_total = sum(1 for r in backlog if _audited(r))

    total = len(backlog)
    max_aging = max((r["aging_days"] for r in backlog if r["aging_days"] is not None), default=0)
    start_idx = (page - 1) * page_size
    items = backlog[start_idx:start_idx + page_size]
    for r in items:
        r["audited"] = _audited(r)

    return R(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "max_aging": max_aging,
        "by_source": by_source,
        "by_category": by_category,
        "by_aging": by_aging,
        "by_handler": by_handler,
        "unassigned": unassigned,
        "overdue": overdue,
        "audited": audited_total,
        "items": items,
    })


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


# ─────────────────────────────────────────────────────────────
# 综合分析 (决策看板用)
# ─────────────────────────────────────────────────────────────


@router.get("/analysis", response_model=R, summary="投诉决策分析(管理摘要/平台分布/时效/处理人效率)")
def complaint_analysis(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
):
    """投诉决策分析接口，返回管理摘要、平台分布、趋势、时效、处理人效率等维度。
    与 /summary 共享口径，但提供更多分析维度供决策看板使用。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    src_expr = _complaint_source_type_case()
    handler_expr = _handler_fallback_cols()

    # 辅助: 时间过滤 (register 或 finish 在范围内)
    def _action_filter(stmt):
        if ts:
            stmt = stmt.where(ServiceTicket.register_time >= ts)
        if te:
            stmt = stmt.where(ServiceTicket.register_time <= te)
        return stmt

    def _tk_base():
        """基础: 投诉类工单"""
        return select(ServiceTicket).where(_ticket_category_case() == "投诉")

    # ── 1. 管理摘要 ──
    total = db.scalar(_action_filter(_tk_base().with_only_columns(func.count()))) or 0

    # 三源统计
    internal_total = db.scalar(
        _action_filter(_tk_base().with_only_columns(func.count()).where(src_expr == "内部投诉"))
    ) or 0
    merchant_total = db.scalar(
        _action_filter(_tk_base().with_only_columns(func.count()).where(src_expr == "商户号投诉"))
    ) or 0
    external_total = db.scalar(
        _action_filter(_tk_base().with_only_columns(func.count()).where(src_expr == "外部投诉"))
    ) or 0

    # 积压: 全量未完成(不限时间)
    _done_set = {"已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止"}
    backlog_total = db.scalar(
        _tk_base().with_only_columns(func.count()).where(
            or_(
                ServiceTicket.status.is_(None),
                ServiceTicket.status == "",
                ~ServiceTicket.status.in_(_done_set),
            )
        )
    ) or 0

    # 已完成: 有真实完成时间
    done_total = db.scalar(
        _action_filter(
            _tk_base().with_only_columns(func.count()).where(
                ServiceTicket.finish_time.isnot(None),
                ServiceTicket.finish_time > ServiceTicket.register_time,
            )
        )
    ) or 0
    completion_rate = round(done_total / total, 4) if total > 0 else 0

    # 平均处理时长(小时) — 仅统计有真实完成时间的
    avg_handle_row = db.execute(
        _action_filter(
            _tk_base().with_only_columns(
                func.avg(
                    func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) / 3600
                )
            ).where(
                ServiceTicket.finish_time.isnot(None),
                ServiceTicket.finish_time > ServiceTicket.register_time,
            )
        )
    ).scalar()
    avg_handle_hours = round(float(avg_handle_row), 1) if avg_handle_row else 0

    # ── 2. 来源分布 ──
    source_dist = db.execute(
        _action_filter(
            _tk_base().with_only_columns(src_expr, func.count())
            .group_by(src_expr)
            .order_by(func.count().desc())
        )
    ).all()
    source_distribution = [
        {"name": r[0] or "内部投诉", "count": int(r[1] or 0)}
        for r in source_dist
    ]

    # ── 3. 趋势(按日, 三源堆叠) ──
    day_trunc = func.date_trunc("day", func.timezone("Asia/Shanghai", ServiceTicket.register_time))
    trend_rows = db.execute(
        _action_filter(
            _tk_base().with_only_columns(day_trunc, src_expr, func.count())
            .group_by(day_trunc, src_expr)
            .order_by(day_trunc)
        )
    ).all()
    # 合并为 {day: {source: count}} 结构
    trend_map: dict[str, dict[str, int]] = {}
    for r in trend_rows:
        day = str(r[0])[:10] if r[0] else ""
        src = r[1] or "内部投诉"
        cnt = int(r[2] or 0)
        if day not in trend_map:
            trend_map[day] = {}
        trend_map[day][src] = trend_map[day].get(src, 0) + cnt
    trend = [
        {
            "day": d,
            "内部投诉": v.get("内部投诉", 0),
            "商户号投诉": v.get("商户号投诉", 0),
            "外部投诉": v.get("外部投诉", 0),
        }
        for d, v in sorted(trend_map.items())[:90]
    ]

    # ── 4. 处理人 Top10 ──
    handler_rows = db.execute(
        _action_filter(
            _tk_base().with_only_columns(handler_expr, func.count())
            .where(handler_expr.isnot(None))
            .group_by(handler_expr)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    handler_top = [
        {"name": _norm_handler(r[0]), "count": int(r[1] or 0)}
        for r in handler_rows
        if _norm_handler(r[0]) != "未分配"
    ][:10]

    # ── 5. 外部投诉平台分布 (以 315 补充表为主口径, 含完整投诉内容) ──
    # 工单口径 remark 全空且数量少; 315 补充表(external_complaints)有完整 problem/平台/分类。
    ext_conds = []
    if ts:
        ext_conds.append(ExternalComplaint.complaint_date >= ts)
    if te:
        ext_conds.append(ExternalComplaint.complaint_date <= te)

    # 平台分布 (315 表 platform 字段)
    ext_plat_rows = db.execute(
        select(ExternalComplaint.platform, func.count())
        .where(*ext_conds)
        .group_by(ExternalComplaint.platform)
        .order_by(func.count().desc())
        .limit(15)
    ).all()
    platform_distribution = [
        {"name": r[0] or "未标注", "count": int(r[1] or 0)}
        for r in ext_plat_rows
    ]

    # 问题分类分布 (315 表 complaint_category 字段)
    ext_cat_rows = db.execute(
        select(ExternalComplaint.complaint_category, func.count())
        .where(*ext_conds)
        .group_by(ExternalComplaint.complaint_category)
        .order_by(func.count().desc())
        .limit(15)
    ).all()
    external_category_distribution = [
        {"name": r[0] or "未标注", "count": int(r[1] or 0)}
        for r in ext_cat_rows
    ]

    # 外部投诉趋势 (315 表按日)
    ext_day_trunc = func.date_trunc("day", func.timezone("Asia/Shanghai", ExternalComplaint.complaint_date))
    ext_trend_rows = db.execute(
        select(ext_day_trunc, func.count())
        .where(*ext_conds)
        .group_by(ext_day_trunc)
        .order_by(ext_day_trunc)
    ).all()
    external_trend = [
        {"day": str(r[0])[:10], "count": int(r[1] or 0)}
        for r in ext_trend_rows
    ]

    # 外部投诉总数/关联状态 (315 表)
    ext_total = db.scalar(select(func.count()).select_from(ExternalComplaint).where(*ext_conds)) or 0
    ext_matched = db.scalar(
        select(func.count()).select_from(ExternalComplaint)
        .where(*ext_conds)
        .where(ExternalComplaint.matched_ticket_no.isnot(None))
    ) or 0

    # 今日登记的外部投诉数(按 created_at 北京时间当天): 独立于筛选范围
    # 用户关注"今天同步/登记进来了几条", 与"投诉日期"口径区分(投诉日期可能早于登记日)
    now_sh = datetime.now(TZ)
    today_start_utc = now_sh.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    ext_today_registered = db.scalar(
        select(func.count()).select_from(ExternalComplaint)
        .where(ExternalComplaint.created_at >= today_start_utc)
    ) or 0

    # 商户号投诉分类 (工单 service_item)
    merchant_rows = db.execute(
        _action_filter(
            _tk_base().with_only_columns(ServiceTicket.service_item, func.count())
            .where(src_expr == "商户号投诉")
            .group_by(ServiceTicket.service_item)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    merchant_category = [
        {"name": r[0] or "未标注", "count": int(r[1] or 0)}
        for r in merchant_rows
    ]

    # ── 5.1 商户号被投诉主体分布 (registrant 字段存被投诉商户主体名, 非处理人) ──
    merchant_entity_rows = db.execute(
        _action_filter(
            _tk_base().with_only_columns(ServiceTicket.registrant, func.count())
            .where(src_expr == "商户号投诉")
            .where(ServiceTicket.registrant.isnot(None), ServiceTicket.registrant != "")
            .group_by(ServiceTicket.registrant)
            .order_by(func.count().desc())
            .limit(15)
        )
    ).all()
    merchant_entity_distribution = [
        {"name": r[0] or "未标注", "count": int(r[1] or 0)}
        for r in merchant_entity_rows
    ]

    # 商户号处理人分布 (handler, 排除被投诉主体名)
    merchant_handler_rows = db.execute(
        _action_filter(
            _tk_base().with_only_columns(handler_expr, func.count())
            .where(src_expr == "商户号投诉")
            .where(handler_expr.isnot(None))
            .group_by(handler_expr)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    merchant_entity_names = {r[0] for r in merchant_entity_rows if r[0]}
    merchant_handler_distribution = [
        {"name": _norm_handler(r[0]), "count": int(r[1] or 0)}
        for r in merchant_handler_rows
        if _norm_handler(r[0]) != "未分配" and r[0] not in merchant_entity_names
    ]

    # ── 6. 时效分布(处理时长分布) ──
    # 按处理时长(天)分桶: <1天, 1-3天, 3-7天, 7-14天, 14-30天, >30天
    _bucket = case(
        (
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) / 3600 <= 24,
            "<1天",
        ),
        (
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) / 3600 <= 72,
            "1-3天",
        ),
        (
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) / 3600 <= 168,
            "3-7天",
        ),
        (
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) / 3600 <= 336,
            "7-14天",
        ),
        (
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) / 3600 <= 720,
            "14-30天",
        ),
        else_=">30天",
    )
    timeliness_rows = db.execute(
        _action_filter(
            _tk_base().with_only_columns(_bucket, func.count())
            .where(
                ServiceTicket.finish_time.isnot(None),
                ServiceTicket.finish_time > ServiceTicket.register_time,
            )
            .group_by(_bucket)
        )
    ).all()
    # 确保桶顺序
    _bucket_order = ["<1天", "1-3天", "3-7天", "7-14天", "14-30天", ">30天"]
    timeliness_map = {r[0]: int(r[1] or 0) for r in timeliness_rows}
    timeliness = [
        {"name": b, "count": timeliness_map.get(b, 0)}
        for b in _bucket_order
    ]

    # ── 7. 处理人效率 (按三源分组: 内部/商户号/外部 是不同维度的任务) ──
    _done_case = case(
        (ServiceTicket.finish_time.isnot(None), 1),
        else_=0,
    )
    _hours_case = case(
        (
            ServiceTicket.finish_time.isnot(None),
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) / 3600,
        ),
        else_=None,
    )

    def _eff_rows(src_val: str, entity_set: set[str] | None = None) -> list[dict]:
        eff = db.execute(
            _action_filter(
                _tk_base().with_only_columns(
                    handler_expr,
                    func.count().label("total"),
                    func.sum(_done_case).label("done"),
                    func.avg(_hours_case).label("avg_hours"),
                )
                .where(src_expr == src_val)
                .where(handler_expr.isnot(None))
                .group_by(handler_expr)
                .order_by(func.count().desc())
                .limit(15)
            )
        ).all()
        return [
            {
                "name": _norm_handler(r[0]),
                "total": int(r[1] or 0),
                "done": int(r[2] or 0),
                "avg_hours": round(float(r[3]), 1) if r[3] else 0,
            }
            for r in eff
            if _norm_handler(r[0]) != "未分配"
            and (not entity_set or r[0] not in entity_set)
        ]

    handler_efficiency = {
        "internal": _eff_rows("内部投诉"),
        "merchant": _eff_rows("商户号投诉", entity_set=merchant_entity_names),
        "external": _eff_rows("外部投诉"),
    }

    # ── 8. 外部投诉平台趋势 (315 表按平台) ──
    ext_plat_trend_rows = db.execute(
        select(ext_day_trunc, ExternalComplaint.platform, func.count())
        .where(*ext_conds)
        .group_by(ext_day_trunc, ExternalComplaint.platform)
        .order_by(ext_day_trunc)
    ).all()
    ext_plat_trend_map: dict[str, dict[str, int]] = {}
    for r in ext_plat_trend_rows:
        day = str(r[0])[:10] if r[0] else ""
        platform = r[1] or "未标注"
        cnt = int(r[2] or 0)
        if day not in ext_plat_trend_map:
            ext_plat_trend_map[day] = {}
        ext_plat_trend_map[day][platform] = ext_plat_trend_map[day].get(platform, 0) + cnt
    external_platform_trend = [
        {"day": d, **v}
        for d, v in sorted(ext_plat_trend_map.items())[:90]
    ]

    return R(
        data={
            "summary": {
                "total": total,
                "internal_total": internal_total,
                "merchant_total": merchant_total,
                "external_total": external_total,
                "backlog_total": backlog_total,
                "done_total": done_total,
                "completion_rate": completion_rate,
                "avg_handle_hours": avg_handle_hours,
            },
            "source_distribution": source_distribution,
            "trend": trend,
            "handler_top": handler_top,
            "platform_distribution": platform_distribution,
            "external_category_distribution": external_category_distribution,
            "external_trend": external_trend,
            "external_total": ext_total,
            "external_matched": ext_matched,
            "external_unmatched": ext_total - ext_matched,
            "external_today_registered": ext_today_registered,
            "merchant_category": merchant_category,
            "merchant_entity_distribution": merchant_entity_distribution,
            "merchant_handler_distribution": merchant_handler_distribution,
            "timeliness": timeliness,
            "handler_efficiency": handler_efficiency,
            "external_platform_trend": external_platform_trend,
        }
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
