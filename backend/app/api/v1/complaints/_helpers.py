"""投诉域共享辅助函数与常量: 时间窗口/三源登记处理口径/主体归一/积压完成集/趋势聚合(零路由依赖)。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, and_, func, or_, select

from app.api.deps import DBSession
from app.models.card_complaint import CardComplaint
from app.models.complaint_audit import ComplaintAudit
from app.models.external_complaint import ExternalComplaint
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.services.analytics import (
    REAL_FINISH_EPOCH_SECS,
    TZ,
    _complaint_source_type_case,
    _ticket_category_case,
    handler_fallback_expr,
)
from app.services.complaint_scope import (
    CARD_SCOPE,
    EVENT_STRICT_MIN_YEAR,
    STATUS_DONE,
    card_result_state,
    dedup_events,
)


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

def _merge_trend(trend_rows, suppl_rows):
    """把补充表(workbuddy推送)趋势并入工单趋势, 逐日去重.

    - trend_rows : 工单按日计数 [(day, count)], 与 _trend 输入一致
    - suppl_rows : _suppl_trend_rows 输出 [{day, total, overlap}]
    合并口径: 工单计数 + 补充表当日( total - overlap )
    (overlap = 已关联到本范围内工单的补充记录, 工单侧已计入, 去重不计)。
    返回 [{day, count}], 与 _trend 输出结构一致, 保证 逐日累加 == 总数口径。
    """
    merged: dict[str, int] = {}
    for d, c in trend_rows:
        if d:
            merged[str(d)[:10]] = merged.get(str(d)[:10], 0) + int(c or 0)
    for r in suppl_rows:
        day = r["day"]
        net = int(r["total"] or 0) - int(r["overlap"] or 0)
        if net:
            merged[day] = merged.get(day, 0) + net
    return [{"day": d, "count": merged[d]} for d in sorted(merged)]

def _day_trunc(col):
    """按 Asia/Shanghai 时区截断到日, 避免 UTC 凌晨被算到前一天.
    与 analytics.py 的 func.date_trunc("day", func.timezone("Asia/Shanghai", col)) 同口径.
    所有投诉日期字段(register_time/complaint_date)都是 timestamptz,
    直接 date_trunc('day', col) 会按 UTC 截断, 导致 CST 00:00~07:59 的记录落到前一天。"""
    return func.date_trunc("day", func.timezone("Asia/Shanghai", col))

def _suppl_trend_rows(
    db,
    model,
    src_expr_val: str,
    ts: datetime | None,
    te: datetime | None,
):
    """补充表(workbuddy推送)按投诉日期分组的趋势行, 含'已关联本范围内工单'的计数.

    返回 [{day, total, overlap}], 其中:
      - total   = 投诉日期在本范围内 的补充记录数(全部, 不论是否关联工单)
      - overlap = 其中已关联到"本统计范围内"工单(matched_ticket_no 对应工单 register/finish
                  也在范围内)的记录数。这部分与工单在趋势里是同一投诉的两视角, 需去重:
                  趋势计入时 total - overlap(仅计未落范围内工单的部分)。
    口径与 /summary 的 _overlap_suppl / 三源总数合并完全一致, 保证 趋势逐日累加 == 总数。
    """
    conds = []
    if ts:
        conds.append(model.complaint_date >= ts)
    if te:
        conds.append(model.complaint_date <= te)

    # 本范围内 投诉类 且 归属指定三源 的工单号子查询(与 _overlap_suppl 同口径:
    # 补充记录关联工单的 register_time 或真实 finish_time 在本范围内 → 视为工单已计入)
    t_sub = (
        select(ServiceTicket.ticket_no)
        .where(_ticket_category_case() == "投诉")
        .where(_complaint_source_type_case() == src_expr_val)
    )
    if ts or te:
        # register 在范围内 OR (finish 非空 且 finish 在范围内)
        reg_conds, fin_conds = [], []
        if ts:
            reg_conds.append(ServiceTicket.register_time >= ts)
            fin_conds.append(and_(ServiceTicket.finish_time.isnot(None), ServiceTicket.finish_time >= ts))
        if te:
            reg_conds.append(ServiceTicket.register_time <= te)
            fin_conds.append(and_(ServiceTicket.finish_time.isnot(None), ServiceTicket.finish_time <= te))
        t_sub = t_sub.where(or_(and_(*reg_conds), and_(*fin_conds)))

    day_expr = _day_trunc(model.complaint_date)
    total_rows = db.execute(
        select(day_expr, func.count())
        .select_from(model)
        .where(*conds)
        .group_by(day_expr)
    ).all()
    overlap_rows = db.execute(
        select(day_expr, func.count())
        .select_from(model)
        .where(*conds)
        .where(model.matched_ticket_no.isnot(None))
        .where(model.matched_ticket_no.in_(t_sub))
        .group_by(day_expr)
    ).all()
    total_map = {str(r[0])[:10]: int(r[1]) for r in total_rows if r[0]}
    overlap_map = {str(r[0])[:10]: int(r[1]) for r in overlap_rows if r[0]}
    return [
        {"day": d, "total": total_map.get(d, 0), "overlap": overlap_map.get(d, 0)}
        for d in sorted(set(total_map) | set(overlap_map))
    ]

def _handler_fallback_cols():
    """工单处理人多级 fallback: handler → assignee → updated_by_raw → registrant

    ★ #87: 实现已上提到 `analytics.handler_fallback_expr`（单一来源）——
       本函数保留仅为兼容既有调用点，**内部委派**，勿在此重写逻辑。
    """
    return handler_fallback_expr(ServiceTicket)



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



# ═══════════════════════════════════════════════════════════════════
# F-P1-2 (2026-09-04): 三源 × registered/handled 口径共享函数
#   registered = 窗口内「新登记」(register_time ∈ 窗口)
#   handled    = 窗口内「真实完成」(finish_time ∈ 窗口 且 finish − register ≥ 3min,
#                即 _is_real_finish 语义; 排除同瞬填充/批量覆盖)。**含窗口外登记、
#                窗口内办结的历史单** —— 用户 2026-09-04 拍板: "当日处理=finish 落当日即算"。
#   供 /summary 与 /analysis 复用(与 external._complaint_enhanced 同口径, 避免漂移)。
#   注意: 本口径与投诉水位 water.out(v2 自洽: 出水⊂进水, 仅窗口内登记且完成)不同,
#         water 保留 v2(净水位=积压); registered/handled 作为独立"登记/处理"KPI 展示。
# ═══════════════════════════════════════════════════════════════════
# 工单「真实完成」阈值（秒）—— ★ #87 单一来源: `analytics.REAL_FINISH_EPOCH_SECS`。
# 原先本模块自写 180，与 analytics 侧各一份，存在漂移风险（参见 #83 退款笔数教训）。
_REAL_FINISH_EPOCH_SECS = REAL_FINISH_EPOCH_SECS

def _source_reg_handled(db, src_val: str, ts, te,
                        include_suppl_external: bool = True) -> tuple[int, int]:
    """返回某三源桶的 (registered, handled)。

    - registered: 工单 register_time ∈ [ts,te]; external 桶再并入 315/卡商补充表
      净新增(complaint_date∈窗口 − 已 matched 到窗口内登记工单的重叠), 与 /summary 三源
      总数 external_total 的合并口径一致。
    - handled: 工单 finish_time ∈ [ts,te] 且真实完成(finish−register≥3min)。
      external 桶另并入 315(external_complaints)补充表「已办结」(result∈办结类):
      315 外部投诉大量由 workbuddy 推送进补充表、未必同步生成/更新工单, 若不并入,
      补充表已办结会被永久当"未处理" → 外部投诉 daily 处理率虚低、积压恒高(2026-09-06
      缺陷, 用户确认口径: 补充表已办结 = 已处理)。去重: 已办结但 matched 工单已在
      工单侧计入 handled 的排除(避免同一笔双算)。卡商(card_complaints)不并入:
      result 语义不同(已解决/已退款完成/投诉错误...), 无办结集合, 保持不进 handled。
    """
    src_expr = _complaint_source_type_case()

    def _reg_where():
        conds = [
            _ticket_category_case() == "投诉",
            src_expr == src_val,
            ServiceTicket.register_time.isnot(None),
        ]
        if ts:
            conds.append(ServiceTicket.register_time >= ts)
        if te:
            conds.append(ServiceTicket.register_time <= te)
        return conds

    def _fin_where():
        conds = [
            _ticket_category_case() == "投诉",
            src_expr == src_val,
            ServiceTicket.finish_time.isnot(None),
            ServiceTicket.register_time.isnot(None),
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= _REAL_FINISH_EPOCH_SECS,
        ]
        if ts:
            conds.append(ServiceTicket.finish_time >= ts)
        if te:
            conds.append(ServiceTicket.finish_time <= te)
        return conds

    registered = db.scalar(select(func.count()).select_from(ServiceTicket).where(*_reg_where())) or 0
    handled = db.scalar(select(func.count()).select_from(ServiceTicket).where(*_fin_where())) or 0

    # ⚠️ 2026-09-10 (#47): 卡商**独立成桶** `(外)卡商` —— 不再并入外部桶。
    #   卡商来源=card_complaints 补充表(工单侧无卡商分类)。
    #   ⚠️ 卡商 handled 口径与 315 **不同**: 卡商 result 是**混合语义字段**
    #      (实测 117 种取值, 含自由文本诉求), 故用**前缀锚定**而非枚举 in_()。
    #
    #   ⚠️ 本分支必须**独立于** `src_val == "外部投诉"` 那个 if 块 ——
    #      否则(我曾犯错)卡商永远进不去, 表现为「total 有值但 registered/handled 全 0」。
    if src_val == "(外)卡商":
        _cc_conds = [CardComplaint.complaint_date.isnot(None)]
        if ts:
            _cc_conds.append(CardComplaint.complaint_date >= ts)
        if te:
            _cc_conds.append(CardComplaint.complaint_date <= te)
        # ★★ #65: 卡商 registered = **工单侧真卡商 + 补充表 − 重叠** ★★
        #   ⚠️ #53 曾用「表自身行数」(不扣重), 依据是「工单侧无卡商分类」。
        #      #57 证明该检测方式有误 —— 卡商在工单侧以 `item='工信部投诉'` 形式存在。
        #      工单侧现为 `src_expr == CARD_SCOPE`(即软纠正**之后**的真卡商),
        #      与补充表平行去重, 与 `/summary` 的 `card_total` **同公式**。
        _reg_tk = db.scalar(
            select(func.count()).select_from(ServiceTicket).where(
                _ticket_category_case() == "投诉",
                src_expr == CARD_SCOPE,
                ServiceTicket.register_time.isnot(None),
                *([ServiceTicket.register_time >= ts] if ts else []),
                *([ServiceTicket.register_time <= te] if te else []),
            )
        ) or 0
        _tot_cc = db.scalar(
            select(func.count()).select_from(CardComplaint).where(*_cc_conds)
        ) or 0
        # 重叠 = 补充表已 matched 到「本范围内卡商工单」的条数
        _ov_sub = select(ServiceTicket.ticket_no).where(
            _ticket_category_case() == "投诉",
            src_expr == CARD_SCOPE,
            ServiceTicket.register_time.isnot(None),
            *([ServiceTicket.register_time >= ts] if ts else []),
            *([ServiceTicket.register_time <= te] if te else []),
        )
        _ov_cc = db.scalar(
            select(func.count()).select_from(CardComplaint)
            .where(*_cc_conds, CardComplaint.matched_ticket_no.isnot(None),
                   CardComplaint.matched_ticket_no.in_(_ov_sub))
        ) or 0
        _reg_cc = int(_reg_tk) + int(_tot_cc) - int(_ov_cc)
        # handled: 补充表前缀锚定 + **工单侧真实完成**(#65 新增)
        #   ⚠️ 卡商 result 是混合语义字段(实测 117 种取值) → 用前缀锚定, 非枚举
        _done_rows = db.execute(
            select(CardComplaint.result).where(*_cc_conds)
        ).scalars().all()
        _hdl_cc = sum(1 for v in _done_rows if card_result_state(v) == STATUS_DONE)
        # ★ 卡商工单侧已办结(与 315 分支同口径: finish 落窗口 且 真实完成)
        _hdl_tk = db.scalar(
            select(func.count()).select_from(ServiceTicket).where(
                _ticket_category_case() == "投诉",
                src_expr == CARD_SCOPE,
                ServiceTicket.finish_time.isnot(None),
                ServiceTicket.register_time.isnot(None),
                func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= _REAL_FINISH_EPOCH_SECS,
                *([ServiceTicket.finish_time >= ts] if ts else []),
                *([ServiceTicket.finish_time <= te] if te else []),
            )
        ) or 0
        return int(_reg_cc), int(_hdl_cc) + int(_hdl_tk)

    # external 桶: 并入补充表(315)
    if include_suppl_external and src_val == "外部投诉":
        def _overlap(model, done_only: bool = False, handled_side: bool = False):
            """补充记录 matched_ticket_no 指向"窗口内登记投诉工单"的条数.

            done_only=True: 只数 result∈办结类 的补充记录(供 handled 并入去重用)。
            handled_side=True: 只数 matched 工单"窗口内真实完成"的那部分
              (即工单侧 handled 已计; 补充办结若指向这类工单则不应重复计入)。
            """
            sub_conds = [
                _ticket_category_case() == "投诉",
            ]
            if handled_side:
                sub_conds += [
                    ServiceTicket.finish_time.isnot(None),
                    ServiceTicket.register_time.isnot(None),
                    func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= _REAL_FINISH_EPOCH_SECS,
                ]
            else:
                sub_conds.append(ServiceTicket.register_time.isnot(None))
            if ts:
                sub_conds.append(ServiceTicket.register_time >= ts if not handled_side else ServiceTicket.finish_time >= ts)
            if te:
                sub_conds.append(ServiceTicket.register_time <= te if not handled_side else ServiceTicket.finish_time <= te)
            sub = select(ServiceTicket.ticket_no).where(*sub_conds)
            conds = [
                model.complaint_date.isnot(None),
                model.matched_ticket_no.isnot(None),
                model.matched_ticket_no.in_(sub),
            ]
            if done_only:
                conds.append(model.result.in_(_EXTERNAL_DONE_RESULTS))
            if ts:
                conds.append(model.complaint_date >= ts)
            if te:
                conds.append(model.complaint_date <= te)
            return db.scalar(select(func.count()).select_from(model).where(*conds)) or 0

        # registered 并入: 净新增(总数 − 已 matched 窗口内登记工单) —— 仅 315
        #   ⚠️ #47: 卡商已拆出为**独立桶**(见函数上方 `src_val == "(外)卡商"` 分支),
        #      此处只并入 ExternalComplaint(原先两表都并入, 会与卡商桶双算)。
        ext_reg = 0
        for model in (ExternalComplaint,):
            conds = [model.complaint_date.isnot(None)]
            if ts:
                conds.append(model.complaint_date >= ts)
            if te:
                conds.append(model.complaint_date <= te)
            total = db.scalar(select(func.count()).select_from(model).where(*conds)) or 0
            ext_reg += int(total) - int(_overlap(model, done_only=False, handled_side=False))
        registered += max(ext_reg, 0)

        # handled 并入: 315(external_complaints) 补充表已办结(result∈办结类)。
        #   ⚠️ #47: 卡商**有独立分支**(见函数上方 `src_val == "(外)卡商"`), 用前缀锚定判定,
        #      不并入此处 —— 用 315 的办结枚举套卡商会恒为 0(实测卡商 result 是另一套语义)。
        model = ExternalComplaint
        conds = [model.complaint_date.isnot(None)]
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        conds_done = list(conds) + [model.result.in_(_EXTERNAL_DONE_RESULTS)]
        ext_done = db.scalar(select(func.count()).select_from(model).where(*conds_done)) or 0
        # matched 到"窗口内真实完成工单"的已办结 → 工单侧 handled 已计, 不重复加
        ext_done_overlap_handled = int(_overlap(model, done_only=True, handled_side=True))
        handled += max(int(ext_done) - int(ext_done_overlap_handled), 0)

    return int(registered), int(handled)

def _sources_reg_handled(db, ts, te, include_suppl_external: bool = True) -> dict:
    """投诉来源 registered/handled 汇总: {source: {registered, handled}} + total。

    ★★ 2026-09-11 (#53): 由四源**收回三源** ★★
      用户: "(外)卡商 的投诉 不纳入总量的管理, 整体独立 统计分析;
             无论是界面上 还是各项指标的分析 统计 接口 业务逻辑 等等 全部单算"。
      → 卡商**不再出现在本字典**, 改为独立字段返回(见 `/summary` 与 `/analysis`
        的 `card` 对象)。`total` 也不含卡商。
      → 三源互斥性: 内部 + 商户号 + 外部 = 投诉总量(不含卡商)。

    ★★ #81 (2026-09-13): 本函数升级为**投诉总量的唯一出口** ★★
      `analytics.today_kpi` 的「核心指标·投诉数」原先**另写一套**（工单 + 315表 +
      卡商表，不扣重叠）→ 与四卡数字必然对不上（本月差 37 条，且违反 #53）。
      现改为 today_kpi **直接调用本函数**，从根上消除"两处口径"
      （#65 的教训就是口径漂移：软纠正只落一侧导致 160 条黑洞）。

    `include_suppl_external=False` 时**不并入** 315 补充表净新增
      （供 `analytics._INCLUDE_COMPLAINT_FILES=False` 场景对齐开关语义）。
    """
    out = {}
    reg_sum = handled_sum = 0
    for src in ("内部投诉", "商户号投诉", "外部投诉"):
        reg, hdl = _source_reg_handled(
            db, src, ts, te, include_suppl_external=include_suppl_external
        )
        out[src] = {"registered": reg, "handled": hdl}
        reg_sum += reg
        handled_sum += hdl
    out["total"] = {"registered": reg_sum, "handled": handled_sum}
    return out

def _card_reg_handled(db, ts, te) -> dict:
    """(外)卡商 **独立** registered/handled（#53/#65 —— 不参与投诉总量）。

    口径(#65): **工单侧真卡商 + `card_complaints` 补充表 − 重叠**。
      · 工单侧真卡商 = 「投诉类 ∧ 分桶=(外)卡商(已含 #58 软纠正)」
      · 重叠 = 补充表 `matched_ticket_no` 指向本范围内卡商工单的条数

    ⚠️ #53 原口径是「表自身行数, 不扣工单重叠」, 依据「工单侧无卡商分类(0 行)」。
       #57 证明该**检测方式有误**: 卡商在工单侧以 `item='工信部投诉'` 形式存在,
       不以「卡商」字面存在 → 老结论失效, 近 3 月实测重叠 16 条(不去重则双计)。

    handled: 补充表前缀锚定(`card_result_state`) + 工单侧真实完成。

    返回: {"registered": int, "handled": int}（与 `_sources_reg_handled` 的 value 同形）
    """
    reg, hdl = _source_reg_handled(db, CARD_SCOPE, ts, te)
    return {"registered": int(reg), "handled": int(hdl)}

def _suppl_event_metrics(
    db,
    model,
    date_col,
    channel_col,
    ts: datetime | None,
    te: datetime | None,
    *,
    with_review_rows: bool = False,
) -> dict:
    """补充表(**事件口径**)归并统计 —— #55 用户口径的**唯一实现**(两处复用)。

    ★ 用户口径（2026-09-11 权威）:
      「同一个人 设备编号 电话号码 都一致, 几天内 的投诉, 渠道(平台)是肯定不同,
        内容略微不同; 可以归结为一个投诉, 但**不能算重复数据**;
        如果 电话 sn 渠道 都一致 的话 那就基本上是同一个投诉 **拷贝复制**的」

    ★ 两类必须分开(此前混淆, 已纠正):
      · `multi_channel` —— 同一投诉多渠道呈现 → 归并 1 笔, **不算重复**
      · `true_dup`      —— 同渠道拷贝复制     → 计 1 笔, 标 `dup_of`
      · `legacy_unmerged` —— ≤2025 年**不归并**(用户: 旧数据允许偏差)
      · `unkeyed`       —— 缺 SN/电话 → 不自动归并, 独立成事件

    ⚠️ **绝不删数据**: 只产出统计视图; `record_ids` 可回溯原始行。
       用户: "毕竟不能乱删除 所以还是慎重"。

    参数:
      · `channel_col` —— 渠道列。315 用 `platform`; 卡商用 `complaint_group`
        (卡商表无 platform, 群名即渠道)。**渠道不同 = 非重复**, 故该列必传。
      · `with_review_rows` —— 是否附带 `needs_review_rows`(逐组明细, 供前端
        人工审核 Tab)。默认 False 以免拖大响应体。

    返回: total / events / merged_rows / multi_channel_events / true_dup_events
          / legacy_unmerged / unkeyed / needs_review / strict_min_year
          (+ needs_review_rows)
    """
    conds: list = [date_col.isnot(None)]
    if ts:
        conds.append(date_col >= ts)
    if te:
        conds.append(date_col <= te)

    # ★ #60: 排除采集质量残影(dup_sn_backfill) —— 与完整行是同一投诉。
    #   ⚠️ #61 之后残影已能在 `dedup_events` 里归入对应事件; 此处**先排除**
    #      可让 `total`/`events` 都干净(残影不参与, 也无需再归并)。
    #      两者不冲突: 排除是「不计残影」, 归并是「残影若在则并入」——
    #      保留归并逻辑可兼容"未排除但已标记"的调用场景(如下钻明细)。
    from app.services.complaint_scope import complaint_quality_include_cond

    rows = db.execute(
        select(model.id, model.device_sn, date_col, model.complainant_phone,
               channel_col, model.quality_status, model.problem)
        .where(*conds, *complaint_quality_include_cond(model, db))
    ).all()
    payload = [
        {"id": r[0], "device_sn": r[1], "complaint_date": r[2],
         "complainant_phone": r[3], "channel": r[4],
         "quality_status": r[5], "problem": r[6]}
        for r in rows
    ]
    events, merged_rows, review = dedup_events(payload)

    kinds: dict[str, int] = {}
    ghost_merged_total = 0
    for e in events:
        k = e.get("kind", "single")
        kinds[k] = kinds.get(k, 0) + 1
        ghost_merged_total += int(e.get("ghost_merged") or 0)

    out = {
        "total": len(payload),                       # 原始行数(**已排除残影**)
        "events": len(events),                       # ★ 归并后事件数(统计应用此值)
        "merged_rows": int(merged_rows),              # 被归并的冗余记录数
        "multi_channel_events": kinds.get("multi_channel", 0),   # 多渠道(非重复)
        "true_dup_events": kinds.get("true_dup", 0),             # 真重复(同渠道)
        "legacy_unmerged": kinds.get("legacy_unmerged", 0),      # 旧年份未归并
        "unkeyed": kinds.get("unkeyed", 0),                      # 缺 SN/电话
        "needs_review": len(review),                             # 待人工判定组数
        "strict_min_year": EVENT_STRICT_MIN_YEAR,
        "ghost_merged": ghost_merged_total,          # ★ #61: 归并进来的残影数
    }
    if with_review_rows:
        out["needs_review_rows"] = review
    return out

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
            # 与「待处理」分支一致(2026-08-25): 状态字段常未同步, 以 finish 真实完成判定优先
            if has_real_finish:
                return "已完成"
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
    if dt.tzinfo is None:
        # naive UTC → 视为UTC
        dt = dt.replace(tzinfo=timezone.utc)
    dt_sh = dt.astimezone(TZ)
    return dt_sh.strftime("%Y-%m-%d %H:%M")



# 被投诉主体归一(2026-08-25): 同一家公司不同商户号/别名 → 规范名, **仅在被投诉主体维度聚合时使用**。
# 商户号投诉 tab 仍按商户号细分(不合并), 避免丢失「云客猫 vs 大秦WiFi管家」等商户号差异。
# 例: 深圳市云客猫科技有限公司 与 云客猫 是同一家公司 → 被投诉主体聚合时都归入「云客猫」。
_SUBJECT_ENTITY_ALIASES: dict[str, str] = {
    "深圳市云客猫科技有限公司": "云客猫",
}

def _norm_subject_entity(name: str | None) -> str | None:
    """归一化被投诉主体名: 命中公司别名映射 → 规范名; 含「云客猫」的主体也归入「云客猫」。"""
    if not name:
        return name
    v = str(name).strip()
    if not v:
        return v
    if v in _SUBJECT_ENTITY_ALIASES:
        return _SUBJECT_ENTITY_ALIASES[v]
    if "云客猫" in v:
        return "云客猫"
    return v



# ---------- 积压未处理投诉 审核分析 ----------


# 积压口径统一 (与 /detail 的 _norm_status 完全一致):
# 积压 = 投诉类工单, 归一化状态 != '已完成'。
# SQL 粗过滤排除显式完成类状态, Python 层用 _norm_status 兜底(排除"待处理/空状态但存在真实完成时间")。
_BACKLOG_DONE_SET = {"已结案", "已完成", "完成", "已解决", "已回复", "已反馈", "已关闭", "终止", "已终止"}

# 积压龄期分桶 (按登记时间距今天数)
_BACKLOG_AGING_BUCKETS = [("今天", 0, 0), ("1-3天", 1, 3), ("4-7天", 4, 7), ("8-14天", 8, 14), (">14天", 15, None)]


# 315 外部投诉登记「办结类」结果集合(2026-08-25 积压闭环):
# 外部投诉工单 status 常停留在"待处理"(上游 BMS 状态同步不完整), 但对应 315 登记里已办结
# (result=和解结案/撤销结案/调解结案等 + 处理时间/明细)。此时工单应视为已完成, 不计入积压。
# 「未达成一致方案/暂行办理/需跟进/空」等不算办结(可能仍需跟进), 保持积压。
_EXTERNAL_DONE_RESULTS = {
    "和解结案", "撤销结案", "暂行办结", "调解结案", "调节结案",
    "投诉无效", "重复投诉", "终止调节", "退回消委会",
}

def _external_done_sns(db) -> set[str]:
    """315登记已办结(result∈办结类)的设备SN集合。

    用于三处积压统计(backlog/summary/analysis)统一排除: 外部投诉工单的 SN 命中该集合,
    且该工单本身是外部投诉来源时, 视为已处理(工单状态未同步的兜底), 不进入积压。

    #105 (2026-09-15): 加缓存(全表扫描, 每次 _complaint_enhanced 都调)。
      key=complaint:ext_done_sns, TTL=60s(办结状态可能随同步变更, 不宜过长)。
      返回值为 set, cache_set_json 会转 list, 调用方转回 set。
    """
    from app.core.redis_client import cached_call
    result = cached_call(
        "complaint:ext_done_sns", 60,
        lambda: list(db.scalars(
            select(ExternalComplaint.device_sn)
            .where(ExternalComplaint.result.in_(_EXTERNAL_DONE_RESULTS))
            .where(ExternalComplaint.device_sn.isnot(None))
        ).all()),
    )
    return set(result or [])

def _norm_external_result_status(result: str | None) -> str:
    """315登记结果 → 归一化状态(明细表展示用, 与积压口径一致):
    办结类 → 已完成; 需跟进/暂行办理/未达成一致方案 → 处理中; 空 → 待跟进; 其它保持原文。
    """
    if not result or not str(result).strip():
        return "待跟进"
    v = str(result).strip()
    if v in _EXTERNAL_DONE_RESULTS:
        return "已完成"
    if v in ("需跟进", "暂行办理", "未达成一致方案"):
        return "处理中"
    return v



# 投诉原因归类(2026-08-25): 基于 remark 投诉内容关键词, 对齐 315 官方分类五大类。
# 用于积压/投诉的原因维度统计(替代区分度低的 service_item 来源子类)。
_REASON_RULES = [
    ("退款/售后", ["退款", "退钱", "退费", "不退", "撤销退款", "退卡", "退货", "售后", "催退", "退不回", "要退", "没退"]),
    ("网络/技术", ["卡", "网速", "断网", "离线", "信号", "连不上", "连接", "用不了", "打不开", "不稳定", "假信号", "故障", "设备", "无法使用", "登录不上", "黑屏", "没网", "无网络"]),
    ("套餐/计费", ["套餐", "计费", "扣费", "自动扣", "乱扣", "多扣", "扣款", "流量计算", "扣流量", "价格", "费用", "资费", "涨价", "贵"]),
    ("充值/规则", ["充值", "充钱", "充了", "购买", "订购", "续费", "会员", "规则", "欺诈", "骗"]),
]

def _complaint_reason(text: str | None) -> str:
    """投诉原因: 按关键词从投诉内容文本归类, 未命中→其他, 无文本→未填写。"""
    if not text or not str(text).strip():
        return "未填写"
    t = str(text)
    for label, kws in _REASON_RULES:
        if any(k in t for k in kws):
            return label
    return "其他"

_315_CATEGORY_MAP = [
    ("退款/售后", ["退款", "售后"]),
    ("网络/技术", ["网络", "流量", "技术"]),
    ("套餐/计费", ["套餐", "计费", "涨价", "价格"]),
    ("充值/规则", ["充值", "钱包", "规则"]),
]

def _map_315_category(cat: str | None) -> str | None:
    """315登记细分分类 → 投诉原因五大类映射(用于外部投诉 remark 为空时的原因补充)。"""
    if not cat:
        return None
    for label, kws in _315_CATEGORY_MAP:
        if any(k in cat for k in kws):
            return label
    return None

def _query_backlog_rows(
    db: DBSession,
    ts: datetime | None,
    te: datetime | None,
    source_type: str | None = None,
    status: str | None = None,
    category: str | None = None,
    has_handler: bool | None = None,
    sort: str = "aging_asc",
) -> list[dict]:
    """查询积压工单全量行(统一归一化口径, 与 /backlog 完全一致).

    供 /backlog(分页+统计) 与 /backlog/export(导出Excel) 共用, 保证两处口径 100% 一致。
    返回列表已按 sort 排序, 每行含 audited 字段(已核查标识)。
    """
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

    # 315登记已办结的SN: 外部投诉工单状态未同步(停"待处理")但实际已办结 → 视为已完成, 不进积压
    ext_done_sns = _external_done_sns(db)
    # SN → 315登记细分分类(用于外部投诉 remark 为空时的投诉原因补充)
    sn_315_cat: dict[str, str] = dict(db.execute(
        select(ExternalComplaint.device_sn, ExternalComplaint.complaint_category)
        .where(ExternalComplaint.device_sn.isnot(None))
        .where(ExternalComplaint.complaint_category.isnot(None), ExternalComplaint.complaint_category != "")
    ).all())

    # Python 归一化兜底, 保证与明细口径 100% 一致
    backlog: list[dict] = []
    for t, src in db.execute(stmt).all():
        # 积压闭环(2026-08-25): 外部投诉工单关联的 315 登记已办结(result∈办结类) → 视为已完成
        if src == "外部投诉" and t.device_sn in ext_done_sns:
            continue
        norm = _norm_status(src, t.status, t.finish_time, t.register_time, _fb_handler(t))
        if norm == "已完成":
            continue
        raw_h = _fb_handler(t)
        norm_h = _norm_handler(raw_h)
        if src == "商户号投诉" and raw_h in entity_set:
            norm_h = "未分配"  # 上游把被投诉主体名写入处理人 → 无真实处理人
        # 投诉原因: remark 关键词归类为主; 外部投诉 remark 空时用 315 登记细分分类映射补充
        reason = _complaint_reason(t.remark)
        if reason == "未填写" and src == "外部投诉" and t.device_sn in sn_315_cat:
            reason = _map_315_category(sn_315_cat[t.device_sn]) or reason
        backlog.append({
            "ticket_no": t.ticket_no,
            "device_sn": t.device_sn,
            "source": src,
            "category": t.service_item or "未标注",
            "reason": reason,
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
    for r in backlog:
        r["audited"] = bool(r["device_sn"] in audited_sns or r["ticket_no"] in audited_refs)
    return backlog



# ---------- 决策概览(#67 P1: 单请求出一屏) ----------
#
# 设计说明(Design Rationale) —— 详见 docs/standards/界面设计说明规范.md
#   目的：客服主管在晨会前 5 分钟内判断「投诉是否失控、该先处理谁」，不需要滚屏。
#   逻辑：总量是后果指标、单看会骗人(客户放弃/渠道堵塞都会让量下降) → 故除总量外
#         同时给出「登记/处理」对称水位与「涉诉金额」，把量翻译成代价；
#         结论条(danger→warn→info→ok)按严重度排序，把数字翻译成"该干什么"。
#   口径：三源 = complaint_scope 分桶(内部/商户号/外部，(外)卡商独立不入总量)；
#         时间过滤与 /summary 对齐(register OR finish)，修正 /analysis 只看 register 的偏差。
#   取舍：不做日粒度环比报警(噪声大，用户 2026-09-11 决策 → 结论条带最小基数护栏)；
#         不做比率指标(数据完整度不足，接口仅预留 ratios=null，见 #68)。
#   工单：#67 · 设计：docs/arch/_pending/A-20260911-001-投诉分析整体重构.md
_AUTO_GRAIN_RULES: tuple[tuple[int, str], ...] = ((31, "day"), (120, "week"))

_OVERDUE_DAYS = 7  # 超期阈值: 累计未处理 >7 天(与积压页龄期口径一致)

_REPEAT_THRESHOLD = 3  # ★ #72 A2: 反复投诉阈值 —— 同一设备 ≥3 次投诉**事件**

#   口径重要提示: 计数单位是「事件(SN + 北京日期去重)」而非原始记录数。
#   实测差异 3 倍(237 台 vs 78 台) —— 同一天多渠道登记属同一笔投诉，不算反复。
_MIN_BASE_FOR_PCT = 5  # 结论条最小基数护栏: 对比期基数 <5 条不给百分比结论(防"3→6 报 +100%")

_MAX_CREDIBLE_PCT = 300.0  # 可信上限: 超过此幅度说明对比期数据覆盖不足(非真实业务信号)

_MAX_CONCLUSIONS = 5

_CONCLUSION_ORDER = ("danger", "warn", "info", "ok")

def _auto_grain(days: int) -> str:
    """按跨度自动推导趋势粒度: ≤31天→日; 32~120天→周; >120天→月。

    理由: 日线超过 31 点即难以阅读; 周线超过 120 天仍可辨认趋势方向。
    """
    for limit, g in _AUTO_GRAIN_RULES:
        if days <= limit:
            return g
    return "month"

def _agg_by_bucket(day_map: dict, grain: str) -> list[dict]:
    """把「按日」字典聚合为 日/周/月 桶列表(有序)。

    输入: {day(YYYY-MM-DD, 北京时间): {field: value}}
    输出: [{bucket, label, **fields}]
      · 日 → bucket=原日, label=MM-DD
      · 周 → bucket=ISO 周一(与前端 dayjs isoWeek 一致), label=MM-DD~MM-DD
      · 月 → bucket=YYYY-MM, label=YYYY-MM

    ⚠️ 聚合在 Python 侧做(而非 SQL date_trunc), 是为了与补充表(315)的逐日去重
       结果统一口径 —— 补充表净额只有日粒度, 在 SQL 里无法按周/月预聚合。
    """
    buckets: dict[str, dict] = {}
    for day, fields in day_map.items():
        try:
            d = datetime.strptime(str(day)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        if grain == "month":
            key = label = d.strftime("%Y-%m")
        elif grain == "week":
            monday = d - timedelta(days=d.weekday())  # weekday(): 周一=0 → ISO 周起点
            key = monday.strftime("%Y-%m-%d")
            label = f"{monday.strftime('%m-%d')}~{(monday + timedelta(days=6)).strftime('%m-%d')}"
        else:
            key = day[:10]
            label = d.strftime("%m-%d")
        b = buckets.setdefault(key, {"bucket": key, "label": label})
        for k, v in (fields or {}).items():
            b[k] = b.get(k, 0) + v
    return [buckets[k] for k in sorted(buckets)]

def _pct(cur: float, prev: float) -> float | None:
    """环比百分比(保留 1 位); 基数为 0 时返回 None —— 不可比值, 前端显示 "—"。"""
    if not prev:
        return None
    return round((float(cur) - float(prev)) / float(prev) * 100.0, 1)

def _kpi_item(key: str, label: str, value, prev, unit: str) -> dict:
    """构造一个 KPI 卡数据项(含环比)。

    prev 为 None(未开启对比)时 delta_pct 为 None → 前端不显示环比箭头。
    """
    return {
        "key": key, "label": label, "value": value,
        "prev": prev, "delta_pct": _pct(value, prev) if prev is not None else None,
        "unit": unit,
    }

def _complaint_sn_subquery(w_ts, w_te, src_expr):
    """窗口内「投诉类」设备 SN 子查询(工单三源 ∪ 315 补充表), 供涉诉退款关联。

    ★ #53: **不含 `(外)卡商`** —— 卡商独立体系, 不参与投诉总量的涉诉金额。
    """
    tk_conds = [
        _ticket_category_case() == "投诉",
        src_expr != CARD_SCOPE,
        ServiceTicket.device_sn.isnot(None),
    ]
    if w_ts:
        tk_conds.append(ServiceTicket.register_time >= w_ts)
    if w_te:
        tk_conds.append(ServiceTicket.register_time <= w_te)
    tk_sn = select(ServiceTicket.device_sn.label("sn")).where(*tk_conds)

    ext_conds = [ExternalComplaint.device_sn.isnot(None)]
    if w_ts:
        ext_conds.append(ExternalComplaint.complaint_date >= w_ts)
    if w_te:
        ext_conds.append(ExternalComplaint.complaint_date <= w_te)
    ext_sn = select(ExternalComplaint.device_sn.label("sn")).where(*ext_conds)
    return tk_sn.union(ext_sn)

def _complaint_refund_rows(db, w_ts, w_te, src_expr):
    """涉诉退款按日明细: [(day, 笔数, 金额元)] —— 投诉 SN 关联的线上退款。

    退款时间用 `created_at_raw`(运营登记口径), 与 /subject-analysis 的退款关联一致。
    """
    sn_sub = _complaint_sn_subquery(w_ts, w_te, src_expr)
    conds = [
        Refund.refund_method == "线上",
        Refund.device_sn.isnot(None),
        Refund.device_sn.in_(sn_sub),
    ]
    if w_ts:
        conds.append(Refund.created_at_raw >= w_ts)
    if w_te:
        conds.append(Refund.created_at_raw <= w_te)
    day = _day_trunc(Refund.created_at_raw)
    return db.execute(
        select(day, func.count(), func.coalesce(func.sum(Refund.refund_amount), 0))
        .where(*conds).group_by(day).order_by(day)
    ).all()

def _complaint_refund_amount(db, w_ts, w_te, src_expr) -> tuple[int, float]:
    """涉诉退款合计 (笔数, 金额元) —— 供 KPI 与结论条使用。

    金额单位: 元(Numeric(12,2) 原值), 与 `/subject-analysis` 的 `refund_amount` 一致。
    """
    conds = [
        Refund.refund_method == "线上",
        Refund.device_sn.isnot(None),
        Refund.device_sn.in_(_complaint_sn_subquery(w_ts, w_te, src_expr)),
    ]
    if w_ts:
        conds.append(Refund.created_at_raw >= w_ts)
    if w_te:
        conds.append(Refund.created_at_raw <= w_te)
    cnt, amt = db.execute(
        select(func.count(), func.coalesce(func.sum(Refund.refund_amount), 0)).where(*conds)
    ).one()
    return int(cnt or 0), float(amt or 0)

def _escalation_stats(db, w_ts, w_te, src_expr) -> tuple[int, int]:
    """升级率统计: (有 SN 的投诉设备数, 其中"内部或商户号 → 外部"升级的设备数)。

    ★ 为什么这是最重要的可控指标:
      外部投诉是后果, 而"内部没处理好导致升级"才是原因 —— 升级率下降 =
      内部处理质量提升的直接证据(其余指标都会被外部因素稀释)。
    """
    base = [
        _ticket_category_case() == "投诉",
        src_expr != CARD_SCOPE,  # ★ #53: 卡商独立，不参与升级率
        ServiceTicket.device_sn.isnot(None),
    ]
    if w_ts:
        base.append(ServiceTicket.register_time >= w_ts)
    if w_te:
        base.append(ServiceTicket.register_time <= w_te)

    total = db.scalar(
        select(func.count(func.distinct(ServiceTicket.device_sn))).where(*base)
    ) or 0
    if not total:
        return 0, 0
    ext_sn = select(ServiceTicket.device_sn).where(*base, src_expr == "外部投诉")
    inner_sn = select(ServiceTicket.device_sn).where(
        *base, src_expr.in_(("内部投诉", "商户号投诉"))
    )
    escalated = db.scalar(
        select(func.count()).select_from(ext_sn.intersect(inner_sn).subquery())
    ) or 0
    return int(total), int(escalated)

def _hour_heatmap(db, w_ts, w_te, src_expr) -> dict:
    """A5 · 投诉登记时段热力（小时 × 星期，北京时间）。

    ★ 决策价值：直接服务**排班决策**
      投诉登记量随时段/星期呈明显节律（实测近 30 天呈双峰：09–12 时、19–21 时，
      02–05 时为谷底）。排班若只看总量、不看分布，高峰期必然积压。

    ★ 为什么用「登记时间」而非「投诉日期」
      排班关心的是"什么时候有人来投诉"（接待压力），不是"投诉属于哪一天"。
      → 用 `register_time` 的小时/星期；时区必须走 `Asia/Shanghai`
        （见 docs/standards/数据时间字段使用规范.md）。

    ★ 峰谷判定用分位数而非固定阈值
      不同业务量级差异大（日 10 单 vs 日 1000 单），固定阈值必然失效；
      P75/P25 分位随数据自适应。

    返回:
      cells       [{weekday, hour, count}]  weekday: 1=周一 … 7=周日（ISO）
      by_hour     [{hour, count}]           边际分布(24 项, 含 0)
      by_weekday  [{weekday, count}]        边际分布(7 项, 含 0)
      peak_hours  [int]                     登记量 ≥P75 的小时(排班需覆盖)
      valley_hours[int]                     登记量 ≤P25 的小时(可减配)
      total       int                       参与统计的投诉登记数
    """
    base = [
        _ticket_category_case() == "投诉",
        src_expr != CARD_SCOPE,  # ★ #53: 卡商独立，不参与投诉排班统计
        ServiceTicket.register_time.isnot(None),
    ]
    if w_ts:
        base.append(ServiceTicket.register_time >= w_ts)
    if w_te:
        base.append(ServiceTicket.register_time <= w_te)

    # 北京时间小时/星期（ISO: 1=周一 … 7=周日）
    sh_time = func.timezone("Asia/Shanghai", ServiceTicket.register_time)
    hour_col = func.extract("hour", sh_time).cast(Integer)
    wd_col = func.extract("isodow", sh_time).cast(Integer)

    rows = db.execute(
        select(wd_col, hour_col, func.count()).where(*base)
        .group_by(wd_col, hour_col)
    ).all()

    cells = [{"weekday": int(w), "hour": int(h), "count": int(c or 0)} for w, h, c in rows if w and h is not None]
    total = sum(c["count"] for c in cells)

    # 边际分布（补齐 0，前端热力图需要完整网格）
    hmap: dict[int, int] = {h: 0 for h in range(24)}
    wmap: dict[int, int] = {w: 0 for w in range(1, 8)}
    for c in cells:
        hmap[c["hour"]] += c["count"]
        wmap[c["weekday"]] += c["count"]

    # 峰谷判定：只在「有数据的小时」上算分位，避免凌晨空档把 P25 拉成 0
    nonzero = sorted(v for v in hmap.values() if v > 0)

    def _quantile(vals: list[int], q: float) -> float:
        """线性插值分位数（与 numpy.percentile 默认口径一致）。"""
        if not vals:
            return 0.0
        if len(vals) == 1:
            return float(vals[0])
        pos = (len(vals) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)

    p75 = _quantile(nonzero, 0.75)
    p25 = _quantile(nonzero, 0.25)
    peak_hours = sorted(h for h, v in hmap.items() if v > 0 and v >= p75)
    valley_hours = sorted(h for h, v in hmap.items() if v > 0 and v <= p25)

    return {
        # ★ #73: 显式声明统计范围 —— 本块**仅统计工单侧**
        #   为何不纳入 315 补充表: 该表 complaint_date 全为 00:00（无小时信息），
        #   created_at 是**采集入库时刻**（06 时 2456 条 = 批量采集）——
        #   若拿来当「客户几点投诉」会造出假的凌晨高峰，**排班结论方向性错误**。
        #   故留空由调用方/前端明确标注，而不是假装口径一致。
        "scope": "ticket_only",
        "scope_note": "仅工单登记侧（不含 315 补充表：该表无投诉时刻，只有日期）",
        "cells": cells,
        "by_hour": [{"hour": h, "count": hmap[h]} for h in range(24)],
        "by_weekday": [{"weekday": w, "count": wmap[w]} for w in range(1, 8)],
        "peak_hours": peak_hours,
        "valley_hours": valley_hours,
        "total": total,
    }

def _repeat_complaints(db, w_ts, w_te, src_expr, threshold: int = 3, top_n: int = 10) -> dict:
    """A2 · 反复投诉（同一设备多次投诉**事件**）。

    ★★★ 口径关键（实测差 3 倍，务必理解）：
      按 SN **直接计数** ≥3 次 → 237 台；
      按**投诉事件（SN + 北京日期）去重**后 ≥3 次 → **78 台**。

      差异来自 #55 口径：**同一天多渠道呈现 = 同一笔投诉的 1 个事件，不算反复**。
      故本函数以「(SN, 北京日期)」为**事件键**去重计数 ——
      否则会把「一天内多渠道登记」误判为该设备在反复投诉，**方向性错误**。

    ★ 决策价值：
      反复投诉 = 「第一次没解决好」的直接证据，比投诉总量更接近真相
      （总量会被一次性事件稀释，反复投诉不会）。

    ★ 升级信号：
      若某设备的 `sources` 同时含「内部/商户号」与「外部」→ 既反复、又已升级，
      属**最高优先级**跟进对象（前端以红色标识）。

    返回:
      threshold/events_total/units_total/repeat_units/repeat_events/rate/items
    """
    base = [
        _ticket_category_case() == "投诉",
        src_expr != CARD_SCOPE,  # ★ #53: 卡商独立
        ServiceTicket.device_sn.isnot(None),
        ServiceTicket.device_sn != "",
        ServiceTicket.register_time.isnot(None),
    ]
    if w_ts:
        base.append(ServiceTicket.register_time >= w_ts)
    if w_te:
        base.append(ServiceTicket.register_time <= w_te)

    # 事件键 = (SN, 北京日期)；同时带出来源桶用于「升级信号」判定
    sh_day = func.date(func.timezone("Asia/Shanghai", ServiceTicket.register_time))
    rows = db.execute(
        select(ServiceTicket.device_sn, sh_day, src_expr).where(*base)
    ).all()

    # 按 (SN, 日) 折叠为「事件」；同一事件内合并来源集合
    events: dict[tuple[str, object], set[str]] = {}
    for sn, day, src in rows:
        key = (sn, day)
        events.setdefault(key, set()).add(src or "内部投诉")

    per_sn: dict[str, dict] = {}
    for (sn, day), srcs in events.items():
        b = per_sn.setdefault(sn, {"events": 0, "sources": set(), "days": []})
        b["events"] += 1
        b["sources"].update(srcs)
        if day:
            b["days"].append(day)

    units_total = len(per_sn)
    events_total = len(events)
    repeats = []
    for sn, b in per_sn.items():
        if b["events"] >= threshold:
            days = sorted(d for d in b["days"] if d)
            srcs = sorted(b["sources"])
            repeats.append({
                "device_sn": sn,
                "events": b["events"],
                "first_day": str(days[0]) if days else None,
                "last_day": str(days[-1]) if days else None,
                "sources": srcs,
                # 升级信号：既有内部/商户号、又有外部
                "escalated": (
                    any(s in ("内部投诉", "商户号投诉") for s in srcs)
                    and "外部投诉" in srcs
                ),
            })

    # 排序：先按事件数降序，再让升级设备优先（同事件数时更该跟进）
    repeats.sort(key=lambda x: (-x["events"], not x["escalated"], x["device_sn"]))
    repeat_units = len(repeats)
    repeat_events = sum(r["events"] for r in repeats)

    return {
        # ★ #73: 显式声明统计范围 —— 与 A5 同因，仅工单侧
        "scope": "ticket_only",
        "scope_note": "仅工单登记侧（不含 315 补充表）",
        "threshold": threshold,
        "events_total": events_total,
        "units_total": units_total,
        "repeat_units": repeat_units,
        "repeat_events": repeat_events,
        "rate": round(repeat_units / units_total * 100.0, 1) if units_total else None,
        "items": repeats[:top_n],
    }
