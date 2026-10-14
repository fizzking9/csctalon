"""today 主题域子模块 trend (拆分自 today.py #226)

纯物理切片, 不改实现。原 today.py 头部 import 块原样保留,
函数体内惰性 import 随函数体原样保留。
"""
# ruff: noqa: F401, I001  # 拆分自原 today.py(#226), 保留原 import 顺序避免漂移
"""今日看板: today_kpi / 小时趋势 / 接待拆分 / 退款拆分 / 队列 / 员工效率。"""

import datetime as _dt
import logging
from decimal import Decimal
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select, text, union
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.external_complaint import ExternalComplaint
from app.models.group import Group, PersonGroupMembership
from app.models.incident import Incident
from app.models.person import Person, PersonAlias
from app.models.refund import RefundClean
from app.models.refund_record_report import RefundRecordReport
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.task import SpecialTask
from app.models.ticket import ServiceTicket
from app.models.todo import TodoItem
from app.models.wallet import WalletWithdrawal
from app.services.complaint_scope import (
    ALL_BUCKETS,
    BUCKET_CARD,
    BUCKET_EXTERNAL,
    BUCKET_INTERNAL,
    # ⚠️ #53: BUCKET_OTHER 已从来源桶移除(四类完备)。保留导入仅供历史数据识别,
    #    若确认无处使用可删除。
    BUCKET_OTHER,  # noqa: F401
    CARD_SCOPE,
)
from app.services.system_accounts import SourceKind  # noqa: I001
from app.services.system_accounts import is_virtual_name as _sa_is_virtual_name

logger = logging.getLogger(__name__)

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    _EXTERNAL_DONE_RESULTS_LOCAL,
    _EXTERNAL_ITEM_COMPLAINT_KW,
    _INCLUDE_COMPLAINT_FILES,
    COMPLAINT_SPECIALTY,
    COMPLAINT_SPECIALTY_WEIGHT,
    D,
    TZ,
    _complaint_source_type_case,
    _complaint_subitem_case,
    _derive_service_item_for_refund,
    _parse_date,
    _pct_delta,
    _resolve_windows,
    _route_a_online_any_status_filter,
    _route_a_online_refund_filter,
    _route_b_withdraw_filter,
    _route_e_cashflow_filter,
    _strip_qw_prefix,
    _ticket_category_case,
)
from app.services.analytics.cash import (
    _cash_routes_total,
    _route_c_shanmao_trend,
    _route_d_other_trend,
)
from app.services.analytics.complaint import _ticket_effective_finish_time_where
from app.services.analytics.reception import (
    _category_case,
    _merge_person_rows,
    _operator_to_person_cte,
)


def today_hourly_trend(db: Session, date: str | None = None, start: str | None = None, end: str | None = None, trend_grain: str | None = None) -> dict[str, Any]:
    """模块2: 时间节奏 (小时/日/周/月 支持手动指定聚合粒度) v5

    粒度规则:
      - trend_grain 手动指定优先级最高: hour/day/week/month
      - 单日自动 → 按小时聚合 (00:00~23:00, 对应今日实时到当前小时)
      - 多日自动 → 按天聚合 (date_trunc day, 标签为 MM-DD)
      - 周聚合: 周一为起始, 标签如 "W32(08-04~08-10)"
      - 月聚合: 标签如 "08月"
    按三组分类展示:
      【工作量组】左Y柱:
        - 会话数(次) / 接待数(个): 独立柱
      【款项组】左Y柱(笔数堆叠) + 右Y面积(金额堆叠):
        - 退款笔数: 四路堆叠柱 → A(线上直退) + B(钱包提现) + C(闪猫退款) + D(手动退款) = 经营合计
        - 退款金额: 四路堆叠面积 → A+B+C+D = 经营合计; 独立虚线 = E(商户号实际现金流)
      【投诉组】左Y柱:
        - 投诉量: 独立柱
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)

    # 先计算跨度天数(北京时间日历日)
    ts_sh = _dt.datetime.fromtimestamp(ts.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ).date()
    te_sh = _dt.datetime.fromtimestamp(te.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ).date()
    span_days = (te_sh - ts_sh).days + 1

    # 确定聚合粒度
    valid_grains = {"hour", "day", "week", "month"}
    if trend_grain in valid_grains:
        grain = trend_grain
    elif span_days <= 1:
        grain = "hour"
    else:
        grain = "day"
    # ── 时间截断函数 ──
    def _time_trunc(col):
        if grain == "hour":
            return func.date_trunc("hour", func.timezone("Asia/Shanghai", col)).label("trunc_t")
        elif grain == "week":
            # date_trunc('week', ...) 返回周一为起始的日期
            return func.date_trunc("week", func.timezone("Asia/Shanghai", col)).label("trunc_t")
        elif grain == "month":
            return func.date_trunc("month", func.timezone("Asia/Shanghai", col)).label("trunc_t")
        return func.date_trunc("day", func.timezone("Asia/Shanghai", col)).label("trunc_t")
    # 2. 小时/日/周/月 标签与横轴: 填充连续槽位
    now_sh = _dt.datetime.now(TZ)
    # 预定义辅助函数: datetime转date
    def _to_date(d):
        if isinstance(d, _dt.datetime):
            return d.date()
        return d
    if grain == "hour":
        # 小时轴: 0~23 小时
        list(range(24))
        is_today_like = is_realtime
        max_slot = now_sh.hour if is_today_like else 23
        def _slot_idx(trunc_t_local: _dt.datetime) -> int | None:
            return int(trunc_t_local.hour)
        def _slot_label(slot: int) -> str:
            return f"{slot:02d}:00"
        x_label = "小时节奏 (北京时间)"
        n_slots = 24
    elif grain == "week":
        # 周轴: 从start所在周到end所在周 (周一为起始)
        # 用datetime对象计算周起始
        ts_sh_dt = _dt.datetime.fromtimestamp(ts.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ)
        te_sh_dt = _dt.datetime.fromtimestamp(te.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ)
        def _start_of_week(d):
            d = _to_date(d)
            return d - timedelta(days=d.weekday())
        cur_week_start = _start_of_week(ts_sh_dt)
        end_week_start = _start_of_week(te_sh_dt)
        slots_weeks = []
        while cur_week_start <= end_week_start:
            slots_weeks.append(cur_week_start)
            cur_week_start += timedelta(days=7)
        # 判断最后一周是否是当前周(实时)
        current_week_start = _start_of_week(now_sh)
        def _slot_idx(trunc_t_local) -> int | None:
            # date_trunc 返回 datetime，先转 date
            ws = _start_of_week(trunc_t_local)
            if ws in slots_weeks:
                return slots_weeks.index(ws)
            return None
        def _slot_label(slot: int) -> str:
            ws = slots_weeks[slot]
            we = ws + timedelta(days=6)
            return f"W{ws.isocalendar()[1]}({ws.strftime('%m-%d')}~{we.strftime('%m-%d')})"
        max_slot = slots_weeks.index(current_week_start) if current_week_start in slots_weeks and is_realtime else len(slots_weeks) - 1
        x_label = "周节奏 (周一~周日)"
        n_slots = len(slots_weeks)
    elif grain == "month":
        # 月轴: 从start所在月到end所在月
        ts_sh_dt = _dt.datetime.fromtimestamp(ts.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ)
        te_sh_dt = _dt.datetime.fromtimestamp(te.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ)
        def _start_of_month(d):
            d = _to_date(d)
            return d.replace(day=1)
        cur_month_start = _start_of_month(ts_sh_dt)
        end_month_start = _start_of_month(te_sh_dt)
        slots_months = []
        while cur_month_start <= end_month_start:
            slots_months.append(cur_month_start)
            # 下月1号
            if cur_month_start.month == 12:
                cur_month_start = cur_month_start.replace(year=cur_month_start.year + 1, month=1)
            else:
                cur_month_start = cur_month_start.replace(month=cur_month_start.month + 1)
        # 判断最后一月是否是当前月(实时)
        current_month_start = _start_of_month(now_sh)
        def _slot_idx(trunc_t_local) -> int | None:
            ms = _start_of_month(trunc_t_local)
            if ms in slots_months:
                return slots_months.index(ms)
            return None
        def _slot_label(slot: int) -> str:
            ms = slots_months[slot]
            return f"{ms.strftime('%Y-%m')}"
        max_slot = slots_months.index(current_month_start) if current_month_start in slots_months and is_realtime else len(slots_months) - 1
        x_label = "月节奏"
        n_slots = len(slots_months)
    else:
        # 日轴: 从 start 到 end 连续日期 (按 Asia/Shanghai 日历日)
        # ts_sh/te_sh已经在开头计算为date类型
        slots_days: list[_dt.date] = []
        cur = ts_sh
        while cur <= te_sh:
            slots_days.append(cur)
            cur += timedelta(days=1)
        max_slot = len(slots_days) - 1
        now_sh.date()
        def _slot_idx(trunc_t_local: _dt.datetime) -> int | None:
            d_local = _to_date(trunc_t_local)
            if d_local in slots_days:
                return slots_days.index(d_local)
            return None
        def _slot_label(slot: int) -> str:
            return slots_days[slot].strftime("%m-%d")
        x_label = "日期 (跨度 {} 天)".format(len(slots_days))
        n_slots = len(slots_days)
    cs_rows = db.execute(
        select(_time_trunc(CsRecord.operation_time),
               func.count().label("cnt"),
               func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("dev_cnt"))
        .where(CsRecord.operation_time.isnot(None),
               CsRecord.operation_time >= ts, CsRecord.operation_time <= te)
        .group_by("trunc_t")
    ).all()
    cs_map: dict[int, int] = {}
    cs_dev_map: dict[int, int] = {}
    for r in cs_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        cs_map[idx] = int(r.cnt or 0)
        cs_dev_map[idx] = int(r.dev_cnt or 0)

    # --- 路线A: 退费单线上直退(按粒度 笔数+金额) ---
    rf_rows = db.execute(
        select(_time_trunc(RefundClean.created_at_raw),
               func.count().label("cnt"),
               func.coalesce(func.sum(RefundClean.refund_amount), 0).label("amt"))
        .where(RefundClean.created_at_raw.isnot(None),
               RefundClean.created_at_raw >= ts, RefundClean.created_at_raw <= te,
               *_route_a_online_refund_filter())
        .group_by("trunc_t")
    ).all()
    a_cnt_map: dict[int, int] = {}
    a_amt_map: dict[int, Decimal] = {}
    for r in rf_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        a_cnt_map[idx] = int(r.cnt or 0)
        a_amt_map[idx] = D(r.amt)

    # --- 路线B: 钱包提现(按粒度 笔数+金额) ---
    wb_rows = db.execute(
        select(_time_trunc(WalletWithdrawal.op_time),
               func.count().label("cnt"),
               func.coalesce(func.sum(WalletWithdrawal.op_amount), 0).label("amt"))
        .where(WalletWithdrawal.op_time.isnot(None),
               WalletWithdrawal.op_time >= ts, WalletWithdrawal.op_time <= te,
               *_route_b_withdraw_filter())
        .group_by("trunc_t")
    ).all()
    b_cnt_map: dict[int, int] = {}
    b_amt_map: dict[int, Decimal] = {}
    for r in wb_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        b_cnt_map[idx] = int(r.cnt or 0)
        b_amt_map[idx] = D(r.amt)

    # --- 路线C: 闪猫退款(按粒度 笔数+金额) ---
    c_cnt_map, c_amt_map = _route_c_shanmao_trend(db, ts, te, grain, _slot_idx)

    # --- 路线D: 手动退款(按粒度 笔数+金额) ---
    d_cnt_map, d_amt_map = _route_d_other_trend(db, ts, te, grain, _slot_idx)

    # --- 路线E: 商户号实际现金流(按粒度 笔数+金额, 数据源=refund_record_reports) ---
    e_rows = db.execute(
        select(_time_trunc(RefundRecordReport.refund_time),
               func.count().label("cnt"),
               func.coalesce(func.sum(RefundRecordReport.refund_amount), 0).label("amt"))
        .where(RefundRecordReport.refund_time.isnot(None),
               RefundRecordReport.refund_time >= ts, RefundRecordReport.refund_time <= te,
               *_route_e_cashflow_filter())
        .group_by("trunc_t")
    ).all()
    e_cnt_map: dict[int, int] = {}
    e_amt_map: dict[int, Decimal] = {}
    for r in e_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        e_cnt_map[idx] = int(r.cnt or 0)
        e_amt_map[idx] = D(r.amt)

    # 投诉: ★★ #85 口径修正 —— 由「工单 + 315全表 + 卡商全表」改为
    #   **与 #81 核心指标同源**（分桶 + 扣 315 已 matched 重叠 + 剥卡商）
    #
    #   【修什么】原实现（2026-09-14 前）：
    #     `工单投诉类 + ExternalComplaint 全表 + CardComplaint 全表`
    #     → ① **含卡商**（与 #53「卡商独立、不纳入总量」直接冲突）
    #       ② **不扣重叠** → 315 里已 matched 到工单的记录被重复计入
    #       ③ 与同页「核心指标·投诉数」**对不上**（实测 1127 vs 1111，差 16）
    #     #81 统一了核心指标，但**漏改了趋势层** → 本处补上。
    #
    #   【新实现】按三源分桶逐桶统计（与 `_sources_reg_handled` 同源）：
    #     · 内部/商户号 = 工单按 `register_time` 落桶（无补充表）
    #     · 外部 = 工单按登记日 + 315 补充表**净新增**（总数 − 已 matched 重叠）
    #     · **卡商不并入**（#53）→ 保证「柱加总 == 核心指标投诉数」
    #
    #   【为什么按分桶而非直接复用 _sources_reg_handled】后者返回**窗口汇总**，
    #     而趋势需要**按桶（小时/日/周/月）拆分**。故此处按同一判据 `src_expr`
    #     分组，而非调用汇总函数 —— **判据同源，粒度不同**。
    src_expr_cp = _complaint_source_type_case()
    cp_map: dict[int, int] = {}
    cp_rows = db.execute(
        select(_time_trunc(ServiceTicket.register_time), src_expr_cp.label("src"),
               func.count().label("cnt"))
        .where(ServiceTicket.register_time.isnot(None),
               ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te,
               _ticket_category_case() == "投诉",
               # ★ #53: 卡商独立，不并入投诉柱
               src_expr_cp != CARD_SCOPE)
        .group_by("trunc_t", "src")
    ).all()
    for r in cp_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        cp_map[idx] = cp_map.get(idx, 0) + int(r.cnt or 0)
    # ★ 处理序列（finish 落桶 + 真实完成）—— 用户 2026-09-14 选定「柱(登记)+叠处理线」
    #   口径与 `complaints._source_reg_handled` 的 handled **同源**：
    #     finish_time 落桶 且 finish − register ≥ 3min（排除同瞬填充的伪完成）
    #     finish_time 落桶 且 finish − register ≥ 3min（排除同瞬填充的伪完成）
    #
    #   ⚠️⚠️ 关键：**不限制 register_time 也在窗口内**（2026-09-14 实测踩到）
    #     若加上该限制，会**丢掉 83 条「窗口外登记、本窗口办结」的历史单**
    #     （实测 1044 → 961）—— 而"今天实际清掉多少历史积压"**正是主管要看的数**
    #     （#82 账龄分档显示有 284 条 >90 天陈年单，办结它们才是有价值的信号）。
    #     → 故**按 finish 落桶**，语义 = 「该时段内完成了多少（含往期单）」。
    #   ★ 与「登记柱」配合读法：柱高 > 线高 ⇒ 在累积；柱高 < 线高 ⇒ 在消化。
    cp_done_map: dict[int, int] = {}
    cp_done_rows = db.execute(
        select(_time_trunc(ServiceTicket.finish_time), func.count().label("cnt"))
        .where(ServiceTicket.finish_time.isnot(None),
               ServiceTicket.register_time.isnot(None),
               ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te,
               _ticket_category_case() == "投诉",
               src_expr_cp != CARD_SCOPE,
               func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= 180)
        .group_by("trunc_t")
    ).all()
    for r in cp_done_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        cp_done_map[idx] = int(r.cnt or 0)
    # 315 补充表「已办结」并入处理线（与 `_source_reg_handled` 的 external 分支同源）
    #   去重: 已办结但 matched 工单已在工单侧计入 handled 的排除（避免同一笔双算）
    if _INCLUDE_COMPLAINT_FILES:
        _done_sub = select(ServiceTicket.ticket_no).where(
            _ticket_category_case() == "投诉",
            ServiceTicket.finish_time.isnot(None),
            ServiceTicket.register_time.isnot(None),
            func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) >= 180,
            ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te)
        ec_done_rows = db.execute(
            select(_time_trunc(ExternalComplaint.complaint_date), func.count().label("cnt"),
                   func.count(ExternalComplaint.matched_ticket_no)
                   .filter(ExternalComplaint.matched_ticket_no.in_(_done_sub)).label("ov"))
            .where(ExternalComplaint.complaint_date.isnot(None),
                   ExternalComplaint.complaint_date >= ts, ExternalComplaint.complaint_date <= te,
                   ExternalComplaint.result.in_(_EXTERNAL_DONE_RESULTS_LOCAL))
            .group_by("trunc_t")
        ).all()
        for r in ec_done_rows:
            tt = r._mapping["trunc_t"]
            if tt is None:
                continue
            idx = _slot_idx(tt)
            if idx is None:
                continue
            net = int(r.cnt or 0) - int(r.ov or 0)
            if net:
                cp_done_map[idx] = cp_done_map.get(idx, 0) + net
    # 315 补充表**净新增**并入「外部投诉」（与 /summary 合并口径一致）
    #   净新增 = 当日总数 − 已 matched 到本范围内工单的条数（避免与工单双算）
    if _INCLUDE_COMPLAINT_FILES:
        _ec_cond = [ExternalComplaint.complaint_date.isnot(None),
                    ExternalComplaint.complaint_date >= ts, ExternalComplaint.complaint_date <= te]
        _sub_tk = select(ServiceTicket.ticket_no).where(
            _ticket_category_case() == "投诉",
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te)
        ec_rows = db.execute(
            select(_time_trunc(ExternalComplaint.complaint_date), func.count().label("cnt"),
                   func.count(ExternalComplaint.matched_ticket_no)
                   .filter(ExternalComplaint.matched_ticket_no.in_(_sub_tk)).label("ov"))
            .where(*_ec_cond)
            .group_by("trunc_t")
        ).all()
        for r in ec_rows:
            tt = r._mapping["trunc_t"]
            if tt is None:
                continue
            idx = _slot_idx(tt)
            if idx is None:
                continue
            net = int(r.cnt or 0) - int(r.ov or 0)
            if net:
                cp_map[idx] = cp_map.get(idx, 0) + net
        # ⚠️ CardComplaint **不再并入**（#53 卡商独立，见上方说明）

    # ── 填充到槽位: 单日填当前小时之前, 多日填全部(最后一天可能实时) ──
    slots = list(range(n_slots))
    def _fill_list(*maps, kind='count'):
        """将多个map按slot合并成list, 返回各路线list + 合计list。
        #222: 金额路径统一用 Decimal 累加（C/D 路线已返回 Decimal，A/B/E 也改为 D()），
              出参边界 float(round(...)) 转 JSON number。"""
        n_maps = len(maps)
        lists = [[] for _ in range(n_maps)]
        total_list = []
        for i in slots:
            if i > max_slot:
                for L in lists: L.append(None)
                total_list.append(None)
            else:
                total = 0 if kind == 'count' else D(0)
                for j, m in enumerate(maps):
                    v = m.get(i, 0 if kind == 'count' else D(0))
                    lists[j].append(v if kind == 'count' else float(round(v, 2)))
                    total += v
                total_list.append(total if kind == 'count' else float(round(total, 2)))
        return lists + [total_list]

    # 退款笔数: A/B/C/D 四路 + 合计
    cnt_lists = _fill_list(a_cnt_map, b_cnt_map, c_cnt_map, d_cnt_map, kind='count')
    r_cnt_a, r_cnt_b, r_cnt_c, r_cnt_d, r_cnt_total = cnt_lists
    # 退款金额: A/B/C/D 四路 + 合计(经营估算)
    amt_lists = _fill_list(a_amt_map, b_amt_map, c_amt_map, d_amt_map, kind='amount')
    r_amt_a, r_amt_b, r_amt_c, r_amt_d, r_amt_total = amt_lists
    # 实际现金流E (笔数+金额)
    e_cnt_list = [e_cnt_map.get(i, 0) if i <= max_slot else None for i in slots]
    e_amt_list = [float(round(e_amt_map.get(i, D(0)), 2)) if i <= max_slot else None for i in slots]

    # 旧兼容字段 = 四路合计
    refund_count = r_cnt_total
    refund_amount = r_amt_total

    # 展示用日期 (单日 → date; 范围 → start date, 另附 date_end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso: str | None = disp_range[1][:10]
    else:
        d_iso = (now_sh.date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None

    slot_labels = [_slot_label(i) for i in slots]
    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "grain": grain,
        "x_label": x_label,
        "hours": slots if grain == "hour" else None,   # 旧兼容
        "hour_labels": slot_labels,                     # 前端统一用 hour_labels
        "slot_labels": slot_labels,
        # === 工作量组 ===
        "reception":          [cs_map.get(i, 0)         if i <= max_slot else None for i in slots],
        "reception_devices":  [cs_dev_map.get(i, 0)     if i <= max_slot else None for i in slots],
        # === 款项组 - 退款笔数: 四路拆分 + 合计 ===
        "refund_count":       refund_count,      # 旧兼容 = 四路合计
        "refund_count_a":     r_cnt_a,           # 路线A 退费单线上直退
        "refund_count_b":     r_cnt_b,           # 路线B 钱包用户取现
        "refund_count_c":     r_cnt_c,           # 路线C 闪猫退款
        "refund_count_d":     r_cnt_d,           # 路线D 手动退款
        "refund_count_direct":  r_cnt_a,         # 向后兼容: direct = A
        "refund_count_withdraw": r_cnt_b,        # 向后兼容: withdraw = B
        "refund_count_total":  r_cnt_total,      # 四路合计 A+B+C+D
        # === 款项组 - 退款金额: 四路拆分 + 合计 + 实际现金流E ===
        "refund_amount":      refund_amount,     # 旧兼容 = 四路合计
        "refund_amount_a":    r_amt_a,           # 路线A 金额
        "refund_amount_b":    r_amt_b,           # 路线B 金额
        "refund_amount_c":    r_amt_c,           # 路线C 金额
        "refund_amount_d":    r_amt_d,           # 路线D 金额
        "refund_amount_e":    e_amt_list,        # 路线E 实际现金流金额
        "refund_amount_direct":  r_amt_a,        # 向后兼容
        "refund_amount_withdraw": r_amt_b,       # 向后兼容
        "refund_amount_total":  r_amt_total,     # 四路经营合计 A+B+C+D
        "cashflow_e_count":   e_cnt_list,        # 实际现金流E 笔数
        "cashflow_e_amount":  e_amt_list,        # 实际现金流E 金额
        # === 投诉组 ===
        "complaint":          [cp_map.get(i, 0)         if i <= max_slot else None for i in slots],
        # ★ #85: 投诉**处理**序列（柱=登记 + 线=处理，用户 2026-09-14 选定）
        #   口径: finish_time 落桶 且 真实完成(finish−register≥3min)，**不含卡商**，
        #   与 `complaints._source_reg_handled` 的 handled 同源。
        #   ⚠️ 「处理」含**历史单今日办结**（登记柱在别的桶、处理线在本桶）——
        #      这正是主管要知道的"今天实际清掉多少"，故按 finish 落桶而非登记日。
        "complaint_done":     [cp_done_map.get(i, 0)    if i <= max_slot else None for i in slots],
        "_spec": {
            "refund_count_a":  "路线A: 退费单 refund_method='线上' (线上直退)",
            "refund_count_b":  "路线B: 钱包提现 op_type='用户提现'",
            "refund_count_c":  "路线C: 闪猫退款 (shanmao_refunds status=已退款)",
            "refund_count_d":  "路线D: 手动退款 (manual_refunds)",
            "refund_count_total": "四路经营合计 A+B+C+D",
            "refund_amount_a": "路线A: 退费单线上直退金额",
            "refund_amount_b": "路线B: 钱包用户取现金额",
            "refund_amount_c": "路线C: 闪猫退款金额",
            "refund_amount_d": "路线D: 手动退款金额",
            "refund_amount_e": "路线E: 商户号实际现金流 (refund_record_reports 退款成功)",
            "refund_amount_total": "四路经营合计金额 A+B+C+D",
            "reception":     "会话数(次) = cs_record 计数；接待数(个) = 去重设备SN计数(去NULL/空串)",
            "groups":        "工作量: 会话/接待 · 款项: 退款笔数(四路堆叠)/退款金额(四路堆叠面积+实际现金流E线) · 投诉: 登记(柱) + 处理(线)",
            "grain":         f"时间粒度: {grain} (跨度{span_days}天 → 自动选择粒度)",
        },
    }

