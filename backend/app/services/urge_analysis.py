"""催单风险分析专用视图 - 手动实现(analysis_engine 分派 code→本模块 HANDLERS)

用途:
  退款看板 → 预测性退款参考因素 (退款催单风险 5 张)
  投诉看板 → 催单→投诉关联分析 (3 张)

调用时机: analysis_engine.execute_view 中若 view.code ∈ HANDLERS 则直接调用,
不经过 segments/SQLAlchemy 自动生成。
"""

from __future__ import annotations

import logging
from datetime import datetime as _dt
from datetime import timedelta as _td
from datetime import timezone as _tz
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Date,
    Numeric,
    and_,
    case,
    cast,
    desc,
    func,
    select,
    text,
)
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket

TZ = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)

HANDLERS: dict[str, Callable[[Session, dict | None], dict]] = {}


def _register(code: str):
    """装饰器:注册手动视图 code→函数"""
    def deco(fn: Callable[[Session, dict | None], dict]):
        HANDLERS[code] = fn
        return fn
    return deco


# ---------- 工具 ----------

def _parse_params(params: dict | None):
    """解析 params.start/end 为北京时间 date 区间 (默认近30天)"""
    p = params or {}
    today = _dt.now(TZ).date()
    sd = p.get("start") or (today - _td(days=29)).isoformat()
    ed = p.get("end") or today.isoformat()
    try:
        s = _dt.strptime(sd, "%Y-%m-%d").date()
    except Exception:
        s = today - _td(days=29)
    try:
        e = _dt.strptime(ed, "%Y-%m-%d").date()
    except Exception:
        e = today
    return s, e


def _utc_range_for_sh_date(start_d, end_d):
    """北京时间 [start,end] (含两端) 日期区间 → UTC 时间范围 (ts_begin, ts_end_excl)"""
    # [YYYY-MM-DD 00:00:00+08:00, 次日00:00:00+08:00)
    sh_day_start = _dt.combine(start_d, _dt.min.time(), tzinfo=TZ)
    sh_next_day = _dt.combine(end_d + _td(days=1), _dt.min.time(), tzinfo=TZ)
    return sh_day_start.astimezone(_tz.utc), sh_next_day.astimezone(_tz.utc)


def _urge_bucket_col(col):
    """催单次数分桶 CASE WHEN → bucket_label

    0次      → '0次'
    1-2次    → '1-2次'
    3-4次    → '3-4次'
    ≥5次     → '≥5次'
    NULL/空  → '未催'
    """
    return case(
        (col.is_(None), "未催"),
        (col <= 0, "0次"),
        (and_(col >= 1, col <= 2), "1-2次"),
        (and_(col >= 3, col <= 4), "3-4次"),
        (col >= 5, "≥5次"),
        else_="未催",
    ).label("bucket")


_BUCKET_ORDER = ["0次", "1-2次", "3-4次", "≥5次", "未催"]


# ============================================================
# 退款看板 5 张: 待处理退款工单催单风险预测
# ============================================================

@_register("preset_refund_urge_kpi")
def refund_urge_kpi(db: Session, params: dict | None) -> dict:
    """KPI 卡(以 table 形式输出 4 指标一行,前端渲染为 table)
    - 当前待处理退款工单量(全局当前快照,不受 start/end 过滤)
    - 平均待处理时长(小时, 基于 now - register_time)
    - 平均催单次数
    - 催单≥3次占比(%)
    """
    T = ServiceTicket
    now_utc = _dt.now(_tz.utc)

    # total_cnt / avg_wait_h / avg_urge_cnt / urge_ge3_cnt
    # ⚠️ wait_h 直接使用 hours 数值表达式 (epoch_diff / 3600) → 不能再套一层 extract("epoch", ...)
    #    因为 SQLAlchemy extract(field, expr) 要求 expr 是 timestamp/interval, 而数值会报错:
    #    "function pg_catalog.extract(unknown, numeric) does not exist"
    # ⚠️ 不要用 select(T).add_columns(...) 选 T 所有列再聚合 → 会报 GroupingError:
    #    "column service_tickets.ticket_no must appear in the GROUP BY clause"
    #    KPI 一行汇总, 直接 select 4 个聚合列即可.
    wait_h_as_hours = text(
        "EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'UTC' - register_time AT TIME ZONE 'UTC')) / 3600"
    )
    sub = select(
        func.count().label("total_cnt"),
        func.avg(wait_h_as_hours).label("avg_wait_h"),
        func.avg(cast(func.coalesce(T.urge_count, 0), Numeric(14, 2))).label("avg_urge_cnt"),
        func.sum(case((func.coalesce(T.urge_count, 0) >= 3, 1), else_=0)).label("urge_ge3_cnt"),
    ).where(
        T.status == "待处理",
        T.service_category == "退款",
    )
    r = db.execute(sub).first()
    # 统一用 _mapping[] 访问, 避免列名与 Python 标识符不兼容(≥、()、数字开头等)
    M = r._mapping
    total = int(M["total_cnt"] or 0)
    avg_wait = round(float(M["avg_wait_h"] or 0), 1)
    avg_urge = round(float(M["avg_urge_cnt"] or 0), 2)
    ge3_cnt = int(M["urge_ge3_cnt"] or 0)
    ge3_pct = round(ge3_cnt * 100.0 / total, 1) if total else 0.0

    return {
        "chart_type": "table",
        "data": {
            "columns": ["待处理退款工单数", "平均待处理时长(h)", "平均催单次数", "催单≥3次占比(%)", "催单≥3次工单数"],
            "rows": [{
                "待处理退款工单数": total,
                "平均待处理时长(h)": avg_wait,
                "平均催单次数": avg_urge,
                "催单≥3次占比(%)": ge3_pct,
                "催单≥3次工单数": ge3_cnt,
            }],
        },
    }


@_register("preset_refund_urge_bucket")
def refund_urge_bucket(db: Session, params: dict | None) -> dict:
    """催单次数分桶 柱状 → 工单数 + 预估退款金额合计 (当前待处理退款快照)"""
    T = ServiceTicket
    bucket = _urge_bucket_col(T.urge_count)
    rows = db.execute(
        select(
            bucket,
            func.count().label("工单数"),
            func.sum(func.coalesce(T.estimated_amount, 0)).label("预估退款金额"),
        )
        .where(T.status == "待处理", T.service_category == "退款")
        .group_by("bucket")
    ).all()
    # 按 bucket 顺序对齐, 缺项补 0 → 全部用 _mapping[] 访问, 避免非标识符列名踩坑
    m: dict[str, dict] = {}
    for r in rows:
        RM = r._mapping
        bucket_key = RM["bucket"]
        m[bucket_key] = {
            "工单数": int(RM["工单数"] or 0),
            "预估退款金额": round(float(RM["预估退款金额"] or 0), 2),
        }
    data = []
    for b in _BUCKET_ORDER:
        row = m.get(b, {"工单数": 0, "预估退款金额": 0.0})
        data.append({"催单次数分桶": b, "工单数": row["工单数"], "预估退款金额": row["预估退款金额"]})
    return {
        "chart_type": "bar",
        "data": {
            "x_axis": [d["催单次数分桶"] for d in data],
            "series": [
                {"name": "工单数", "data": [d["工单数"] for d in data], "series_type": "bar"},
                {"name": "预估退款金额", "data": [d["预估退款金额"] for d in data], "series_type": "line", "yAxisIndex": 1},
            ],
            "dim_keys": ["催单次数分桶"],
            "measure_keys": ["工单数", "预估退款金额"],
        },
    }


@_register("preset_refund_urge_person_top")
def refund_urge_person_top(db: Session, params: dict | None) -> dict:
    """按 最后催单人 / 分配人员 → 催单≥3次的待处理退款工单数 TOP15 (table形式,两列对比)"""
    T = ServiceTicket
    ge3 = and_(func.coalesce(T.urge_count, 0) >= 3, T.status == "待处理", T.service_category == "退款")

    # 1. 按最后催单人 TOP
    by_urge_user = db.execute(
        select(
            func.coalesce(T.last_urge_by, "(未催)").label("人员"),
            func.count().label("催单≥3次待处理工单数"),
        )
        .where(ge3)
        .group_by("人员")
        .order_by(func.count().desc())
        .limit(15)
    ).all()

    # 2. 按分配人员 TOP
    by_assignee = db.execute(
        select(
            func.coalesce(T.assignee, "(无分配人)").label("人员"),
            func.count().label("催单≥3次待处理工单数"),
        )
        .where(ge3)
        .group_by("人员")
        .order_by(func.count().desc())
        .limit(15)
    ).all()

    # 用 table 输出: 列 = [最后催单人|分配人员|工单数] (并排放, 分两张子表有点复杂, 这里合并成一个宽表: 两部分各占 2 列, 中间用空行对齐)
    # 统一用 _mapping[] 访问, 避免 "催单≥3次待处理工单数" 里的 ≥ 和数字无法做 Python 属性访问(SyntaxError: invalid character '≥')
    max_rows = max(len(by_urge_user), len(by_assignee), 1)
    rows = []
    for i in range(max_rows):
        u_row = by_urge_user[i] if i < len(by_urge_user) else None
        a_row = by_assignee[i] if i < len(by_assignee) else None
        u_map = u_row._mapping if u_row else None
        a_map = a_row._mapping if a_row else None
        rows.append({
            "排名": i + 1,
            "最后催单人": str(u_map["人员"]) if u_map else "",
            "催单人待处理工单数": int(u_map["催单≥3次待处理工单数"]) if u_map else 0,
            "分配人员": str(a_map["人员"]) if a_map else "",
            "分配人员待处理工单数": int(a_map["催单≥3次待处理工单数"]) if a_map else 0,
        })
    return {
        "chart_type": "table",
        "data": {
            "columns": ["排名", "最后催单人", "催单人待处理工单数", "分配人员", "分配人员待处理工单数"],
            "rows": rows,
        },
    }


@_register("preset_refund_urge_scatter")
def refund_urge_scatter(db: Session, params: dict | None) -> dict:
    """散点图 (散点当前引擎/前端不支持, 以 table 形式输出, 列为:
       工单号 / 待处理时长h / 催单次数 / 预估金额 / 分配人员 / 最后催单人 / service_item
       → 用颜色分级(催单次数 ≥ 3 = 高亮) 让用户一眼看出高风险长尾工单
    """
    T = ServiceTicket
    wait_h_expr = text("EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'UTC' - register_time AT TIME ZONE 'UTC')) / 3600")
    # ⚠️ PG 的 round 第二个参数(小数位数)只支持 numeric, 不支持 double precision。
    #    若直接 round(float8_expr, 1) → UndefinedFunction: function round(double precision, integer) does not exist
    #    必须先 cast(expr AS NUMERIC), 再 round(numeric, 1), 最后 cast 到总精度 Numeric(14,1)。
    wait_h_rounded = cast(
        func.round(cast(wait_h_expr, Numeric), 1), Numeric(14, 1)
    ).label("待处理时长(h)")
    rows = db.execute(
        select(
            T.ticket_no,
            wait_h_rounded,
            func.coalesce(T.urge_count, 0).label("催单次数"),
            func.coalesce(T.estimated_amount, 0).label("预估退款金额"),
            func.coalesce(T.assignee, "-").label("分配人员"),
            func.coalesce(T.last_urge_by, "-").label("最后催单人"),
            func.coalesce(T.service_item, "-").label("退款原因"),
            func.coalesce(T.phone, "-").label("联系电话"),
        )
        .where(T.status == "待处理", T.service_category == "退款")
        # 排序优先级: 先催单次数多→再待处理时长→再预估金额大 (一眼看出高风险)
        # ⚠️ TextClause 没有 .desc() 方法, 用全局 desc(expr) 包装
        .order_by(
            func.coalesce(T.urge_count, 0).desc(),
            desc(wait_h_expr),
            func.coalesce(T.estimated_amount, 0).desc(),
        )
        .limit(100)
    ).all()
    data = []
    for r in rows:
        RM = r._mapping
        urge_cnt = int(RM["催单次数"])
        wait_h = float(RM["待处理时长(h)"] or 0)
        data.append({
            "工单号": RM["ticket_no"],
            "待处理时长(h)": wait_h,
            "催单次数": urge_cnt,
            "预估退款金额": round(float(RM["预估退款金额"] or 0), 2),
            "分配人员": RM["分配人员"],
            "最后催单人": RM["最后催单人"],
            "退款原因": RM["退款原因"],
            "联系电话": RM["联系电话"],
            "风险等级": (
                "极高" if urge_cnt >= 5 or wait_h >= 72
                else "高" if urge_cnt >= 3 or wait_h >= 48
                else "中" if urge_cnt >= 1 or wait_h >= 24
                else "低"
            ),
        })
    cols = ["工单号", "风险等级", "催单次数", "待处理时长(h)", "预估退款金额", "退款原因", "分配人员", "最后催单人", "联系电话"]
    return {"chart_type": "table", "data": {"columns": cols, "rows": data}}


@_register("preset_refund_urge_top50")
def refund_urge_top50(db: Session, params: dict | None) -> dict:
    """Top50 高风险工单明细 (复合评分 = 催单次数*10 + 待处理天数*3 + log(预估金额+1)*2)

    列: 工单号/登记时间/催单次数/最后催单时间/待处理时长h/预估金额/分配人员/派发人员/最后催单人/服务项/处理节点/联系电话/风险评分
    """
    T = ServiceTicket
    wait_h_expr = text("EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'UTC' - register_time AT TIME ZONE 'UTC')) / 3600")
    score_expr = text(
        "COALESCE(urge_count,0)*10"
        " + (EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'UTC' - register_time AT TIME ZONE 'UTC')) / 3600 / 24) * 3"
        " + LN(COALESCE(estimated_amount,0)::float + 1) * 2"
    )
    # ⚠️ PG round(double precision, integer) 不存在, 必须先 cast 到 NUMERIC 再 round(..., 1)
    wait_h_rounded = cast(func.round(cast(wait_h_expr, Numeric), 1), Numeric(14, 1)).label("待处理时长(h)")
    score_rounded = cast(func.round(cast(score_expr, Numeric), 1), Numeric(14, 1)).label("风险评分")
    rows = db.execute(
        select(
            T.ticket_no,
            T.register_time,
            func.coalesce(T.urge_count, 0).label("催单次数"),
            T.last_urge_time,
            wait_h_rounded,
            func.coalesce(T.estimated_amount, 0).label("预估退款金额"),
            func.coalesce(T.assignee, "-").label("分配人员"),
            func.coalesce(T.dispatcher, "-").label("派发人员"),
            func.coalesce(T.last_urge_by, "-").label("最后催单人"),
            func.coalesce(T.contact_person, "-").label("联系人员"),
            func.coalesce(T.service_item, "-").label("退款原因"),
            func.coalesce(T.handle_node, "-").label("处理节点"),
            func.coalesce(T.phone, "-").label("联系电话"),
            score_rounded,
        )
        .where(T.status == "待处理", T.service_category == "退款")
        # ⚠️ TextClause 没有 .desc() → 用 desc(expr) 包装
        .order_by(desc(score_expr))
        .limit(50)
    ).all()

    def _fmt_sh(ts):
        if not ts:
            return "-"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        return ts.astimezone(TZ).strftime("%m-%d %H:%M")

    data = []
    for r in rows:
        RM = r._mapping
        urge_cnt = int(RM["催单次数"])
        wait_h = float(RM["待处理时长(h)"] or 0)
        data.append({
            "风险评分": float(RM["风险评分"] or 0),
            "工单号": RM["ticket_no"],
            "登记时间": _fmt_sh(RM["register_time"]),
            "催单次数": urge_cnt,
            "最后催单时间": _fmt_sh(RM["last_urge_time"]),
            "待处理时长(h)": wait_h,
            "预估退款金额": round(float(RM["预估退款金额"] or 0), 2),
            "退款原因": RM["退款原因"],
            "分配人员": RM["分配人员"],
            "派发人员": RM["派发人员"],
            "最后催单人": RM["最后催单人"],
            "联系人员": RM["联系人员"],
            "处理节点": RM["处理节点"],
            "联系电话": RM["联系电话"],
        })
    cols = [
        "风险评分", "工单号", "登记时间", "催单次数", "最后催单时间", "待处理时长(h)",
        "预估退款金额", "退款原因", "分配人员", "派发人员", "最后催单人",
        "联系人员", "处理节点", "联系电话",
    ]
    return {"chart_type": "table", "data": {"columns": cols, "rows": data}}


# ============================================================
# 投诉看板 3 张: 催单 → 投诉 关联分析
# ============================================================

@_register("preset_complaint_urge_trend")
def complaint_urge_trend(db: Session, params: dict | None) -> dict:
    """双轴趋势 (近 N 天, 按 start/end 过滤):
       柱 = 当日「催单≥3次的退款工单数(全状态)」
       线 = 当日「外部投诉量 + 卡投诉量」合计
       观察 高催单日 → 投诉量 是否同步上升 (正相关即为催单对投诉有显著影响)
    """
    s_d, e_d = _parse_params(params)
    ts_utc, te_utc = _utc_range_for_sh_date(s_d, e_d)

    # 生成日轴
    # ⚠️ 不要用 :sd::date 写法, SQLAlchemy 会把 :sd:: 当成绑定参数名 sd: (含冒号) → PG syntax error
    #    用 CAST(:bind AS date) 或 (:bind)::date
    days_q = db.execute(text("""
        SELECT d::date AS d
        FROM generate_series(CAST(:sd AS date), CAST(:ed AS date), INTERVAL '1 day') d
    """), {"sd": s_d.isoformat(), "ed": e_d.isoformat()}).all()
    days = [r._mapping["d"] for r in days_q]

    # 1) 每日催单≥3退款工单量:按 register_time 北京时间分日
    T = ServiceTicket
    urge_daily_q = db.execute(
        select(
            cast(func.date_trunc("day", T.register_time.op("AT TIME ZONE")('Asia/Shanghai')), Date).label("d"),
            func.count().label("cnt"),
        )
        .where(
            T.register_time >= ts_utc,
            T.register_time < te_utc,
            T.service_category == "退款",
            func.coalesce(T.urge_count, 0) >= 3,
        )
        .group_by("d")
    ).all()
    urge_map = {r._mapping["d"]: int(r._mapping["cnt"] or 0) for r in urge_daily_q}

    # 2) 每日外部投诉量
    ec_q = db.execute(
        select(
            cast(func.date_trunc("day", ExternalComplaint.complaint_date.op("AT TIME ZONE")('Asia/Shanghai')), Date).label("d"),
            func.count().label("cnt"),
        )
        .where(
            ExternalComplaint.complaint_date >= ts_utc,
            ExternalComplaint.complaint_date < te_utc,
        )
        .group_by("d")
    ).all()
    ec_map = {r._mapping["d"]: int(r._mapping["cnt"] or 0) for r in ec_q}

    # 3) 每日卡投诉量
    cc_q = db.execute(
        select(
            cast(func.date_trunc("day", CardComplaint.complaint_date.op("AT TIME ZONE")('Asia/Shanghai')), Date).label("d"),
            func.count().label("cnt"),
        )
        .where(
            CardComplaint.complaint_date >= ts_utc,
            CardComplaint.complaint_date < te_utc,
        )
        .group_by("d")
    ).all()
    cc_map = {r._mapping["d"]: int(r._mapping["cnt"] or 0) for r in cc_q}

    x_axis, ser_urge, ser_comp = [], [], []
    for d in days:
        x_axis.append(d.strftime("%m-%d"))
        ser_urge.append(urge_map.get(d, 0))
        ser_comp.append(ec_map.get(d, 0) + cc_map.get(d, 0))

    return {
        "chart_type": "bar",
        "data": {
            "x_axis": x_axis,
            "series": [
                {"name": "催单≥3次退款工单(柱)", "data": ser_urge, "series_type": "bar"},
                {"name": "投诉总量(线)", "data": ser_comp, "series_type": "line", "yAxisIndex": 1},
            ],
            "dim_keys": ["日期"],
            "measure_keys": ["催单≥3次退款工单(柱)", "投诉总量(线)"],
        },
    }


@_register("preset_complaint_urge_conversion")
def complaint_urge_conversion(db: Session, params: dict | None) -> dict:
    """投诉转化率按催单次数分桶:
    样本 = 在 [start, end] 区间内登记且 service_category=退款 的工单
    对每个工单算 "催单次数分桶", 再算该桶中 "最终产生投诉(按 phone 关联投诉记录)" 的比例%
    因SN/手机号跨表关联 投诉记录, 这里用 phone 做关联 (1工单 → 多条投诉 OK)

    输出: 分组柱 (投诉工单数, 总退款工单数, 转化率%)
    """
    s_d, e_d = _parse_params(params)
    ts_utc, te_utc = _utc_range_for_sh_date(s_d, e_d)

    T = ServiceTicket
    bucket = _urge_bucket_col(T.urge_count)

    # 1) 各桶退款工单总数 (按 start/end 区间登记时间 filter)
    base = (
        select(bucket.label("b"), func.count().label("total_tickets"))
        .where(
            T.register_time >= ts_utc,
            T.register_time < te_utc,
            T.service_category == "退款",
        )
        .group_by("b")
    )
    rows_total = db.execute(base).all()
    total_map = {r._mapping["b"]: int(r._mapping["total_tickets"] or 0) for r in rows_total}

    # 2) 各桶有投诉的工单数: refund ticket phone 与 外部/卡投诉 phone 交集
    comp_ticket_bucket = db.execute(text("""
        WITH refund_t AS (
            SELECT ticket_no,
                   phone,
                   CASE
                       WHEN urge_count IS NULL THEN '未催'
                       WHEN urge_count <= 0 THEN '0次'
                       WHEN urge_count BETWEEN 1 AND 2 THEN '1-2次'
                       WHEN urge_count BETWEEN 3 AND 4 THEN '3-4次'
                       WHEN urge_count >= 5 THEN '≥5次'
                       ELSE '未催'
                   END AS b
            FROM service_tickets
            WHERE service_category = '退款'
              AND register_time >= :ts AND register_time < :te
        ),
        comp_phones AS (
            SELECT complainant_phone AS phone FROM external_complaints
            WHERE complaint_date >= :ts AND complaint_date < :te
              AND complainant_phone IS NOT NULL
            UNION
            SELECT complainant_phone AS phone FROM card_complaints
            WHERE complaint_date >= :ts AND complaint_date < :te
              AND complainant_phone IS NOT NULL
        )
        SELECT r.b AS b,
               COUNT(DISTINCT r.ticket_no) FILTER (WHERE p.phone IS NOT NULL) AS comp_cnt
        FROM refund_t r
        LEFT JOIN comp_phones p ON r.phone IS NOT NULL AND r.phone = p.phone
        GROUP BY 1
    """), {"ts": ts_utc, "te": te_utc}).all()
    comp_map = {r._mapping["b"]: int(r._mapping["comp_cnt"] or 0) for r in comp_ticket_bucket}

    rows_list = []
    for b in _BUCKET_ORDER:
        tot = total_map.get(b, 0)
        comp = comp_map.get(b, 0)
        pct = round(comp * 100.0 / tot, 2) if tot else 0.0
        rows_list.append({
            "催单次数分桶": b,
            "退款工单数": tot,
            "投诉工单数": comp,
            "投诉转化率(%)": pct,
        })
    return {
        "chart_type": "bar",
        "data": {
            "x_axis": [r["催单次数分桶"] for r in rows_list],
            "series": [
                {"name": "退款工单数", "data": [r["退款工单数"] for r in rows_list], "series_type": "bar"},
                {"name": "投诉工单数", "data": [r["投诉工单数"] for r in rows_list], "series_type": "bar"},
                {"name": "投诉转化率(%)", "data": [r["投诉转化率(%)"] for r in rows_list], "series_type": "line", "yAxisIndex": 1},
            ],
            "dim_keys": ["催单次数分桶"],
            "measure_keys": ["退款工单数", "投诉工单数", "投诉转化率(%)"],
        },
    }


@_register("preset_complaint_urge_contribution")
def complaint_urge_contribution(db: Session, params: dict | None) -> dict:
    """贡献度饼: 投诉工单中「此前有过催单(urge_count>=1 的退款工单 关联phone)」占比
    分 3 类:
      - 有催单记录(>=1次)且为退款工单 phone 匹配
      - 退款工单但无催单 (urge_count<=0 OR NULL)
      - 非退款工单匹配 / 无匹配退款工单
    """
    s_d, e_d = _parse_params(params)
    ts_utc, te_utc = _utc_range_for_sh_date(s_d, e_d)

    r = db.execute(text("""
        WITH comp_all AS (
            SELECT complainant_phone AS phone
            FROM external_complaints
            WHERE complaint_date >= :ts AND complaint_date < :te AND complainant_phone IS NOT NULL
            UNION ALL
            SELECT complainant_phone AS phone
            FROM card_complaints
            WHERE complaint_date >= :ts AND complaint_date < :te AND complainant_phone IS NOT NULL
        ),
        refund_tag AS (
            SELECT phone,
                   MAX(CASE WHEN COALESCE(urge_count, 0) >= 1 THEN 1 ELSE 0 END) AS had_urge,
                   MAX(1) AS is_refund_phone
            FROM service_tickets
            WHERE service_category = '退款' AND phone IS NOT NULL
            GROUP BY phone
        )
        SELECT
            COUNT(*)                                                           AS total_comp,
            COUNT(*) FILTER (WHERE rt.had_urge = 1)                            AS had_urge,
            COUNT(*) FILTER (WHERE rt.is_refund_phone = 1 AND (rt.had_urge IS NULL OR rt.had_urge = 0)) AS refund_no_urge,
            COUNT(*) FILTER (WHERE rt.phone IS NULL)                           AS no_refund_match
        FROM comp_all c
        LEFT JOIN refund_tag rt ON c.phone = rt.phone
    """), {"ts": ts_utc, "te": te_utc}).first()
    RM = r._mapping

    items = [
        {"name": "有催单记录(≥1次)的退款工单用户投诉", "value": int(RM["had_urge"] or 0)},
        {"name": "退款工单但无催单的投诉", "value": int(RM["refund_no_urge"] or 0)},
        {"name": "与退款工单无关的投诉", "value": int(RM["no_refund_match"] or 0)},
    ]
    total = sum(x["value"] for x in items)
    return {
        "chart_type": "pie",
        "data": {
            "items": items,
            "measure_key": "投诉占比",
            "_meta": {"total_complaints": total, "date_range": [s_d.isoformat(), e_d.isoformat()]},
        },
    }
