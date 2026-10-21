"""投诉决策概览(单请求一屏): complaint_overview。"""

from datetime import datetime, timedelta

from fastapi import Query
from sqlalchemy import column, func, select, table

from app.api.deps import CurrentUser, DBSession

# #292: 复用积压页的设备四维分组函数(设备类型/套餐/商户号/激活时间段), 保证概览↔明细同源
from app.api.v1.complaints._backlog import _device_dim_groups

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._helpers import (
    _CONCLUSION_ORDER,
    _EXTERNAL_DONE_RESULTS,
    _MAX_CONCLUSIONS,
    _MAX_CREDIBLE_PCT,
    _MIN_BASE_FOR_PCT,
    _OVERDUE_DAYS,
    _REPEAT_THRESHOLD,
    _agg_by_bucket,
    _auto_grain,
    _complaint_refund_amount,
    _complaint_refund_rows,
    _day_trunc,
    _escalation_stats,
    _hour_heatmap,
    _kpi_item,
    _parse_dt,
    _parse_dt_end,
    _pct,
    _query_backlog_rows,
    _repeat_complaints,
    _sources_reg_handled,
    _suppl_trend_rows,
)
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.models.ticket import ServiceTicket

# #338: 投诉概览读 std_all 镜像
_CC = table("std_card_complaint_all", column("complaint_date"), column("result"), column("matched_ticket_no"))
_EC = table("std_external_complaint_all",
            column("complaint_date"), column("result"), column("matched_ticket_no"))
from app.schemas.common import R
from app.services.analytics import (
    TZ,
    _complaint_source_type_case,
    _ticket_category_case,
)
from app.services.complaint_scope import (
    CARD_SCOPE,
)


@router.get("/overview", response_model=R, summary="投诉决策概览(单请求出一屏: 结论条/KPI/四趋势/积压)")
@cached_json(
    lambda *a, **kw: f"complaint:overview:{kw.get('start')}:{kw.get('end')}:{kw.get('grain')}:{kw.get('compare')}",
    ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 60),
)
def complaint_overview(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    grain: str | None = Query(default=None, description="趋势粒度 day/week/month; 缺省按跨度自动"),
    compare: str = Query(default="prev", description="对比期 none/prev"),
):
    """投诉决策概览 —— 一次请求返回「一屏」所需的全部数据。

    ★ 为什么需要独立接口(#67 P1):
      原决策概览需要并发 8 个请求(summary/analysis/detail/backlog/subject/…),
      任一慢都会拖垮首屏, 且各接口**时间过滤口径不一致**(/analysis 只看 register_time,
      /summary 看 register OR finish) → 同一屏数字互相打架。
      本接口统一为 register OR finish, 并只返回概览所需的最小集合。

    返回块(与前端「决策概览」一一对应):
      ① kpi               —— 5 个核心数 + 环比(总量/登记/处理/积压/涉诉金额)
      ② conclusions       —— 自动归因结论条(后端生成, 口径可单测)
      ③ trend_stacked     —— 总量(分项堆积): 内/商/外 三源堆叠
      ④ card_trend        —— (外)卡商 **独立序列**(不并入 ③)
      ⑤ trend_reg_handled —— 登记 vs 处理(对称水位, 出水⊂进水)
      ⑥ trend_refund      —— 涉诉金额趋势(投诉 SN → 线上退款)
      ⑦ backlog           —— 总积压 + 龄期 + Top10 明细
      ⑧ ratios            —— 比率指标**占位**(恒 null, 见 #68 专项研究)

    ⚠️ 金额单位: 本模块沿用既有 `refund_amount` 口径 = **元**(Numeric(12,2)),
       与 `/subject-analysis` 一致, 保证概览与主体页金额可直接比对。
    """
    # ── 0. 解析时间范围(默认近 30 天), 推导粒度与对比期 ──
    today = datetime.now(TZ).date()

    def _date_or(s: str | None, fallback):
        if s:
            try:
                return datetime.strptime(s[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
        return fallback

    sd = _date_or(start, today - timedelta(days=29))
    ed = _date_or(end, today)
    if ed < sd:
        sd, ed = ed, sd
    span_days = (ed - sd).days + 1

    grain_eff = grain if grain in ("day", "week", "month") else _auto_grain(span_days)
    use_compare = compare == "prev"
    # 对比期 = 紧邻的等长前一段窗口
    ped = sd - timedelta(days=1)
    psd = ped - timedelta(days=span_days - 1)

    ts, te = _parse_dt(str(sd)), _parse_dt_end(str(ed))
    pts, pte = (_parse_dt(str(psd)), _parse_dt_end(str(ped))) if use_compare else (None, None)

    src_expr = _complaint_source_type_case()
    # ★ 堆叠趋势之和必须 ≡ KPI.total —— 提前取出作为对账基准(见下方趋势段的差额归桶)
    cur_total_for_stack = int(_sources_reg_handled(db, ts, te)["total"]["registered"])

    # ── 1. 区间指标(总量/登记/处理/涉诉金额) —— 当前期与对比期共用 ──
    def _window_metrics(w_ts, w_te) -> dict:
        """窗口内的基本量。

        · total     : **投诉总量(登记口径)** = 三源 registered 之和 = **堆叠趋势之和**
                      ⚠️ 与 `/summary` 的 total(register OR finish 活跃口径)**有意不同** ——
                        本页「总量」就是分项堆积趋势的合计，两者必须能对上，
                        否则同一屏里 KPI 与图表达不到自洽(历史上正是这个坑)。
                        完成但登记在窗口外的历史单，只体现在 handled 而不计入 total。
        · handled   : 窗口内完成(finish 落窗内 且 真实完成，剔卡商)
        · completion_rate: 窗口内登记 且 已完成 ÷ 窗口内登记（**对称口径**，≤100%）
        · refund_amt/refund_cnt: 窗口内**投诉涉及**的线上退款(按退款时间归窗)

        ★★ #53: 卡商**完全独立** —— 上述量**全部不含 `(外)卡商` 桶** ★★
           用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
        """
        src_rh = _sources_reg_handled(db, w_ts, w_te)["total"]
        registered = int(src_rh["registered"])
        handled = int(src_rh["handled"])
        # 对称完成数: 工单侧(窗口内登记 且 真实完成) + 315办结(投诉日期在窗内 且 未被工单计入)
        tk_conds = [
            _ticket_category_case() == "投诉",
            src_expr != CARD_SCOPE,
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.finish_time.isnot(None),
            ServiceTicket.finish_time > ServiceTicket.register_time,
        ]
        if w_ts:
            tk_conds.append(ServiceTicket.register_time >= w_ts)
        if w_te:
            tk_conds.append(ServiceTicket.register_time <= w_te)
        window_done = int(
            db.scalar(select(func.count()).select_from(ServiceTicket).where(*tk_conds)) or 0
        )
        # 315 已办结: 扣除「matched 到窗口内工单」的部分 —— 与 registered 的扣减对称，
        #   否则分子含、分母不含 → 完成率可能 >100%(长跨度实测 100.2%)。
        ed_conds = [_EC.c.complaint_date.isnot(None),
                    _EC.c.result.in_(_EXTERNAL_DONE_RESULTS)]
        if w_ts:
            ed_conds.append(_EC.c.complaint_date >= w_ts)
        if w_te:
            ed_conds.append(_EC.c.complaint_date <= w_te)
        ext_done_all = int(
            db.scalar(select(func.count()).select_from(_EC).where(*ed_conds)) or 0
        )
        matched_sub = select(ServiceTicket.ticket_no).where(
            _ticket_category_case() == "投诉",
            ServiceTicket.register_time.isnot(None),
            *([ServiceTicket.register_time >= w_ts] if w_ts else []),
            *([ServiceTicket.register_time <= w_te] if w_te else []),
        )
        ext_done_overlap = int(
            db.scalar(
                select(func.count()).select_from(_EC).where(
                    *ed_conds,
                    _EC.c.matched_ticket_no.isnot(None),
                    _EC.c.matched_ticket_no.in_(matched_sub),
                )
            ) or 0
        )
        window_done += max(ext_done_all - ext_done_overlap, 0)
        refund_cnt, refund_amt = _complaint_refund_amount(db, w_ts, w_te, src_expr)
        return {
            "total": registered,
            "registered": registered,
            "handled": handled,
            "window_done": window_done,
            "completion_rate": round(window_done / registered * 100.0, 1) if registered else None,
            "refund_cnt": int(refund_cnt),
            "refund_amt": float(refund_amt),
        }

    cur = _window_metrics(ts, te)
    prev = _window_metrics(pts, pte) if use_compare else None

    # ── 2. 积压(复用 _query_backlog_rows, 保证与「执行跟踪」页逐条一致) ──
    #   ★ #53: 概览的总积压**剔除卡商行** —— 卡商独立体系，不计入投诉积压。
    #     ⚠️ 仅在本接口过滤，**不**改 `_query_backlog_rows`(否则会改到「执行跟踪」页)。
    backlog_rows = [r for r in _query_backlog_rows(db, ts, te) if r.get("source") != CARD_SCOPE]
    b_total = len(backlog_rows)
    b_overdue = sum(1 for r in backlog_rows if (r.get("aging_days") or 0) > _OVERDUE_DAYS)
    b_unassigned = sum(1 for r in backlog_rows if r.get("handler") == "未分配")
    b_max_aging = max((r.get("aging_days") or 0) for r in backlog_rows) if backlog_rows else 0
    by_source_cnt: dict[str, int] = {}
    by_aging_cnt: dict[str, int] = {}
    for r in backlog_rows:
        by_source_cnt[r.get("source") or "未标注"] = by_source_cnt.get(r.get("source") or "未标注", 0) + 1
        a = r.get("aging_days") or 0
        bucket = "≤3天" if a <= 3 else ("4-7天" if a <= 7 else ("8-14天" if a <= 14 else ">14天"))
        by_aging_cnt[bucket] = by_aging_cnt.get(bucket, 0) + 1

    # ── 3. KPI 卡(带环比) ──
    prev_backlog = None
    if use_compare:
        # 对比期积压 = 对比期末仍在积压(同口径重算, 保证可比; 同样剔除卡商)
        prev_backlog = len([
            r for r in _query_backlog_rows(db, pts, pte) if r.get("source") != CARD_SCOPE
        ])
    kpi = [
        _kpi_item("total", "投诉总量", cur["total"], prev["total"] if prev else None, "条"),
        _kpi_item("handled", "已处理", cur["handled"], prev["handled"] if prev else None, "条"),
        # 完成率是比率 → 环比用**百分点差**(pp)，不是百分比变化
        {
            "key": "completion_rate", "label": "完成率", "value": cur["completion_rate"],
            "prev": prev["completion_rate"] if prev else None,
            "delta_pp": (
                round(cur["completion_rate"] - prev["completion_rate"], 1)
                if prev and cur["completion_rate"] is not None and prev["completion_rate"] is not None
                else None
            ),
            "delta_pct": None, "unit": "%",
        },
        _kpi_item("backlog", "总积压", b_total, prev_backlog, "条"),
        _kpi_item("refund_amount", "涉诉退款金额", round(cur["refund_amt"], 2),
                  round(prev["refund_amt"], 2) if prev else None, "元"),
    ]

    # ── 4. 趋势①: 总量(分项堆积) 内/商/外 —— 按登记日 ──
    day_trunc = _day_trunc(ServiceTicket.register_time)
    trend_stmt = select(day_trunc, src_expr, func.count()).select_from(ServiceTicket).where(
        _ticket_category_case() == "投诉"
    )
    if ts:
        trend_stmt = trend_stmt.where(ServiceTicket.register_time >= ts)
    if te:
        trend_stmt = trend_stmt.where(ServiceTicket.register_time <= te)
    day_map: dict[str, dict] = {}
    for d, src, cnt in db.execute(
        trend_stmt.group_by(day_trunc, src_expr).order_by(day_trunc)
    ).all():
        if not d:
            continue
        # ★ #53: 卡商桶**不进**投诉总量堆叠(独立序列见 card_trend)
        if (src or "") == CARD_SCOPE:
            continue
        key = str(d)[:10]
        b = day_map.setdefault(key, {"内部投诉": 0, "商户号投诉": 0, "外部投诉": 0})
        b[src or "内部投诉"] = b.get(src or "内部投诉", 0) + int(cnt or 0)
    # 并入 315 补充表净新增(与 /summary 同口径); ⚠️ 卡商**不并入**(#53)
    #   ⚠️ 必须用 `+=` 逐日累加(不清零 net 负值) —— 与 `_source_reg_handled` 的
    #      `max(ext_reg, 0)` 是在**总和**层面截断，若这里对逐日净额逐个 max(0) 会改变总量。
    for r in _suppl_trend_rows(db, _EC, "外部投诉", ts, te):
        b = day_map.setdefault(r["day"], {"内部投诉": 0, "商户号投诉": 0, "外部投诉": 0})
        b["外部投诉"] = b.get("外部投诉", 0) + int(r["total"] or 0) - int(r["overlap"] or 0)
    trend_stacked = []
    for b in _agg_by_bucket(day_map, grain_eff):
        # 逐桶与总量口径对齐: 每日净额可能为负(重叠>新增)，按桶 floor(0) 后
        # 再把"全期累积差额"补到最后一个桶，保证 `堆叠之和 ≡ KPI.total`。
        trend_stacked.append(dict(b))
    for b in trend_stacked:
        for k in ("内部投诉", "商户号投诉", "外部投诉"):
            b[k] = max(int(b.get(k, 0)), 0)
    _stack_sum = sum(b["内部投诉"] + b["商户号投诉"] + b["外部投诉"] for b in trend_stacked)
    _diff = cur_total_for_stack - _stack_sum
    if _diff and trend_stacked:
        # 差额归入最后一个桶的外部投诉(占比最小，影响最轻)
        trend_stacked[-1]["外部投诉"] += _diff
        if trend_stacked[-1]["外部投诉"] < 0:
            trend_stacked[-1]["外部投诉"] = 0

    # ── 4.1 (外)卡商独立趋势(不并入总量堆叠, #53) ──
    card_day: dict[str, dict] = {}
    for r in _suppl_trend_rows(db, _CC, "外部投诉", ts, te):
        net = int(r["total"] or 0) - int(r["overlap"] or 0)
        if net:
            b = card_day.setdefault(r["day"], {"registered": 0})
            b["registered"] = b.get("registered", 0) + net
    card_trend = _agg_by_bucket(card_day, grain_eff)

    # ── 5. 趋势②: 登记 vs 处理(对称水位 v2: 出水⊂进水) ──
    #   ★ 与「总积压」**同源同口径**(_query_backlog_rows)，保证
    #      `累计净水位(末桶) ≡ 总积压`。历史上这两处因各自实现完成判定而互相打架。
    #   进水 = 窗口内按登记日新增(含 315 补充表净额);
    #   出水 = 进水 − 截至当日仍未完成 —— “仍未完成”直接复用积压判定，无法漂移。
    in_stmt = select(day_trunc, func.count()).select_from(ServiceTicket).where(
        _ticket_category_case() == "投诉", src_expr != CARD_SCOPE
    )
    if ts:
        in_stmt = in_stmt.where(ServiceTicket.register_time >= ts)
    if te:
        in_stmt = in_stmt.where(ServiceTicket.register_time <= te)
    in_map = {str(d)[:10]: int(c or 0) for d, c in db.execute(in_stmt.group_by(day_trunc)).all() if d}
    # 进水并入 315 补充表净新增(与总量口径一致)，否则净水位末端与积压对不上
    for r in _suppl_trend_rows(db, _EC, "外部投诉", ts, te):
        in_map[r["day"]] = in_map.get(r["day"], 0) + int(r["total"] or 0) - int(r["overlap"] or 0)

    # 按登记日汇总"仍未完成"(与积压页同一条判定链: _query_backlog_rows 的结果)
    _pending_by_day: dict[str, int] = {}
    for r in backlog_rows:
        d = str(r.get("register_time") or "")[:10]
        if d:
            _pending_by_day[d] = _pending_by_day.get(d, 0) + 1

    rh_day: dict[str, dict] = {}
    for d in sorted(set(in_map) | set(_pending_by_day)):
        inn = in_map.get(d, 0)
        rh_day[d] = {"registered": inn, "handled": inn - _pending_by_day.get(d, 0)}
    trend_reg_handled = _agg_by_bucket(rh_day, grain_eff)
    cum = 0
    for b in trend_reg_handled:
        b["net"] = b.get("registered", 0) - b.get("handled", 0)
        cum += b["net"]
        b["cum_net"] = cum

    # ── 6. 趋势③: 涉诉金额(投诉 SN → 线上退款, 按退款时间归桶) ──
    ref_day: dict[str, dict] = {}
    for d, cnt, amt in _complaint_refund_rows(db, ts, te, src_expr):
        if not d:
            continue
        b = ref_day.setdefault(str(d)[:10], {"count": 0, "amount": 0.0})
        b["count"] += int(cnt or 0)
        b["amount"] += float(amt or 0)
    trend_refund = _agg_by_bucket(ref_day, grain_eff)

    # ── 7. 升级率(内部/商户号 → 外部 的同一设备占比) ──
    sn_total, sn_escalated = _escalation_stats(db, ts, te, src_expr)
    escalation = {
        "sns_total": sn_total,
        "sns_escalated": sn_escalated,
        "rate": round(sn_escalated / sn_total * 100.0, 1) if sn_total else None,
    }

    # ── 7.5 A5: 投诉登记时段热力(#72) —— 服务排班决策 ──
    hour_heatmap = _hour_heatmap(db, ts, te, src_expr)

    # ── 7.6 A2: 反复投诉(#72) —— 事件口径(SN+北京日期)，非记录口径 ──
    repeat_stats = _repeat_complaints(db, ts, te, src_expr, threshold=_REPEAT_THRESHOLD)

    # ── 8. 结论条(后端生成: 口径统一 + 可单测; 前端只渲染) ──
    conclusions: list[dict] = []
    src_rh_cur = _sources_reg_handled(db, ts, te)
    _baseline_weak = False  # 对比期数据覆盖不足标记(用于给出"为什么没有环比"的说明)
    if prev:
        src_rh_prev = _sources_reg_handled(db, pts, pte)
        for src, tab in (("外部投诉", "external"), ("内部投诉", "internal"), ("商户号投诉", "merchant")):
            cv = src_rh_cur[src]["registered"]
            pv = src_rh_prev[src]["registered"]
            p = _pct(cv, pv)
            # 最小基数护栏: 对比期基数 <5 条时不给百分比结论(防小基数放大)
            if p is None or pv < _MIN_BASE_FOR_PCT:
                continue
            # ★ 可信上限护栏: |p| 超 300% 说明对比期几乎无数据(如窗口落在系统上线前)，
            #   这是"基线无效"而非"业务暴增" —— 报出来反而误导决策，故抑制。
            if abs(p) > _MAX_CREDIBLE_PCT:
                _baseline_weak = True
                continue
            if abs(p) >= 20:
                conclusions.append({
                    "level": "danger" if p > 0 else "ok",
                    "text": f"{src}环比 {'+' if p > 0 else ''}{p}%（{pv}→{cv} 条）",
                    "drill": {"tab": tab},
                })
        # 基线不足时显式说明(优于静默无环比 —— 使用者会追问"为什么没数据")
        if _baseline_weak and not conclusions:
            conclusions.append({
                "level": "info",
                "text": f"对比期（{psd}~{ped}）数据覆盖不足，本期未输出环比结论",
                "drill": {"tab": "overview"},
            })
    if b_overdue:
        conclusions.append({
            "level": "warn",
            "text": f"积压超期 {b_overdue} 条（>{_OVERDUE_DAYS} 天），最旧 {b_max_aging} 天",
            "drill": {"tab": "backlog", "overdue": True},
        })
    if escalation["rate"] is not None:
        conclusions.append({
            "level": "warn" if escalation["rate"] >= 20 else "ok",
            "text": f"升级率 {escalation['rate']}%（{sn_escalated}/{sn_total} 台设备：内部或商户号投诉后升级到外部）",
            "drill": {"tab": "subject"},
        })
    if cur["refund_amt"] > 0:
        rp = _pct(cur["refund_amt"], prev["refund_amt"]) if prev else None
        suffix = f"，环比 {'+' if rp > 0 else ''}{rp}%" if rp is not None else ""
        conclusions.append({
            "level": "info",
            "text": f"涉诉退款 ¥{cur['refund_amt']:,.2f}（{cur['refund_cnt']} 笔）{suffix}",
            "drill": {"tab": "external"},
        })
    # ★ #72 A2: 反复投诉（事件口径）—— 「第一次没解决好」的直接证据
    if repeat_stats["repeat_units"]:
        _esc = sum(1 for i in repeat_stats["items"] if i["escalated"])
        _esc_txt = f"，其中 {_esc} 台已升级到外部" if _esc else ""
        conclusions.append({
            "level": "warn",
            "text": (
                f"反复投诉 {repeat_stats['repeat_units']} 台设备"
                f"（同一设备 ≥{repeat_stats['threshold']} 次投诉事件）{_esc_txt}"
            ),
            "drill": {"tab": "overview"},
        })
    conclusions.sort(key=lambda c: _CONCLUSION_ORDER.index(c["level"]))
    conclusions = conclusions[:_MAX_CONCLUSIONS]

    # ── 9. 设备维度四维统计(#292): 窗口内全量投诉工单(剔卡商) SN 按设备类型/套餐/商户号/激活时间段 ──
    #   复用积压页 _device_dim_groups, 保证概览四维 ↔ 明细下钻同源自洽(点击桶 → 筛选行数 = 桶计数)。
    #   与积压页差异: 概览用窗口内**全量**工单(不限积压), 回答「本期投诉落在什么设备上」。
    _all_ticket_rows = db.execute(
        select(ServiceTicket.device_sn, ServiceTicket.register_time).where(
            _ticket_category_case() == "投诉",
            src_expr != CARD_SCOPE,
            ServiceTicket.register_time.isnot(None),
            *([ServiceTicket.register_time >= ts] if ts else []),
            *([ServiceTicket.register_time <= te] if te else []),
        )
    ).all()
    _device_dim_rows = [{"device_sn": r[0], "register_time": r[1]} for r in _all_ticket_rows]
    device_dims = _device_dim_groups(db, _device_dim_rows)

    return R(data={
        "range": {
            "start": str(sd), "end": str(ed), "grain": grain_eff, "compare": compare if use_compare else "none",
            **({"prev_start": str(psd), "prev_end": str(ped)} if use_compare else {}),
        },
        "kpi": kpi,
        "kpi_meta": {
            # 口径注释(供前端 ⓘ 气泡直接渲染, 避免前端硬编码口径描述)
            "total": "本期窗口内登记的投诉量(三源合计，不含(外)卡商)，= 总量堆积趋势之和",
            "handled": "本期窗口内完成的投诉(含登记在窗口外的历史单)",
            "completion_rate": "窗口内登记的投诉中已办结的占比（对称口径，≤100%）",
            "backlog": f"截至窗口末仍未完成(超期阈值 >{_OVERDUE_DAYS} 天)，不含(外)卡商",
            "refund_amount": "本期投诉涉及 SN 关联的线上退款金额(元)",
        },
        "conclusions": conclusions,
        "trend_stacked": trend_stacked,
        "card_trend": card_trend,
        "trend_reg_handled": trend_reg_handled,
        "trend_refund": trend_refund,
        "escalation": escalation,
        # ★ #72 A5: 投诉登记时段热力（服务排班决策）
        "hour_heatmap": hour_heatmap,
        # ★ #72 A2: 反复投诉（事件口径，非记录口径）
        "repeat": repeat_stats,
        "backlog": {
            "total": b_total, "overdue": b_overdue, "unassigned": b_unassigned, "max_aging": b_max_aging,
            "by_source": [{"name": k, "count": v} for k, v in sorted(by_source_cnt.items(), key=lambda x: -x[1])],
            "by_aging": [{"name": k, "count": by_aging_cnt.get(k, 0)} for k in ("≤3天", "4-7天", "8-14天", ">14天")],
            "items": backlog_rows[:10],
        },
        # ★ #292: 设备维度四维统计(窗口内全量投诉工单, 剔卡商)
        #   设备类型/套餐/商户号/激活时间段; 每桶可下钻到明细(带时间窗口与筛选条件)
        "device_dims": device_dims,
        # ★ 比率指标占位: 数据完整度不足, 本期恒 null(见 #68 专项研究)
        "ratios": None,
    })
