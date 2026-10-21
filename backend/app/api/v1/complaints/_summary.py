"""三源投诉统计汇总: complaint_summary。"""

from sqlalchemy import and_, case, column, func, or_, select, table

from app.api.deps import CurrentUser, DBSession

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._helpers import (
    _card_reg_handled,
    _day_trunc,
    _external_done_sns,
    _handler_fallback_cols,
    _merge_trend,
    _norm_handler,
    _parse_dt,
    _parse_dt_end,
    _sources_reg_handled,
    _suppl_event_metrics,
    _suppl_trend_rows,
)
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.models.ticket import ServiceTicket

# #338: 投诉汇总读 std_all 镜像(_suppl_event_metrics 需 id/device_sn/complainant_phone/quality_status/problem)
_CC = table("std_card_complaint_all",
            column("id"), column("device_sn"), column("complainant_phone"),
            column("quality_status"), column("problem"),
            column("complaint_date"), column("complaint_group"),
            column("problem_category"), column("matched_ticket_no"))
_EC = table("std_external_complaint_all",
            column("id"), column("device_sn"), column("complainant_phone"),
            column("quality_status"), column("problem"),
            column("complaint_date"), column("platform"),
            column("matched_ticket_no"))
from app.schemas.common import R
from app.services.analytics import (
    _complaint_source_type_case,
    _ticket_category_case,
)
from app.services.complaint_scope import (
    CARD_SCOPE,
)


@router.get("/summary", response_model=R, summary="三源投诉统计汇总(工单为主,卡商/外部投诉为前期参考补充)")
@cached_json(lambda *a, **kw: f"complaint:summary:{kw.get('start')}:{kw.get('end')}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 60))
def complaint_summary(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
):
    """投诉统计汇总接口。

    口径说明(需求变更):
    - 三大分类(内部/商户号/外部)的 **总数/分类分布/处理人分布/趋势** 四要素
      以 service_tickets(工单) 为唯一主数据来源。
      这是运营实际考核与追踪的"主台账"。
    - card_complaints(卡商群) / external_complaints(监督局-315) 两张补充表
      作为"前期参考补充"——帮助识别"可能还没落成工单、因此工单口径里暂时缺失"
      的早期投诉线索。它们在返回值中单独位于 supplementary.external /
      supplementary.card 子对象(不合并进 by_category/by_handler),但**计数上
      计入对应三源桶**(315与卡商均→外部投诉, 2026-09-01 用户确认口径)。
      三源总数/趋势 = 工单 + 补充表(已关联本范围工单的按 matched 去重)。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    def _time_filter(reg_col, fin_col):
        """生成时间过滤条件:register_time在范围内 OR finish_time在范围内(真实完成).
        与 complaint_detail 保持一致口径,解决历史工单今日完成不出现在统计里的问题."""
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

    # ---- 商户号投诉 (主: service_tickets 微信类工单) ----
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

    # ---- 外部投诉 (service_tickets 平台类工单: 12315/消协/监所等) ----
    # ⚠️ #65: 工单侧口径已含 **#58 软纠正** —— `_complaint_source_type_case`
    #    默认传入 `ServiceTicket.device_sn`, 会把「误录为工信部、实为监所」
    #    的工单改判为外部投诉。
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

    # ---- (外)卡商工单侧 (item='工信部投诉' 且 SN 非监所) ----
    # ★★ #65: 卡商在工单侧**确实存在**(28 条/近3月), 需与补充表并集去重 ★★
    #   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析"。
    card_ticket_total = db.scalar(_tk_where(select(func.count())).where(src_expr == CARD_SCOPE)) or 0
    card_ticket_trend = db.execute(
        _tk_where(select(_day_trunc(ServiceTicket.register_time), func.count()).where(src_expr == CARD_SCOPE), use_action_time=False)
        .group_by(_day_trunc(ServiceTicket.register_time))
        .order_by(_day_trunc(ServiceTicket.register_time))
    ).all()

    # ---- (外)卡商 · 分类分布 (#75) ----
    # ★ 修复前此处缺失 → 前端「卡商投诉分类」图**永久空白**（后端返回 by_category=[]）。
    #   根因: 原注释写 "分类见 complaint_subs"，但该接口**从未存在**（死引用）；
    #        前端 ComplaintChannelPanel 只认 `by_category`，故恒为空。
    # 数据源: `card_complaints.problem_category`（生产填充率 552/764 = 72%）。
    # ⚠️ 口径: **仅补充表侧**分类 —— 工单侧卡商无 problem_category 字段（其 service_item
    #   只有「工信部投诉」一项，无区分度），故本图不尝试与 card_total 对齐；
    #   语义 = 「卡商投诉分类」的来源即该字段，NULL 归「未标注」（与其它三源同规则）。
    # ⚠️ limit 与三源不同(三源用 20): 卡商分类实测 **25 种**（长尾自由文本），
    #   用 20 会**截断类别**，导致"各分类之和 < 总数"的假象（实测 65 vs 70）。
    #   故放到 50 覆盖全集；图表可读性由前端做 Top-N + 「其他」聚合解决
    #   （前端聚合优于后端截断：后端截断会**丢数据**且无法察觉）。
    card_by_cat = db.execute(
        select(_CC.c.problem_category, func.count())
        .where(*([_CC.c.complaint_date >= ts] if ts else []))
        .where(*([_CC.c.complaint_date <= te] if te else []))
        .group_by(_CC.c.problem_category)
        .order_by(func.count().desc())
        .limit(50)
    ).all()

    # ---- 补充明细数据源统计(315外部投诉 / 卡商群投诉,workbuddy推送) ----
    # 口径: 两张补充表按投诉日期统计总数与"已关联服务工单"数。
    # 关联 = 按 device_sn 匹配到对应工单(matched_ticket_no 非空)。
    # 补充表与工单是同一批投诉的两种视角, 先统计以便三源总数合并去重。
    def _suppl_stats(model, date_col, matched_col):
        """补充表统计: 原始行数 + 已关联工单数 + ★事件口径(委托 `_suppl_event_metrics`)。

        ★ 2026-09-11 (#55): 事件口径已抽出为模块级 `_suppl_event_metrics`
          (与 `/analysis` 的 `card` 共用**同一实现**, 避免口径漂移)。
          ⚠️ `total` 保留为**原始行数**(向后兼容); 新代码应使用 `events`。
        """
        conds: list = []
        if ts:
            conds.append(date_col >= ts)
        if te:
            conds.append(date_col <= te)
        total = db.scalar(select(func.count()).select_from(model).where(*conds)) or 0
        matched = db.scalar(select(func.count()).select_from(model).where(matched_col.isnot(None), *conds)) or 0

        # ★ 渠道列: 315 用 platform; 卡商无 platform, 群名即渠道
        ch_col = (_CC.c.complaint_group
                  if model is _CC else _EC.c.platform)
        ev = _suppl_event_metrics(
            db, model, date_col, ch_col, ts, te, with_review_rows=True
        )
        return {"total": int(total), "matched": int(matched), **ev}

    supplementary = {
        "external": _suppl_stats(_EC, _EC.c.complaint_date, _EC.c.matched_ticket_no),
        "card": _suppl_stats(_CC, _CC.c.complaint_date, _CC.c.matched_ticket_no),
    }

    # ---- 三源总数合并补充表(去重) ----
    # 补充表(workbuddy推送)是对应主台账的更详细描述: 315→外部投诉工单, 卡商→外部投诉工单。
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
            conds.append(model.c.complaint_date >= ts)
        if te:
            conds.append(model.c.complaint_date <= te)
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
                .where(model.c.matched_ticket_no.isnot(None))
                .where(model.c.matched_ticket_no.in_(t_sub))
            )
            or 0
        )

    # ⚠️ 2026-09-10 (#47): 卡商**从外部桶拆出** → external_total 不再并入 card 补充表。
    #   原式(2026-09-01 口径): external = 工单 + 315净 + 卡商净  ← 卡商被并进来
    #   现式: external = 工单 + 315净
    external_total = int(external_ticket_total) + supplementary["external"]["total"] - _overlap_suppl(_EC, "外部投诉")
    # ★★ 2026-09-11 (#53) 卡商**独立**统计 ★★
    #   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
    #   ⚠️ 卡商**不进** `complaint_total`（不进三源合计）—— 这句仍成立。
    #
    # ★★ #65 口径修正: 卡商 = 工单侧真卡商 + 补充表 − 重叠 ★★
    #   #53 曾撤销卡商去重, 理由是「工单表 service_item/service_category 含「卡商」
    #   均为 0 行 → 工单侧无卡商分类」。
    #   ⚠️ 该断言的**检测方式有误** —— 卡商在工单侧**不以"卡商"字面存在**,
    #      而是以 `service_item='工信部投诉'` 存在（#57 已证实）。
    #   → #57 之后工单侧**已有**真卡商（近3月 28 条）, 老结论失效。
    #   实测: `card_complaints` 78 行 与 工单侧真卡商 SN 重叠 16 条 → 不去重会双计。
    #   公式与外部侧**完全平行**: 工单 + 补充表 − 重叠(matched 且指向本范围内卡商工单)。
    card_total = (
        int(card_ticket_total)
        + int(supplementary["card"]["total"])
        - _overlap_suppl(_CC, CARD_SCOPE)
    )
    merchant_total = int(merchant_ticket_total)

    # ---- 四源趋势合并补充表(逐日去重) ----
    # 趋势与总数同口径: 工单按日计数 + 补充表按投诉日期 (total - overlap)。
    # overlap = 当日已关联到本范围内工单的补充记录(工单已计入, 去重)。
    # 使「推送了但未落工单」的外部/卡商投诉也能在趋势里按投诉日期体现。
    # ⚠️ 2026-09-10 (#47): 卡商**独立成桶** —— 原先把卡商群投诉归入「外部投诉」桶
    #    (2026-09-01 口径), 现拆开: 外部趋势 = 工单 + 315净; 卡商趋势 = 卡商净。
    # ⚠️ 不可嵌套调用 _merge_trend(其第一参数要求 (day,count) 元组列表, 嵌套会把
    #    {day,count} 字典解包 → int('count') 崩溃)。两个补充表 dict 列表直接相加后一次合并。
    external_suppl_trend = _suppl_trend_rows(db, _EC, "外部投诉", ts, te)
    card_suppl_trend = _suppl_trend_rows(db, _CC, "(外)卡商", ts, te)
    external_trend_merged = _merge_trend(external_trend, external_suppl_trend)
    # ★★ #65: 卡商趋势 = 工单侧真卡商 + 补充表净（与 card_total 同口径）★★
    #   修复前: `_merge_trend([], card_suppl_trend)` —— 工单侧完全没进来,
    #   → 趋势逐日累加 **≠** 总数（违反「趋势和总数同口径」约定）。
    card_trend_merged = _merge_trend(card_ticket_trend, card_suppl_trend)
    merchant_trend_merged = _merge_trend(merchant_trend, [])
    internal_trend_merged = _merge_trend(internal_trend, [])  # 内部投诉无补充表

    def _handler_rows(rows):
        """归一化处理人后聚合 by_handler 结果,过滤系统值."""
        agg: dict[str, int] = {}
        for r in rows:
            nm = _norm_handler(r[0])
            if nm == "未分配":
                continue
            agg[nm] = agg.get(nm, 0) + int(r[1] or 0)
        return [{"name": k, "count": v} for k, v in sorted(agg.items(), key=lambda x: -x[1])]

    # ---- 积压未处理投诉(窗口内登记的未完成, 跟随 start/end; 不再无限累积历史遗留) ----
    # 口径(2026-08-25): 积压 = 投诉类工单, status 不在已完成类集合,
    #   且 register_time 落在 start~end 窗口内(默认近30天)。超出窗口的历史遗留不再计入,
    #   避免陈年失效数据无限堆叠污染"近期处理及时性"判断。
    _done_status_set = {"已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止"}
    _backlog_status_where = or_(
        ServiceTicket.status.is_(None),
        ServiceTicket.status == "",
        ~ServiceTicket.status.in_(_done_status_set),
    )
    _backlog_reg_conds = []
    if ts:
        _backlog_reg_conds.append(ServiceTicket.register_time >= ts)
    if te:
        _backlog_reg_conds.append(ServiceTicket.register_time <= te)
    # 积压闭环: 外部投诉工单关联的 315 登记已办结(result∈办结类) → 视为已完成, 不进积压
    _ext_done_sns = _external_done_sns(db)
    if _ext_done_sns:
        _backlog_reg_conds.append(
            or_(src_expr != "外部投诉", ServiceTicket.device_sn.notin_(_ext_done_sns))
        )
    _backlog_stmt = (
        select(func.count())
        .where(_ticket_category_case() == "投诉")
        .where(_backlog_status_where)
        .where(*_backlog_reg_conds)
    )
    backlog_total = db.scalar(_backlog_stmt) or 0
    # 按分类统计积压
    _backlog_cat_stmt = (
        select(ServiceTicket.service_item, func.count())
        .where(_ticket_category_case() == "投诉")
        .where(_backlog_status_where)
        .where(*_backlog_reg_conds)
        .group_by(ServiceTicket.service_item)
        .order_by(func.count().desc())
        .limit(20)
    )
    backlog_by_cat = [
        {"name": r[0] or "未标注", "count": int(r[1] or 0)}
        for r in db.execute(_backlog_cat_stmt).all()
    ]

    # ---- ★ #82: 积压**账龄分档**（全量，不限窗口）----
    #   【为什么新增而不是改上面那个 backlog.total】
    #     两者含义**确实不同**，不是同一指标的两个写法：
    #       · 上面 backlog.total = **窗口内登记**的未完成 → 衡量「近期消化压力」
    #       · 本段 aging         = **全量**未完成按账龄分档 → 衡量「历史包袱」
    #     实测（2026-09-13）差异极大：近30天窗口 46 条，而全量 436 条
    #     （其中 >90 天 286 条，最老 910 天）→ 只看窗口会**漏掉 89%**，
    #     让人误判「积压可控」。故**保留前者 + 新增后者**，并互相声明口径，
    #     避免「改口径」造成数字变动争议。
    #   ⚠️ 口径声明（#53）：本段**不含 (外)卡商** —— 卡商是独立统计体系，
    #     不参与投诉总量（用户明示「不纳入总量的管理, 全部单算」）。
    #     与上方 backlog 同判据（`_ticket_category_case()=="投诉"` + 未完成态）。
    #   ⚠️ 与 backlog 的「315 已办结视为完成」保持同一闭环规则（_ext_done_sns），
    #     否则两处会对不上（同源是唯一可靠做法，见 #81 教训）。
    #   时间基准: register_time 是 timestamptz，`now()` 在 SQL 侧同为 timestamptz
    #   → 直接用 SQL now() 求差，避免 Python 侧 naive/aware 混算。
    _age_days = func.extract("day", func.now() - ServiceTicket.register_time)
    _aging_conds = [
        _ticket_category_case() == "投诉",
        _backlog_status_where,
        ServiceTicket.register_time.isnot(None),
    ]
    if _ext_done_sns:
        _aging_conds.append(
            or_(src_expr != "外部投诉", ServiceTicket.device_sn.notin_(_ext_done_sns))
        )
    _aging_stmt = (
        select(
            func.sum(case((_age_days <= 7, 1), else_=0)),
            func.sum(case((and_(_age_days > 7, _age_days <= 30), 1), else_=0)),
            func.sum(case((and_(_age_days > 30, _age_days <= 90), 1), else_=0)),
            func.sum(case((_age_days > 90, 1), else_=0)),
            func.min(ServiceTicket.register_time),
            func.max(_age_days),
        )
        .where(*_aging_conds)
    )
    _ag = db.execute(_aging_stmt).first() or (0, 0, 0, 0, None, None)
    # 分档计数的 "空" 含义: 0 = 该档确无积压单（不是数据缺失）
    backlog_aging = {
        "d0_7": int(_ag[0] or 0),
        "d8_30": int(_ag[1] or 0),
        "d31_90": int(_ag[2] or 0),
        "d90_plus": int(_ag[3] or 0),
        "oldest_register": _ag[4].isoformat() if _ag[4] else None,
        # 最老积压单的账龄（天）——「陈年烂账有多老」的直观量
        "max_age_days": int(_ag[5]) if _ag[5] is not None else 0,
        "total": int((_ag[0] or 0) + (_ag[1] or 0) + (_ag[2] or 0) + (_ag[3] or 0)),
    }

    # F-P1-2: 三源 registered/handled(首页/运营看板三源 KPI 双值用)
    #   registered=窗口内新登记; handled=窗口内真实完成(finish落窗口, ≥3min, 含历史单今日办结)
    _reg_handled = _sources_reg_handled(db, ts, te)
    # ★★ (#53) 卡商独立统计(不参与 _reg_handled / 总量) ★★
    _card_reg = _card_reg_handled(db, ts, te)

    return R(
        data={
            "supplementary": supplementary,
            "reg_handled": _reg_handled,
            "backlog": {
                "total": int(backlog_total),
                "by_category": backlog_by_cat,
                # ★ #82: 全量账龄分档（与 total 口径不同 —— 见字段旁 `_spec`）
                "aging": backlog_aging,
                "_spec": {
                    "total": "积压 = 投诉类工单中未完成者，且 register_time 落在**本窗口内**（默认近30天）→ 衡量「近期消化压力」",
                    "aging": "账龄分档 = **全量**未完成投诉工单按 register_time 距今账龄分档（0-7/8-30/31-90/>90天）→ 衡量「历史包袱」",
                    "why_two": "两者口径不同、数字必然不等，属**有意设计**：窗口口径看近期压力，全量分档看历史包袱。只看窗口会漏掉陈年单（实测漏 89%）。",
                    "scope": "**不含 (外)卡商** —— 卡商按 #53 独立统计, 不纳入投诉总量",
                },
            },
            "internal": {
                "total": int(internal_total),
                "registered": _reg_handled["内部投诉"]["registered"],
                "handled": _reg_handled["内部投诉"]["handled"],
                "by_category": [{"name": r[0] or "未标注", "count": int(r[1] or 0)} for r in internal_by_cat],
                "by_handler": _handler_rows(internal_by_handler),
                "trend": internal_trend_merged,
            },
            "merchant": {
                "total": int(merchant_total),
                "registered": _reg_handled["商户号投诉"]["registered"],
                "handled": _reg_handled["商户号投诉"]["handled"],
                "by_category": [{"name": r[0] or "未标注", "count": int(r[1] or 0)} for r in merchant_by_cat],
                "by_handler": _handler_rows(merchant_by_handler),
                "trend": merchant_trend_merged,
            },
            "external": {
                "total": int(external_total),
                "registered": _reg_handled["外部投诉"]["registered"],
                "handled": _reg_handled["外部投诉"]["handled"],
                "by_category": [{"name": r[0] or "未标注", "count": int(r[1] or 0)} for r in external_by_cat],
                "by_handler": _handler_rows(external_by_handler),
                "trend": external_trend_merged,
            },
            # #47 (2026-09-10) 新增: (外)卡商 独立桶(原先并入 external)
            # ★★ (#53) 完全独立: 不参与 complaint 总量; 统计体系与投诉平行 ★★
            "card": {
                "total": int(card_total),
                "registered": _card_reg["registered"],
                "handled": _card_reg["handled"],
                # ★★ (#55) 事件口径: 多渠道(非重复) vs 真重复(同渠道) ★★
                #   由 `supplementary.card` 提供(同一 `_suppl_event_metrics` 实现),
                #   此处只做**上提**, 便于前端一个对象取全。
                "events": supplementary["card"]["events"],
                "multi_channel_events": supplementary["card"]["multi_channel_events"],
                "true_dup_events": supplementary["card"]["true_dup_events"],
                "merged_rows": supplementary["card"]["merged_rows"],
                "needs_review": supplementary["card"]["needs_review"],
                "by_category": [{"name": r[0] or "未标注", "count": int(r[1] or 0)} for r in card_by_cat],
                # ★★ 有意保持空数组 —— **不是** bug，是设计决策 ★★
                #   卡商不画「处理人排行」，原因:
                #     1) `handler` 为人员别名字段, 来源混合(含自由文本), 语义不可靠;
                #     2) 画了就是**误导** —— 无法区分"客服处理"与"诉求原文"。
                #   前端 `ComplaintChannelPanel.vue` 已用「卡商渠道说明」占据该列位，
                #   不留空。若要恢复该图，须先解决 handler 语义可信度（另立工单）。
                #   ⚠️ 原注释指向 `analytics.complaint_card_handlers`，该函数存在但
                #     **前端从未调用**（死引用）—— 已就地说明，避免下次被误判为缺陷。
                "by_handler": [],
                "trend": card_trend_merged,
            },
        }
    )
