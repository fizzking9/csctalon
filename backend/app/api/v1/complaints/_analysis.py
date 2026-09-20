"""投诉决策分析(管理摘要/平台分布/时效/效率): complaint_analysis。"""

from datetime import datetime, timezone

from sqlalchemy import and_, case, func, or_, select

from app.api.deps import CurrentUser, DBSession

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._helpers import (
    _card_reg_handled,
    _external_done_sns,
    _handler_fallback_cols,
    _norm_handler,
    _parse_dt,
    _parse_dt_end,
    _sources_reg_handled,
    _suppl_event_metrics,
    _suppl_trend_rows,
)
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.schemas.common import R
from app.services.analytics import (
    TZ,
    _complaint_source_type_case,
    _ticket_category_case,
)
from app.services.complaint_scope import (
    CARD_SCOPE,
)


@router.get("/analysis", response_model=R, summary="投诉决策分析(管理摘要/平台分布/时效/处理人效率)")
@cached_json(lambda *a, **kw: f"complaint:analysis:{kw.get('start')}:{kw.get('end')}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 120))
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

    # 三源统计 (工单主台账 + workbuddy推送补充表, 去重口径与 /summary 一致)
    internal_total = db.scalar(
        _action_filter(_tk_base().with_only_columns(func.count()).where(src_expr == "内部投诉"))
    ) or 0
    merchant_ticket_total = db.scalar(
        _action_filter(_tk_base().with_only_columns(func.count()).where(src_expr == "商户号投诉"))
    ) or 0
    external_ticket_total = db.scalar(
        _action_filter(_tk_base().with_only_columns(func.count()).where(src_expr == "外部投诉"))
    ) or 0

    # 补充表趋势(按日, 含 overlap)供总数与趋势共用同一去重口径
    #   ⚠️ 2026-09-10 (#47): 卡商**独立成桶**(统计出口), 但传入 `_suppl_trend_rows`
    #      的 `src_expr_val` 是 **工单侧来源桶**, 用于匹配"已落成工单"的重叠。
    #      卡商投诉在工单侧登记为**外部投诉类**(工信部/12315/消协等平台投诉),
    #      故此处仍传 "外部投诉" —— 传 "(外)卡商" 会使 t_sub 恒空 → overlap 恒 0
    #      → 卡商总数虚高(实测 796 vs 真实 39)。
    _ext_suppl_trend = _suppl_trend_rows(db, ExternalComplaint, "外部投诉", ts, te)
    _card_suppl_trend = _suppl_trend_rows(db, CardComplaint, "外部投诉", ts, te)
    # 补充表净计入总数 = total - overlap (overlap 已关联本范围内工单 → 工单已计入, 去重)
    #   ⚠️ 仅 315 适用此去重(315 投诉可能落成工单)。
    _ext_suppl_net = sum(int(r["total"] or 0) - int(r["overlap"] or 0) for r in _ext_suppl_trend)
    merchant_total = int(merchant_ticket_total)
    external_total = int(external_ticket_total) + _ext_suppl_net
    # ★★ (#53) 卡商独立总数 = **表自身行数**, 不扣工单重叠 ★★
    #   实证: 工单表 service_item/service_category 含「卡商」均 0 行 →
    #   卡商在工单侧无独立分类, 无"已计工单"可扣。原扣减口径(#52)会算少卡商。
    _card_reg = _card_reg_handled(db, ts, te)
    card_total = int(_card_reg["registered"])

    # 积压: 窗口内登记的未完成(跟随 start/end, 默认近30天; 不再无限累积历史遗留)
    _done_set = {"已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止"}
    _backlog_status_where = or_(
        ServiceTicket.status.is_(None),
        ServiceTicket.status == "",
        ~ServiceTicket.status.in_(_done_set),
    )
    _backlog_stmt = _action_filter(
        _tk_base().with_only_columns(func.count()).where(_backlog_status_where)
    )
    # 积压闭环: 外部投诉工单关联的 315 登记已办结 → 视为已完成, 不进积压
    _ext_done_sns = _external_done_sns(db)
    if _ext_done_sns:
        _backlog_stmt = _backlog_stmt.where(
            or_(src_expr != "外部投诉", ServiceTicket.device_sn.notin_(_ext_done_sns))
        )
    backlog_total = db.scalar(_backlog_stmt) or 0

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

    # ── 2. 来源分布 (工单三源 + 补充表并入外部桶, 与 /summary 同口径) ──
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
    # ★★ (#53) 来源分布**不含卡商** ★★
    #   卡商已剥离为独立体系(用户: "不纳入总量的管理…全部单算")。
    #   仅 315(外部投诉) 补充表的净记录并入外部桶(去重口径与 /summary 一致)。
    #   原 `_ext_suppl_net + _card_suppl_net` 会把卡商并回外部桶 —— 已撤销。
    if _ext_suppl_net:
        found_ext = False
        for x in source_distribution:
            if x["name"] == "外部投诉":
                x["count"] += _ext_suppl_net; found_ext = True; break
        if not found_ext:
            source_distribution.append({"name": "外部投诉", "count": _ext_suppl_net})
        source_distribution.sort(key=lambda x: -x["count"])

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

    # 三源趋势并入补充表(workbuddy推送, 与 /summary 同口径逐日去重):
    #   external_complaints(315) → 外部投诉。
    #   补充记录若已关联到本范围内工单(overlap) → 工单已计入, 去重; 否则按投诉日期计入。
    #   解决「推送了有数据、但趋势当天没数据」(推送记录未落工单/日期错位)。
    #
    # ★★ (#53) 卡商**不再并入外部趋势** ★★
    #   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
    #   原 `for r in _card_suppl_trend: b["外部投诉"] += net` 会把卡商并回外部 —— 已撤销。
    #   卡商趋势独立返回, 见 `card.trend`(= `_card_suppl_trend`)。
    for r in _ext_suppl_trend:
        net = int(r["total"] or 0) - int(r["overlap"] or 0)
        if net:
            b = trend_map.setdefault(r["day"], {})
            b["外部投诉"] = b.get("外部投诉", 0) + net

    trend = [
        {
            "day": d,
            "内部投诉": v.get("内部投诉", 0),
            "商户号投诉": v.get("商户号投诉", 0),
            "外部投诉": v.get("外部投诉", 0),
        }
        for d, v in sorted(trend_map.items())[:90]
    ]

    # ── 3.5 投诉水位(2026-08-25 v2 口径修正): 进水/出水必须对称, 否则净水位与积压不自洽
    #    进水 = 窗口内按日新增登记(register_time in window)
    #    出水 = 窗口内登记 且 已完成(register in window AND finish 真实完成, finish<=te)
    #          → 出水⊂进水, 净水位=进-出=窗口内登记仍未完成数, 累计净水位最终值 ≈ 当前积压量
    #    v1 缺陷: 出水把"窗口外登记、窗口内完成"的老投诉也算进去(它们的进水不在窗口内),
    #          导致出水虚高、净水位为负, 与积压快照(266)方向相反。
    finish_day = func.date_trunc("day", func.timezone("Asia/Shanghai", ServiceTicket.finish_time))
    in_rows = db.execute(
        _action_filter(
            _tk_base().with_only_columns(day_trunc, func.count()).group_by(day_trunc)
        )
    ).all()
    # 出水 = 窗口内登记 且 已完成; 已完成判定与积压闭环一致:
    #   真实完成(finish 晚于 register 3分钟+) OR 外部投诉工单关联315登记已办结
    _real_finish_cond = and_(
        ServiceTicket.finish_time.isnot(None),
        ServiceTicket.finish_time > ServiceTicket.register_time,
    )
    _done_cond = _real_finish_cond
    _ext_done_sns = _external_done_sns(db)
    if _ext_done_sns:
        _done_cond = or_(
            _real_finish_cond,
            and_(src_expr == "外部投诉", ServiceTicket.device_sn.in_(_ext_done_sns)),
        )
    out_stmt = (
        select(finish_day, func.count())
        .select_from(ServiceTicket)
        .where(_ticket_category_case() == "投诉")
        .where(_done_cond)
    )
    if ts:
        out_stmt = out_stmt.where(ServiceTicket.register_time >= ts)  # 与进水对称: 仅窗口内登记
    if te:
        out_stmt = out_stmt.where(ServiceTicket.register_time <= te)
    out_rows = db.execute(out_stmt.group_by(finish_day)).all()
    in_map = {str(r[0])[:10]: int(r[1]) for r in in_rows if r[0]}
    out_map = {str(r[0])[:10]: int(r[1]) for r in out_rows if r[0]}
    water_dates = sorted(set(in_map) | set(out_map))
    water_cum = 0
    water = []
    for d in water_dates:
        inn = in_map.get(d, 0)
        outt = out_map.get(d, 0)
        water_cum += (inn - outt)
        water.append({"day": d, "in": inn, "out": outt, "net": inn - outt, "cum_net": water_cum})

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

    # ── 5.2 商户号被投诉主体趋势(按日, 不同主体堆叠柱; 商户号内细分, 不合并同名公司) ──
    merchant_entity_day_rows = db.execute(
        _action_filter(
            _tk_base().with_only_columns(day_trunc, ServiceTicket.registrant, func.count())
            .where(src_expr == "商户号投诉")
            .where(ServiceTicket.registrant.isnot(None), ServiceTicket.registrant != "")
            .group_by(day_trunc, ServiceTicket.registrant)
        )
    ).all()
    entity_total_map: dict[str, int] = {}
    for r in merchant_entity_day_rows:
        ent = r[1] or "未标注"
        entity_total_map[ent] = entity_total_map.get(ent, 0) + int(r[2] or 0)
    # 主体按总量降序(全部列示, 商户号内细分到具体商户号)
    entity_order = [e for e, _ in sorted(entity_total_map.items(), key=lambda x: -x[1])]
    entity_trend_map: dict[str, dict[str, int]] = {}
    for r in merchant_entity_day_rows:
        day = str(r[0])[:10] if r[0] else ""
        ent = r[1] or "未标注"
        b = entity_trend_map.setdefault(day, {})
        b[ent] = b.get(ent, 0) + int(r[2] or 0)
    merchant_entity_trend = [
        {"day": d, **{e: v.get(e, 0) for e in entity_order}}
        for d, v in sorted(entity_trend_map.items())[:90]
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

    # F-P1-2: 三源 registered/handled(投诉分析页管理摘要双值; 口径同 /summary/_sources_reg_handled)
    _reg_handled = _sources_reg_handled(db, ts, te)
    # ★★ (#53) 卡商独立统计(不参与 _reg_handled / 总量) ★★
    _card_reg = _card_reg_handled(db, ts, te)
    # ★★ (#55) 卡商**事件口径** —— 与 /summary 的 supplementary.card 同实现 ★★
    #   渠道列用 `complaint_group`(卡商表无 platform, 群名即渠道)。
    #   前端据此展示「多渠道(非重复) / 真重复」两个指标, 见 ComplaintAnalysis.vue。
    _card_events = _suppl_event_metrics(
        db, CardComplaint, CardComplaint.complaint_date,
        CardComplaint.complaint_group, ts, te,
    )

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
            "reg_handled": _reg_handled,
            # ★★ (#53) (外)卡商 **完全独立** —— 不参与 complaint 总量 ★★
            #   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
            #   口径: 表自身行数(card_complaints 按 complaint_date 窗口), **不扣工单重叠**
            #         (实证: 工单表 service_item/service_category 含「卡商」均为 0 行)。
            "card": {
                "scope": CARD_SCOPE,
                "total": int(card_total),
                "registered": _card_reg["registered"],
                "handled": _card_reg["handled"],
                "trend": _card_suppl_trend,
                # ★★ (#55) 事件口径: 多渠道(非重复) vs 真重复(同渠道) ★★
                #   前端只读展示, 不改变 total/registered(原始行数口径保持兼容)。
                "events": _card_events["events"],
                "multi_channel_events": _card_events["multi_channel_events"],
                "true_dup_events": _card_events["true_dup_events"],
                "merged_rows": _card_events["merged_rows"],
                "needs_review": _card_events["needs_review"],
            },
            "source_distribution": source_distribution,
            "water": water,
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
            "merchant_entity_trend": merchant_entity_trend,
            "merchant_handler_distribution": merchant_handler_distribution,
            "timeliness": timeliness,
            "handler_efficiency": handler_efficiency,
            "external_platform_trend": external_platform_trend,
        }
    )
