"""投诉统计路由(规划设计 §4.6 三源统一统计)

三源: 内部投诉(service_tickets商品/服务投诉) / 商户号投诉(service_tickets微信投诉) / 外部投诉(service_tickets平台类 + external_complaints + card_complaints卡商群)。
复用 analytics 服务的口径 helper, 保证与既有看板一致。
"""

import re
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import and_, case, func, or_, select

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.core.redis_client import cache_ttl_for, cached_json
from app.models.card_complaint import CardComplaint
from app.models.complaint_audit import ComplaintAudit
from app.models.external_complaint import ExternalComplaint
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.schemas.common import PageBase, R
from app.services.analytics import TZ, _complaint_source_type_case, _ticket_category_case

# #47 (2026-09-10): 卡商 result 前缀锚定判定 + 状态常量(与 complaint_scope 单一来源)
# #47 (2026-09-10) 新增 / #53 (2026-09-11) 强化: (外)卡商 **完全独立** ——
#   卡商不在投诉总量内, 统计体系与投诉平行(用户: "全部单算")
# #55 (2026-09-11): 新增 `dedup_events` —— 多渠道归并 vs 真重复 判据
from app.services.complaint_scope import (
    CARD_SCOPE,
    EVENT_STRICT_MIN_YEAR,
    STATUS_DONE,
    card_result_state,
    dedup_events,
)

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
_REAL_FINISH_EPOCH_SECS = 180


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
        # ★★ (#53) 卡商 registered = **表自身行数**, 不扣工单重叠 ★★
        #   实证: 工单表 service_item/service_category 含「卡商」均 0 行 →
        #   卡商在工单侧无独立分类, 无"已计工单"可扣。原扣减口径(#52)会把卡商算少。
        _reg_cc = db.scalar(
            select(func.count()).select_from(CardComplaint).where(*_cc_conds)
        ) or 0
        # handled: 前缀锚定判定(与 complaint_scope 单一来源, 见上方说明)
        _done_rows = db.execute(
            select(CardComplaint.result).where(*_cc_conds)
        ).scalars().all()
        _hdl_cc = sum(1 for v in _done_rows if card_result_state(v) == STATUS_DONE)
        return int(_reg_cc), int(_hdl_cc)

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


def _sources_reg_handled(db, ts, te) -> dict:
    """投诉来源 registered/handled 汇总: {source: {registered, handled}} + total。

    ★★ 2026-09-11 (#53): 由四源**收回三源** ★★
      用户: "(外)卡商 的投诉 不纳入总量的管理, 整体独立 统计分析;
             无论是界面上 还是各项指标的分析 统计 接口 业务逻辑 等等 全部单算"。
      → 卡商**不再出现在本字典**, 改为独立字段返回(见 `/summary` 与 `/analysis`
        的 `card` 对象)。`total` 也不含卡商。
      → 三源互斥性: 内部 + 商户号 + 外部 = 投诉总量(不含卡商)。
    """
    out = {}
    reg_sum = handled_sum = 0
    for src in ("内部投诉", "商户号投诉", "外部投诉"):
        reg, hdl = _source_reg_handled(db, src, ts, te)
        out[src] = {"registered": reg, "handled": hdl}
        reg_sum += reg
        handled_sum += hdl
    out["total"] = {"registered": reg_sum, "handled": handled_sum}
    return out


def _card_reg_handled(db, ts, te) -> dict:
    """(外)卡商 **独立** registered/handled（#53 —— 不参与投诉总量）。

    口径: `card_complaints` 表自身行数(按 `complaint_date` 窗口), **不扣工单重叠**。
      实证: 工单表 `service_item`/`service_category` 含「卡商」均为 0 行 →
      卡商在工单侧无独立分类, 无"已计工单"可扣。
    handled: 前缀锚定判定(`complaint_scope.card_result_state`)。

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
        ch_col = (CardComplaint.complaint_group
                  if model is CardComplaint else ExternalComplaint.platform)
        ev = _suppl_event_metrics(
            db, model, date_col, ch_col, ts, te, with_review_rows=True
        )
        return {"total": int(total), "matched": int(matched), **ev}

    supplementary = {
        "external": _suppl_stats(ExternalComplaint, ExternalComplaint.complaint_date, ExternalComplaint.matched_ticket_no),
        "card": _suppl_stats(CardComplaint, CardComplaint.complaint_date, CardComplaint.matched_ticket_no),
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

    # ⚠️ 2026-09-10 (#47): 卡商**从外部桶拆出** → external_total 不再并入 card 补充表。
    #   原式(2026-09-01 口径): external = 工单 + 315净 + 卡商净  ← 卡商被并进来
    #   现式: external = 工单 + 315净
    external_total = int(external_ticket_total) + supplementary["external"]["total"] - _overlap_suppl(ExternalComplaint, "外部投诉")
    # ★★ 2026-09-11 (#53) 卡商口径修正 ★★
    #   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
    #   ⚠️ 卡商 **不扣工单重叠** —— 实证: 工单表 service_item/service_category 含「卡商」
    #      均为 0 行 → 卡商在工单侧无独立分类, 无"重叠"可言, 扣减会把卡商算少。
    #      (原 `- _overlap_suppl(CardComplaint, "(外)卡商")` 是 #52 的误用, 已撤销)
    card_total = int(supplementary["card"]["total"])
    merchant_total = int(merchant_ticket_total)

    # ---- 四源趋势合并补充表(逐日去重) ----
    # 趋势与总数同口径: 工单按日计数 + 补充表按投诉日期 (total - overlap)。
    # overlap = 当日已关联到本范围内工单的补充记录(工单已计入, 去重)。
    # 使「推送了但未落工单」的外部/卡商投诉也能在趋势里按投诉日期体现。
    # ⚠️ 2026-09-10 (#47): 卡商**独立成桶** —— 原先把卡商群投诉归入「外部投诉」桶
    #    (2026-09-01 口径), 现拆开: 外部趋势 = 工单 + 315净; 卡商趋势 = 卡商净。
    # ⚠️ 不可嵌套调用 _merge_trend(其第一参数要求 (day,count) 元组列表, 嵌套会把
    #    {day,count} 字典解包 → int('count') 崩溃)。两个补充表 dict 列表直接相加后一次合并。
    external_suppl_trend = _suppl_trend_rows(db, ExternalComplaint, "外部投诉", ts, te)
    card_suppl_trend = _suppl_trend_rows(db, CardComplaint, "(外)卡商", ts, te)
    external_trend_merged = _merge_trend(external_trend, external_suppl_trend)
    # 卡商无工单侧来源 → 趋势直接用补充表
    card_trend_merged = _merge_trend([], card_suppl_trend)
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
                "by_category": [],   # 卡商无工单侧分类; 分类见 complaint_subs(用 problem_category)
                "by_handler": [],     # 卡商处理人见 analytics.complaint_card_handlers
                "trend": card_trend_merged,
            },
        }
    )


# ---------- 投诉明细(三源统一) ----------


@router.get("/detail", response_model=R, summary="投诉明细列表(三源合并: 内部/商户号/外部)")
@cached_json(lambda *a, **kw: f"complaint:detail:{kw.get('start')}:{kw.get('end')}:{kw.get('source_type')}:{kw.get('subject')}:{kw.get('limit')}:{int(bool(kw.get('include_supplementary')))}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 60))
def complaint_detail(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="内部投诉/商户号投诉/外部投诉/(外)卡商"),
    subject: str | None = Query(None, description="按被投诉主体(归一化名, 如 云客猫)过滤明细"),
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
    # 315登记已办结的SN: 外部投诉工单状态未同步但实际已办结 → 明细状态显示已完成(与积压口径一致)
    ext_done_sns = _external_done_sns(db)
    for r in db.execute(stmt.order_by(action_time.desc().nulls_last())).all():
        source = r[2] or "内部投诉"
        raw_status = r[7]
        raw_handler = r[5]
        finish = r[8]
        register = r[6]
        norm_status = _norm_status(source, raw_status, finish, register, raw_handler)
        if source == "外部投诉" and r[1] in ext_done_sns:
            norm_status = "已完成"  # 积压闭环: 315登记已办结 → 工单视为已完成
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
            # 被投诉主体: 仅商户号投诉的 registrant 是主体; 内部/外部投诉 registrant 多为处理人或系统值, 由补充表回填
            "entity": r[9] if source == "商户号投诉" else None,
            "time": _to_sh_iso(display_dt),
            "register_time": _to_sh_iso(register),
            "finish_time": _to_sh_iso(finish) if _is_real_finish(finish, register) else None,
            "status": norm_status,
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
                    "company_entity": getattr(c, "company_entity", None),
                    "complaint_shop": getattr(c, "complaint_shop", None),
                    "problem": c.problem,
                    "solution": c.solution,
                    "result": c.result,
                    "handler": getattr(c, "handler", None) or getattr(c, "follower", None),
                    "handle_detail": getattr(c, "handle_detail", None) or getattr(c, "handle_result", None),
                    "handle_time": _to_sh_iso(c.handle_time) if hasattr(c, "handle_time") else None,
                    "matched_ticket_no": c.matched_ticket_no,
                }
                # 被投诉主体(仅315外部投诉有: 公司主体 → 投诉店铺兜底)
                entity = None
                if source_name == "315外部投诉":
                    ce = (suppl_item.get("company_entity") or "").strip()
                    cs = (suppl_item.get("complaint_shop") or "").strip()
                    entity = ce or (cs if cs and cs not in ("/", "-") else None)
                # 命中工单SN → 附加到对应工单行(补充说明)
                targets = [r for r in records if r.get("device_sn") == sn] if sn else []
                if targets:
                    for rec in targets:
                        rec.setdefault("supplementary", [])
                        if len(rec["supplementary"]) >= limit_per_sn:
                            continue
                        rec["supplementary"].append(suppl_item)
                    continue
                # 未关联工单(或SN为空) → 独立行, 归入对应桶
                #   ⚠️ 2026-09-10 (#47): 卡商**独立成桶**「(外)卡商」,
                #      原先 `main_source = "外部投诉"`(卡商被并入外部) → 已拆开。
                is_315 = source_name == "315外部投诉"
                main_source = "外部投诉" if is_315 else "(外)卡商"
                extra_rows.append({
                    "source": main_source,
                    "source_role": "supplementary",
                    "is_primary": False,
                    "no": suppl_item["complaint_no"] or suppl_item["matched_ticket_no"],
                    "device_sn": sn,
                    # 分类: 315表=平台(全国12315平台/公共服务平台等, 属外部投诉) /
                    #      卡商表=投诉群名无分类意义(群/主体名, 如"小皮丘&深圳联通"), 统一用「(外)卡商」
                    #      避免明细/看板分类筛选出现主体群名
                    "category": getattr(c, "platform", None) if is_315 else "(外)卡商",
                    "problem": c.problem,
                    "handler": _norm_handler(suppl_item["handler"]),
                    "entity": entity,
                    "time": suppl_item["handle_time"] or suppl_item["complaint_date"],
                    "register_time": suppl_item["complaint_date"],
                    "finish_time": suppl_item["handle_time"],
                    # 315行状态归一化(办结类→已完成, 与积压口径一致); 卡商行保持原文
                    "status": _norm_external_result_status(c.result) if is_315 else (c.result or "补充记录"),
                    "supplementary": [suppl_item],
                    "suppl_finish_time": suppl_item["handle_time"],
                    "suppl_problem": c.problem,
                    "suppl_count": 1,
                })

        _collect(ExternalComplaint, "device_sn", "315外部投诉")
        # #47: 统计桶名用「(外)卡商」(与数据源字面名"卡商群投诉"区分)
        _collect(CardComplaint, "device_sn", "(外)卡商")

        # 工单行便捷合并字段: 补充表里更详细的 完成时间/投诉内容(供明细表直接展示)
        for r in records:
            suppl = r.get("supplementary") or []
            r["supplementary"] = suppl
            if suppl:
                times = [s.get("handle_time") for s in suppl if s.get("handle_time")]
                r["suppl_finish_time"] = max(times) if times else None
                r["suppl_problem"] = next((s.get("problem") for s in suppl if s.get("problem")), None)
                r["suppl_count"] = len(suppl)
                # 外部投诉工单 registrant 多为处理人(无主体), 用补充表的公司主体回填
                if not r.get("entity"):
                    r["entity"] = next((s.get("company_entity") for s in suppl if s.get("company_entity")), None)

        records.extend(extra_rows)

    # 按动作时间降序排列
    records.sort(key=lambda x: x.get("time") or "", reverse=True)
    # F-P2-4 (问题4) + 审查 W2 修订(2026-09-04): 按被投诉主体过滤(归一化比较)。
    # subject_ranking 的口径 = 商户号工单主体(registrant) + 315/卡商补充表主体
    # (company_entity/投诉店铺)。detail 中同一投诉可能有两种落点:
    #   ① 主行 entity: 商户号行=registrant; 315 未匹配工单→独立行 entity=company_entity
    #   ② 挂靠: include_supplementary 时 315/卡商记录若 SN 匹配到工单, 挂在主行
    #      supplementary 下(主行 entity 可能是商户号/内部主体, 非该 315 的主体)
    # 故 subject 过滤需同时看 主行 entity 与 supplementary[] 里各补充项的 company_entity,
    # 任一归一命中 subject 即保留 —— 避免"315 挂靠到其它主体工单行"导致漏出(W2)。
    def _record_subject_entities(r: dict) -> set:
        """该明细行涉及的所有归一被投诉主体(主行 entity + 挂靠 315 的 company_entity/店铺)。"""
        out = set()
        ent = r.get("entity")
        if ent:
            norm = _norm_subject_entity(ent)
            if norm:
                out.add(norm)
        for s in (r.get("supplementary") or []):
            ce = (s.get("company_entity") or "").strip()
            cs = (s.get("complaint_shop") or "").strip()
            for v in (ce, cs):
                if v and v not in ("/", "-"):
                    norm = _norm_subject_entity(v)
                    if norm:
                        out.add(norm)
        return out

    if subject:
        _norm_subject = _norm_subject_entity(subject)
        if _norm_subject:
            records = [r for r in records if _norm_subject in _record_subject_entities(r)]
    # F-P1-2(审查 W3 修订 2026-09-04): registered/handled 计数统一基于"过滤后明细行"
    # 计算, 与 items 集合同口径(消除与全窗口 _sources_reg_handled 的口径差):
    #   registered = 行 register_time 落在窗口(start~end 北京日);  hand handled = 行 finish_time 非空
    #   (detail 仅在 _is_real_finish 真实完成时写 finish_time)且落在窗口。
    # register_time/finish_time 为 _to_sh_iso 输出(YYYY-MM-DD HH:MM), 取前10位与 start/end 比较。
    _ds = (start[:10] if start else "")
    _de = (end[:10] if end else "")
    def _in_window(dt_str: str | None) -> bool:
        if not dt_str or len(dt_str) < 10:
            return False
        d10 = dt_str[:10]
        return (not _ds or d10 >= _ds) and (not _de or d10 <= _de)
    _reg_cnt = sum(1 for r in records if _in_window(r.get("register_time")))
    _hdl_cnt = sum(1 for r in records if r.get("finish_time") and _in_window(r.get("finish_time")))
    # 保持顶层 reg_handled 三源结构向后兼容(全窗口三源; 前端明细标题主要用顶层
    # registered/handled, 无需三源细分时无需关心)。为不增加额外 6~10 次 count SQL,
    # 这里 total 直接用上面按行统计值(与 items 一致), 三源键保留默认空(前端不消费)。
    _reg_handled = {"total": {"registered": _reg_cnt, "handled": _hdl_cnt}}
    return R(data={
        "items": records[:limit],
        "total": len(records),
        "reg_handled": _reg_handled,
        "registered": _reg_cnt,
        "handled": _hdl_cnt,
    })


# ---------- 补充明细(315外部投诉 / 卡商群投诉,workbuddy推送) ----------


@router.get("/reconcile", response_model=R,
            summary="外部投诉对账·双边闭环(经营登记 ↔ 接待过程) (#56)")
def complaint_reconcile(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    detail_limit: int = Query(200, ge=1, le=2000, description="空悬明细返回上限"),
):
    """外部投诉对账 —— **经营登记(BMS) ↔ 接待过程(文档)** 双向闭环。

    ★ 用户语义（2026-09-11 权威）:
      > 「内部投诉/商户号投诉 跟外部投诉 **不存在对账关系**。
      >   外部投诉对账 = 服务类别为空、类型是几个平台的**外部投诉**与文档数据拉齐。
      >   两边数据都有意义: BMS 是**经营登记**; 文档是**接待过程/分析/处理**。」

    ★ 对账目标 = **双边空悬**（不是数量相等, 两侧不互相抵消）:
      · `doc_orphan`    —— 文档有、工单无 → 投诉了但**没建单**(漏登记)
      · `ticket_orphan` —— 工单有、文档无 → 建单了但**没过程记录**

    ★ 口径: 候选工单**只含对应渠道**的投诉类工单:
      · `external` —— 外部投诉类(`item` 命中 12315/公共服务平台/消协会/监督局…)
      · `card`     —— (外)卡商类(`item = 工信部投诉`) ← #57 摘出

    ★★ #57 (2026-09-11): 分渠道对账 ★★
      用户: 「工信部投诉 应该是卡商投诉匹配, 可以从外部投诉 摘出来 做单独统计对账去」
            →「全部摘出吧 然后再分开对账」
      证据: 卡商表 ⨝ 工信部工单 的配对内容全是「物联网卡/转售商/ICCID/卡商群名」,
            日期差恒 +1 天; 315 文档里**没有工信部**渠道。

    返回: `{"external": {...}, "card": {...}}` 两套完整双边对账。
    """
    from app.services.external_reconcile import reconcile_all

    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    data = reconcile_all(db, ts, te, detail_limit=detail_limit)
    return R(data=data)


@router.post("/reconcile/remark", response_model=R,
             summary="重算并回写 matched_ticket_no(修复「工单晚到不补挂」) (#56)")
def complaint_reconcile_remark(
    db: DBSession,
    _: ManagerUser,
    window_days: int = Query(90, ge=1, le=3650, description="重匹配回溯天数"),
    dry_run: bool = Query(False, description="true=只统计不改库"),
):
    """重算 `matched_ticket_no` —— 修复「工单晚到不补挂」。

    实测外部投诉类工单 `created_at - register_time` **平均滞后 71.1 天**
    (>30 天占 53%), 而匹配只在同步瞬间写一次 → 工单晚到时该 315 记录**永久空悬**。

    · 只写 `matched_ticket_no` / `matched_at`, **不动其他字段**
    · 幂等(候选集与选择规则确定), 可反复执行
    · 会把**已不合规的旧匹配**(指向非外部投诉类工单)清空, 避免统计污染
    """
    from app.services.external_reconcile import remark_matched

    stat = remark_matched(db, window_days=window_days, dry_run=dry_run)
    return R(data=stat)


@router.get("/sn-backfill", response_model=R,
            summary="采集质量·SN后补残影清单(已从统计排除) (#60)")
def complaint_sn_backfill(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(
        None, description="external=315 / card=(外)卡商, 缺省全部"
    ),
    limit: int = Query(500, ge=1, le=2000),
):
    """**SN 后补残影**清单 —— 采集质量问题可视化（#60）

    ## 这是什么

      源文档存在「**先建行、后补 SN**」的采集习惯 →
      `dedup_hash` 含 SN, 补 SN 后指纹变化 → **又 INSERT**
      → 同一投诉产生两条：

      | 行 | 特征 | created_at |
      |---|---|---|
      | **残影** | 缺 SN（字段不全） | **较早** |
      | 完整行 | 有 SN（字段齐全） | 较晚 |

      #59 已把残影标记为 `quality_status='dup_sn_backfill'`，
      并从统计口径**排除**（避免同一条投诉被算两次）。

    ## 本接口的用途

      · 采集质量**可视化** —— 让运营看到上游「先建行后补 SN」的规模与趋势
      · 提供**配对关系**（残影 ↔ 完整行），便于人工核对
      · ★ 明确标注「**已排除 / 原行保留**」，避免误解为数据丢失

    ## 数据安全

      ★ **不删除任何行** —— 残影保留, 仅打标 + 统计排除。
        可通过把 `quality_status` 置空**完整回滚**。
    """
    from app.models.card_complaint import CardComplaint
    from app.services.complaint_scope import QSTATUS_DUP_SN_BACKFILL

    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    out: list[dict] = []
    by_month: dict[str, int] = {}

    def _collect(model, label_col, src: str):
        conds: list = [model.quality_status == QSTATUS_DUP_SN_BACKFILL]
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        ghosts = db.execute(
            select(model.id, model.device_sn, model.complaint_date,
                   model.complainant_phone, label_col, model.created_at,
                   model.quality_note)
            .where(*conds)
            .order_by(model.complaint_date.desc().nullslast())
            .limit(limit)
        ).all()
        for gid, sn, cdate, ph, lbl, cat, note in ghosts:
            # 从 note 里解析出「被哪条完整行取代」(格式: 被 id=NNN 取代)
            full_id = None
            if note:
                m = re.search(r"id=(\d+)", note)
                if m:
                    full_id = int(m.group(1))
            # ★ 取**完整行**的 SN —— 残影本身缺 SN, 用户要看的是"补齐的那个SN"
            full_sn = full_created = None
            if full_id:
                frow = db.execute(
                    select(model.device_sn, model.created_at)
                    .where(model.__table__.c.id == full_id)
                ).first()
                if frow:
                    full_sn, full_created = frow[0], frow[1]
            out.append({
                "ghost_id": gid,
                "source_type": src,
                "device_sn": sn,              # 残影的 SN(通常为空)
                "full_device_sn": full_sn,    # ★ 完整行的 SN(即补齐的那个)
                "complaint_date": cdate.isoformat() if cdate else None,
                "complainant_phone": ph,
                "platform": lbl,
                "ghost_created_at": cat.isoformat() if cat else None,
                "full_created_at": full_created.isoformat() if full_created else None,
                "full_id": full_id,
                "quality_note": note,
                "table": model.__tablename__,
            })
            if cdate:
                k = cdate.strftime("%Y-%m")
                by_month[k] = by_month.get(k, 0) + 1

    if source_type in (None, "", "external"):
        _collect(ExternalComplaint, ExternalComplaint.platform, "external")
    if source_type in (None, "", "card"):
        _collect(CardComplaint, CardComplaint.complaint_group, "card")

    out.sort(key=lambda x: str(x.get("complaint_date") or ""), reverse=True)
    return R(data={
        "items": out[:limit],
        "total": len(out),
        "by_month": dict(sorted(by_month.items(), reverse=True)),
        "excluded_from_stats": True,   # ★ 明确: 已从统计排除
        "rows_preserved": True,        # ★ 明确: 原始行保留未删
    })


@router.get("/dedup-review", response_model=R, summary="投诉事件归并·待人工审核列表(#55)")
def complaint_dedup_review(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(
        None, description="external=315外部投诉 / card=(外)卡商, 缺省全部"
    ),
):
    """★ (#55) **待人工判定**的同设备同日多电话组 —— 供前端「人工审核」Tab。

    ## 为什么需要人工

      用户（2026-09-11）:
      > 「手机不同 默认 统计为不同, 但是提供一个人工再审核更合适点」

      `dedup_events` 的自动归并只在 **(SN, 归一化电话)** 一致时生效;
      电话不同 → **默认算不同投诉**(不自动归并), 但可能是:
        · 同一人换了号码 / 用家人号码登记
        · 联系人字段被业务填错
        · 确实就是两起不同的投诉
      → 无法程序判定, 必须人工。

    ## 归并组 `reason` 取值

      · `same_day_multi_phone` —— 同 SN 同投诉日 但电话 ≥2 个(最需人工)
      · `phone_missing`         —— 有 SN 但电话缺失

    ⚠️ 本接口**只读**, 不修改任何数据。人工结论目前落 `complaint_audits`
       (见 `POST /complaints/audits`), **不删原始行**。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    def _collect(model, ch_col) -> list[dict]:
        conds: list = [model.complaint_date.isnot(None)]
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        rows = db.execute(
            select(model.id, model.device_sn, model.complaint_date,
                   model.complainant_phone, ch_col)
            .where(*conds)
        ).all()
        payload = [
            {"id": r[0], "device_sn": r[1], "complaint_date": r[2],
             "complainant_phone": r[3], "channel": r[4]}
            for r in rows
        ]
        _, _, review = dedup_events(payload)
        # 补 `source_type`(前端区分卡商/315) 与 `record_count`(组内条数, 排序用)
        for g in review:
            g["source_type"] = "card" if model is CardComplaint else "external"
            g["record_count"] = len(g.get("record_ids") or [])
        return review

    out: list[dict] = []
    if source_type in (None, "", "external"):
        out += _collect(ExternalComplaint, ExternalComplaint.platform)
    if source_type in (None, "", "card"):
        out += _collect(CardComplaint, CardComplaint.complaint_group)

    # 影响面大的优先(组内条数降序), 再按投诉日倒序(近期更值得处理)
    out.sort(key=lambda g: (-int(g.get("record_count") or 0),
                            str(g.get("complaint_day") or "")),
             reverse=False)
    return R(data={"items": out, "total": len(out)})


@router.get("/supplementary", response_model=R, summary="投诉补充明细列表(315/卡商,workbuddy推送)")
def complaint_supplementary(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="external=315外部投诉/card=(外)卡商, 缺省查全部"),
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
        items.extend(_items(CardComplaint, "device_sn", "(外)卡商"))
    # 按投诉日期降序(主排序), 无日期的排末尾
    items.sort(key=lambda x: (x.get("complaint_date") is None, x.get("complaint_date") or ""), reverse=True)
    # F-P1-2: 补充记录 登记/处理 计数(明细标题展示)
    #   registered = 窗口内投诉日期记录数(= 查询已按 complaint_date 过滤, 即全部 items)
    #   handled    = 已办结记录数: 315 result∈办结类, 或 handle_time 落在窗口内
    _day_start = start[:10] if start else None
    _day_end = end[:10] if end else None

    def _is_handled(it: dict) -> bool:
        if (it.get("result") or "").strip() in _EXTERNAL_DONE_RESULTS:
            return True
        ht = it.get("handle_time")
        if ht and len(ht) >= 10:
            hd = ht[:10]
            if _day_start and hd < _day_start:
                return False
            if _day_end and hd > _day_end:
                return False
            return True
        return False

    _registered = len(items)
    _handled = sum(1 for it in items if _is_handled(it))
    return R(data={
        "items": items[:limit],
        "total": len(items),
        "registered": _registered,
        "handled": _handled,
    })


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
    """
    return set(db.scalars(
        select(ExternalComplaint.device_sn)
        .where(ExternalComplaint.result.in_(_EXTERNAL_DONE_RESULTS))
        .where(ExternalComplaint.device_sn.isnot(None))
    ).all())


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


@router.get("/backlog", response_model=R, summary="积压未处理投诉审核分析(统一口径: 归一化状态非已完成)")
@cached_json(lambda *a, **kw: f"complaint:backlog:{kw.get('start')}:{kw.get('end')}:{kw.get('source_type')}:{kw.get('status')}:{kw.get('category')}:{kw.get('has_handler')}:{kw.get('page')}:{kw.get('page_size')}:{kw.get('sort')}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 60))
def complaint_backlog(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="内部投诉/商户号投诉/外部投诉/(外)卡商"),
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
    backlog = _query_backlog_rows(db, ts, te, source_type, status, category, has_handler, sort)

    # 统计维度
    by_source = [{"name": k, "count": v} for k, v in sorted(Counter(r["source"] for r in backlog).items(), key=lambda x: -x[1])]
    by_category = [
        {"name": k, "count": v}
        for k, v in sorted(Counter(r["category"] for r in backlog).items(), key=lambda x: -x[1])[:15]
    ]
    # 投诉原因分布(替代区分度低的来源子类分类)
    _REASON_ORDER = ["退款/售后", "网络/技术", "套餐/计费", "充值/规则", "其他", "未填写"]
    _reason_counter = Counter(r["reason"] for r in backlog)
    by_reason = [
        {"name": k, "count": _reason_counter.get(k, 0)}
        for k in _REASON_ORDER
        if _reason_counter.get(k)
    ] + [
        {"name": k, "count": v}
        for k, v in sorted(_reason_counter.items(), key=lambda x: -x[1])
        if k not in _REASON_ORDER
    ]
    aging_counter = Counter()
    for r in backlog:
        ad = r["aging_days"]
        if ad is None:
            continue  # 无登记时间, 不进龄期分桶
        bucket = next((b for b, lo, hi in _BACKLOG_AGING_BUCKETS if lo <= ad and (hi is None or ad <= hi)), ">14天")
        aging_counter[bucket] += 1
    by_aging = [{"name": b, "count": aging_counter.get(b, 0)} for b, _, _ in _BACKLOG_AGING_BUCKETS]
    # 来源×龄期交叉统计: 每个来源的龄期分布, 回答「哪个来源积压最久」
    #   ⚠️ 2026-09-10 (#47): 由三源扩为**四源** —— 新增 `(外)卡商`(用户要求各统计口单列)。
    _SOURCE_ORDER = ["内部投诉", "商户号投诉", "外部投诉", "(外)卡商"]
    _sa_map: dict[str, dict[str, int]] = {}
    for r in backlog:
        ad = r["aging_days"]
        src = r["source"]
        if ad is None:
            continue
        bucket = next((b for b, lo, hi in _BACKLOG_AGING_BUCKETS if lo <= ad and (hi is None or ad <= hi)), ">14天")
        _sa_map.setdefault(src, {b: 0 for b, _, _ in _BACKLOG_AGING_BUCKETS})[bucket] += 1
    by_source_aging = [
        {
            "source": src,
            "buckets": [{"name": b, "count": _sa_map.get(src, {}).get(b, 0)} for b, _, _ in _BACKLOG_AGING_BUCKETS],
        }
        for src in _SOURCE_ORDER
        if _sa_map.get(src)
    ]
    by_handler = [
        {"name": k, "count": v}
        for k, v in sorted(Counter(r["handler"] for r in backlog).items(), key=lambda x: -x[1])[:15]
    ]
    unassigned = sum(1 for r in backlog if r["handler"] == "未分配")
    overdue = sum(1 for r in backlog if r["aging_days"] is not None and r["aging_days"] >= 8)  # 登记满7天未完成
    audited_total = sum(1 for r in backlog if r["audited"])

    total = len(backlog)
    max_aging = max((r["aging_days"] for r in backlog if r["aging_days"] is not None), default=0)
    start_idx = (page - 1) * page_size
    items = backlog[start_idx:start_idx + page_size]

    return R(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "max_aging": max_aging,
        "by_source": by_source,
        "by_category": by_category,
        "by_reason": by_reason,
        "by_aging": by_aging,
        "by_source_aging": by_source_aging,
        "by_handler": by_handler,
        "unassigned": unassigned,
        "overdue": overdue,
        "audited": audited_total,
        "items": items,
    })


@router.get("/backlog/export", summary="积压未处理投诉明细导出Excel")
def complaint_backlog_export(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="内部投诉/商户号投诉/外部投诉"),
    status: str | None = Query(None, description="归一化状态: 处理中/待处理/待跟进"),
    category: str | None = Query(None, description="按工单服务项(分类)过滤"),
    has_handler: bool | None = Query(None, description="true=已分派 / false=待分派"),
    sort: str = Query("aging_asc", description="aging_asc=最旧积压优先(默认) / register_desc / register_asc"),
):
    """导出积压未处理投诉明细 Excel。

    与 /backlog 完全同口径(_query_backlog_rows 统一归一化), 不受分页限制, 导出筛选后的全量行。
    """
    import io
    from urllib.parse import quote

    import pandas as pd
    from fastapi.responses import StreamingResponse

    from app.utils.excel import clean_df_for_excel

    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    rows = _query_backlog_rows(db, ts, te, source_type, status, category, has_handler, sort)

    df = pd.DataFrame([{
        "工单号": r["ticket_no"],
        "来源": r["source"],
        "分类": r["category"],
        "投诉原因": r["reason"],
        "登记时间": r["register_time"],
        "积压天数": r["aging_days"],
        "设备SN": r["device_sn"],
        "投诉内容": r["problem"],
        "处理人": r["handler"],
        "状态": r["status"],
        "完成时间": r["finish_time"],
        "已核查": "是" if r["audited"] else "否",
    } for r in rows])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        clean_df_for_excel(df).to_excel(writer, index=False, sheet_name="积压投诉")
    buf.seek(0)
    fname = quote(f"积压投诉明细_{start or '全量'}_{end or '至今'}.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


# ---------- 被投诉主体维度分析 ----------


@router.get("/subject-analysis", response_model=R, summary="被投诉主体维度分析(主体统计/严重性分布/升级链/退款关联)")
@cached_json(lambda *a, **kw: f"complaint:subject:{kw.get('start')}:{kw.get('end')}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 120))
def complaint_subject_analysis(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
):
    """被投诉主体维度分析。

    口径(2026-08-25 用户确认):
    - 主体统计仅覆盖有真实主体字段的两类来源:
      商户号投诉(工单 registrant=被投诉商户主体名) 与 外部投诉(external_complaints.company_entity/投诉店铺)。
      内部投诉(商品/服务投诉)工单无主体字段, 不纳入主体统计。
    - 严重性(内部<商户号<外部)以各来源数量分布呈现, 主体级以「外部投诉占比」体现严重度。
    - 升级链(传递性): 按设备SN汇总三源投诉组合, 呈现 内部→商户号→外部 升级漏斗。
    - 退款关联: 主体投诉SN关联的线上退款(refund_method='线上', created_at_raw 在范围内)笔数/金额,
      体现「投诉处理不好最终导致退款」。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    src_expr = _complaint_source_type_case()

    def _reg_conds(col):
        conds = []
        if ts:
            conds.append(col >= ts)
        if te:
            conds.append(col <= te)
        return conds

    def _day_str(dt) -> str | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return str(dt.astimezone(TZ).date())

    # ── 1. 商户号投诉主体 (工单 registrant=被投诉商户主体名) ──
    merchant_rows = db.execute(
        select(
            ServiceTicket.registrant,
            ServiceTicket.device_sn,
            ServiceTicket.register_time,
        )
        .where(src_expr == "商户号投诉")
        .where(ServiceTicket.registrant.isnot(None), ServiceTicket.registrant != "")
        .where(*_reg_conds(ServiceTicket.register_time))
    ).all()
    merchant_total = len(merchant_rows)
    merchant_by_subject: dict[str, dict] = {}
    for registrant, sn, rt in merchant_rows:
        subject = _norm_subject_entity(registrant)
        if not subject:
            continue
        b = merchant_by_subject.setdefault(subject, {"merchant_count": 0, "external_count": 0, "sns": set(), "trend": {}})
        b["merchant_count"] += 1
        if sn:
            b["sns"].add(sn)
        if rt:
            d = _day_str(rt)
            if d:
                b["trend"][d] = b["trend"].get(d, 0) + 1

    # ── 2. 外部投诉主体 (external_complaints.company_entity / 投诉店铺) ──
    _IGNORE_SHOP = {"/", "-", ""}
    ext_rows = db.execute(
        select(
            ExternalComplaint.company_entity,
            ExternalComplaint.complaint_shop,
            ExternalComplaint.device_sn,
            ExternalComplaint.complaint_date,
        ).where(*_reg_conds(ExternalComplaint.complaint_date))
    ).all()
    external_total = len(ext_rows)
    external_unattributed = 0
    external_by_subject: dict[str, dict] = {}
    for company_entity, complaint_shop, sn, cd in ext_rows:
        subject = None
        if company_entity and str(company_entity).strip():
            subject = _norm_subject_entity(company_entity)
        elif complaint_shop and str(complaint_shop).strip() not in _IGNORE_SHOP:
            subject = _norm_subject_entity(complaint_shop)
        if not subject:
            external_unattributed += 1
            continue
        b = external_by_subject.setdefault(subject, {"merchant_count": 0, "external_count": 0, "sns": set(), "trend": {}})
        b["external_count"] += 1
        if sn:
            b["sns"].add(sn)
        if cd:
            d = _day_str(cd)
            if d:
                b["trend"][d] = b["trend"].get(d, 0) + 1

    # ── 3. 主体合并 (商户号+外部 同名主体合并) ──
    subjects: dict[str, dict] = {}
    for name, b in merchant_by_subject.items():
        subjects[name] = {
            "name": name,
            "merchant_count": b["merchant_count"],
            "external_count": 0,
            "sns": set(b["sns"]),
            "trend": dict(b["trend"]),
        }
    for name, b in external_by_subject.items():
        s = subjects.setdefault(name, {
            "name": name, "merchant_count": 0, "external_count": 0, "sns": set(), "trend": {},
        })
        s["external_count"] = b["external_count"]
        s["sns"].update(b["sns"])
        for d, c in b["trend"].items():
            s["trend"][d] = s["trend"].get(d, 0) + c

    # ── 4. 升级链漏斗 (按SN: 内部/商户号/外部 出现组合) ──
    sn_sources: dict[str, set] = {}
    for sn, src in db.execute(
        select(ServiceTicket.device_sn, src_expr)
        .where(_ticket_category_case() == "投诉")
        .where(ServiceTicket.device_sn.isnot(None))
        .where(*_reg_conds(ServiceTicket.register_time))
    ).all():
        sn_sources.setdefault(sn, set()).add(src or "内部投诉")
    for company_entity, complaint_shop, sn, _cd in ext_rows:
        if sn:
            sn_sources.setdefault(sn, set()).add("外部投诉")

    _FUNNEL_ORDER = [
        ("internal_only", "仅内部投诉"),
        ("merchant_only", "仅商户号投诉"),
        ("external_only", "仅外部投诉"),
        ("internal_merchant", "内部→商户号"),
        ("internal_external", "内部→外部"),
        ("merchant_external", "商户号→外部"),
        ("full_chain", "内部→商户号→外部"),
    ]
    funnel: dict[str, int] = {k: 0 for k, _ in _FUNNEL_ORDER}
    for srcs in sn_sources.values():
        has_int = "内部投诉" in srcs
        has_mer = "商户号投诉" in srcs
        has_ext = "外部投诉" in srcs
        if has_int and has_mer and has_ext:
            funnel["full_chain"] += 1
        elif has_int and has_mer:
            funnel["internal_merchant"] += 1
        elif has_int and has_ext:
            funnel["internal_external"] += 1
        elif has_mer and has_ext:
            funnel["merchant_external"] += 1
        elif has_int:
            funnel["internal_only"] += 1
        elif has_mer:
            funnel["merchant_only"] += 1
        elif has_ext:
            funnel["external_only"] += 1
    escalation_funnel = [{"key": k, "name": n, "count": funnel[k]} for k, n in _FUNNEL_ORDER]

    # ── 5. 退款关联 (主体投诉SN → 线上退款) ──
    all_subject_sns: set[str] = set()
    for s in subjects.values():
        all_subject_sns.update(s["sns"])
    refund_by_sn: dict[str, tuple[int, float]] = {}
    if all_subject_sns:
        for sn, cnt, amt in db.execute(
            select(Refund.device_sn, func.count(), func.coalesce(func.sum(Refund.refund_amount), 0))
            .where(Refund.refund_method == "线上")
            .where(Refund.device_sn.in_(all_subject_sns))
            .where(*_reg_conds(Refund.created_at_raw))
            .group_by(Refund.device_sn)
        ).all():
            refund_by_sn[sn] = (int(cnt), float(amt))

    refund_total_count = 0
    refund_total_amount = 0.0
    for s in subjects.values():
        rc = sum(refund_by_sn[sn][0] for sn in s["sns"] if sn in refund_by_sn)
        ra = sum(refund_by_sn[sn][1] for sn in s["sns"] if sn in refund_by_sn)
        s["refund_count"] = rc
        s["refund_amount"] = round(ra, 2)
        refund_total_count += rc
        refund_total_amount += ra
        s["total"] = s["merchant_count"] + s["external_count"]
        s["escalated"] = s["merchant_count"] > 0 and s["external_count"] > 0
        s.pop("sns", None)

    # ── 6. 主体Top榜 (按总量降序) ──
    subject_ranking = sorted(
        subjects.values(),
        key=lambda x: (-x["total"], -x["external_count"], x["name"]),
    )[:20]

    # ── 7. 严重性数量分布 (商户号投诉 vs 外部投诉, 含主体识别口径) ──
    severity_distribution = [
        {"name": "商户号投诉", "count": merchant_total},
        {"name": "外部投诉", "count": external_total},
    ]

    # ── 8. 趋势: 全量按日 (商户号工单登记日 + 外部投诉日期) ──
    trend_map: dict[str, dict[str, int]] = {}
    for registrant, _sn, rt in merchant_rows:
        d = _day_str(rt)
        if not d:
            continue
        b = trend_map.setdefault(d, {"商户号投诉": 0, "外部投诉": 0})
        b["商户号投诉"] += 1
    for _ce, _cs, _sn, cd in ext_rows:
        d = _day_str(cd)
        if not d:
            continue
        b = trend_map.setdefault(d, {"商户号投诉": 0, "外部投诉": 0})
        b["外部投诉"] += 1
    subject_trend = [
        {"day": d, "商户号投诉": v["商户号投诉"], "外部投诉": v["外部投诉"]}
        for d, v in sorted(trend_map.items())
    ]

    # ── 9. Top5 主体逐日趋势 ──
    top5 = subject_ranking[:5]
    top_subject_trend = []
    for s in top5:
        trend = s.get("trend") or {}
        top_subject_trend.append({
            "name": s["name"],
            "trend": [{"day": d, "count": trend[d]} for d in sorted(trend)],
        })

    # 排名项不携带逐日 trend(仅Top5趋势需要, 避免响应体膨胀)
    for s in subject_ranking:
        s.pop("trend", None)

    return R(data={
        "summary": {
            "subject_total": len(subjects),
            "merchant_total": merchant_total,
            "external_total": external_total,
            "external_attributed": external_total - external_unattributed,
            "external_unattributed": external_unattributed,
            "refund_count": refund_total_count,
            "refund_amount": round(refund_total_amount, 2),
        },
        "subject_ranking": subject_ranking,
        "severity_distribution": severity_distribution,
        "escalation_funnel": escalation_funnel,
        "subject_trend": subject_trend,
        "top_subject_trend": top_subject_trend,
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
