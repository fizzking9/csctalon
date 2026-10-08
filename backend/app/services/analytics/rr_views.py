"""RR 预置视图 handlers: _view_* 系列 + HANDLERS 注册表(analysis_engine 手动分派用)。"""

import datetime as _dt
from datetime import datetime, timedelta
from typing import Callable as _Callable

from sqlalchemy import and_, case, func, literal, select, text, union
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    SYS_OP_BLACKLIST_RR,
    _clean_col_expr,
    _complaint_root_case,
    _complaint_subitem_case,
    _resolve_windows,
    _ticket_category_case,
)
from app.services.analytics.refund import _empty_kpi, refund_recovery_analysis

_PARAMS_T = dict | None

_RET_T = dict

def _rr_parse_params(params: _PARAMS_T) -> tuple[str | None, str | None]:
    """从视图执行参数解析 start/end (兼容 AnalysisView 约定)"""
    if not params:
        return None, None
    return params.get("start") or params.get("date_start"), params.get("end") or params.get("date_end")

def _view_rr_kpi(db: Session, params: _PARAMS_T) -> _RET_T:
    """退款挽回 KPI: 核心4指标 + 费占比 (table 格式,供 ViewItemCard 展示)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    k = data.get("kpi") or _empty_kpi()
    # 行=指标, 列=名称/数值/说明
    rows = [
        {"指标": "总接待SN (分母)", "数值": int(k.get("total_sn") or 0), "单位": "个",
         "说明": "退款专员接待 + 真退款类型 (4类) 的去重SN"},
        {"指标": "挽留成功SN", "数值": int(k.get("success_sn") or 0), "单位": "个",
         "说明": "分母SN 中 最终未进入退款工单/退款单 (被拦截)"},
        {"指标": "挽留失败SN", "数值": int(k.get("failed_sn") or 0), "单位": "个",
         "说明": "分母SN 中 T+7 窗口内 最终进入了退款流程"},
        {"指标": "挽回成功率", "数值": f"{round(float(k.get('success_rate') or 0), 1)}%", "单位": "%",
         "说明": "成功SN / 分母SN"},
        {"指标": "拦截费占比", "数值": f"{round(float(k.get('fee_ratio') or 0), 1)}%", "单位": "%",
         "说明": "成功单量/(成功单量+实际退款单量) 参考目标 37%+"},
        {"指标": "实际退款金额", "数值": round(float(k.get("actual_refund_amount") or 0), 2), "单位": "元",
         "说明": "失败金额 + 退充金额"},
    ]
    return {
        "chart_type": "table",
        "data": {
            "columns": [
                {"field": "指标", "title": "指标"},
                {"field": "数值", "title": "数值"},
                {"field": "单位", "title": "单位"},
                {"field": "说明", "title": "口径说明"},
            ],
            "rows": rows,
            "meta": {"_source": "refund_recovery", "date": data.get("date"), "date_end": data.get("date_end")},
        },
    }

def _view_rr_daily_trend(db: Session, params: _PARAMS_T) -> _RET_T:
    """近30天 挽回成功率 (线) + 接待量 (柱) """
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    tr = data.get("daily_trend") or []
    x_axis = [r["date"][5:] for r in tr]  # MM-DD
    series = [
        {"name": "总接待SN", "type": "bar", "data": [r.get("total_sn") or 0 for r in tr],
         "itemStyle": {"color": "#93c5fd"}, "barMaxWidth": 14},
        {"name": "挽回成功SN", "type": "bar", "stack": None, "data": [r.get("success_sn") or 0 for r in tr],
         "itemStyle": {"color": "#4ade80"}, "barMaxWidth": 14},
        {"name": "挽回失败SN", "type": "bar", "stack": None, "data": [r.get("failed_sn") or 0 for r in tr],
         "itemStyle": {"color": "#f87171"}, "barMaxWidth": 14},
        {"name": "挽回成功率", "type": "line", "yAxisIndex": 1, "smooth": True, "symbolSize": 6,
         "data": [r.get("success_rate") or 0 for r in tr],
         "itemStyle": {"color": "#059669"}, "lineStyle": {"width": 3, "color": "#059669"}},
    ]
    return {
        "chart_type": "line",
        "data": {
            "title": "退款挽回 按日趋势 (近30天)",
            "x_axis": x_axis,
            "series": series,
            "legend": {"data": [s["name"] for s in series]},
            "yAxis": [
                {"type": "value", "name": "SN个数"},
                {"type": "value", "name": "成功率%", "min": 0, "max": 100, "axisLabel": {"formatter": "{value}%"}},
            ],
        },
    }

def _view_rr_hourly_today(db: Session, params: _PARAMS_T) -> _RET_T:
    """今日 按小时 接待SN (柱) + 成功率(线)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    hl = data.get("hourly_today") or []
    x_axis = [r["hour"] for r in hl]
    total = [r.get("total_sn") or 0 for r in hl]
    succ = [r.get("success_sn") or 0 for r in hl]
    fail = [r.get("failed_sn") or 0 for r in hl]
    rates = []
    for r in hl:
        t = r.get("total_sn") or 0
        rates.append(round((r.get("success_sn") or 0) / t * 100, 1) if t else 0)
    series = [
        {"name": "总接待SN", "type": "bar", "data": total, "itemStyle": {"color": "#bfdbfe"}, "barMaxWidth": 18},
        {"name": "挽留成功SN", "type": "bar", "data": succ, "itemStyle": {"color": "#34d399"}, "barMaxWidth": 18},
        {"name": "挽留失败SN", "type": "bar", "data": fail, "itemStyle": {"color": "#fca5a5"}, "barMaxWidth": 18},
        {"name": "成功率%", "type": "line", "yAxisIndex": 1, "smooth": True, "symbolSize": 7,
         "data": rates, "itemStyle": {"color": "#047857"}, "lineStyle": {"width": 3, "color": "#047857"}},
    ]
    return {
        "chart_type": "bar",
        "data": {
            "title": "今日 按小时 退款挽回趋势 (00:00 ~ 当前)",
            "x_axis": x_axis,
            "series": series,
            "legend": {"data": [s["name"] for s in series]},
            "yAxis": [
                {"type": "value", "name": "SN个数"},
                {"type": "value", "name": "成功率%", "min": 0, "max": 100, "axisLabel": {"formatter": "{value}%"}},
            ],
        },
    }

def _view_rr_operator_rank(db: Session, params: _PARAMS_T) -> _RET_T:
    """退款挽回 按人员明细排名 (表格)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    sd = data.get("staff_detail") or []
    rows = []
    for i, r in enumerate(sd):
        rate = r.get("success_rate")
        rate_txt = "—" if rate is None else (f"{round(float(rate), 1)}%")
        rows.append({
            "排名": i + 1,
            "姓名": r.get("name") or r.get("operator") or "-",
            "组别": r.get("group") or "未分组",
            "岗位": r.get("position") or "-",
            "接待SN": int(r.get("total_sn") or 0),
            "成功SN": int(r.get("success_sn") or 0),
            "失败SN": int(r.get("failed_sn") or 0),
            "成功率": rate_txt,
            "退款操作SN": int(r.get("refund_op_cnt") or 0),
            "退款操作金额": round(float(r.get("refund_op_amount") or 0), 2),
        })
    cols = [
        {"field": "排名", "title": "排名", "width": 60},
        {"field": "姓名", "title": "姓名", "width": 120},
        {"field": "组别", "title": "组别", "width": 120},
        {"field": "岗位", "title": "岗位", "width": 100},
        {"field": "接待SN", "title": "接待SN(分母)"},
        {"field": "成功SN", "title": "成功SN"},
        {"field": "失败SN", "title": "失败SN"},
        {"field": "成功率", "title": "成功率"},
        {"field": "退款操作SN", "title": "退款操作SN"},
        {"field": "退款操作金额", "title": "退款操作金额(元)"},
    ]
    return {
        "chart_type": "table",
        "data": {"columns": cols, "rows": rows,
                 "meta": {"_source": "refund_recovery_staff"}},
    }

def _view_rr_reason_dist(db: Session, params: _PARAMS_T) -> _RET_T:
    """挽回失败SN 原因分布 (柱+占比)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    rd = data.get("reason_dist") or []
    x_axis = [r.get("reason") or "未知" for r in rd]
    pal = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#6366f1", "#ec4899", "#0ea5e9"]
    bars_data = []
    for idx in range(len(rd)):
        bars_data.append({
            "value": int(rd[idx].get("order_cnt") or 0),
            "itemStyle": {"color": pal[idx % len(pal)], "borderRadius": [4, 4, 0, 0]},
        })
    series = [
        {"name": "失败SN数", "type": "bar", "data": bars_data,
         "barMaxWidth": 36,
         "label": {"show": True, "position": "top"}},
        {"name": "占比%", "type": "line", "yAxisIndex": 1, "smooth": True, "symbolSize": 7,
         "data": [round(float(r.get("ratio") or 0), 1) for r in rd],
         "itemStyle": {"color": "#7c3aed"}, "lineStyle": {"width": 3, "color": "#7c3aed"}},
    ]
    return {
        "chart_type": "bar",
        "data": {
            "title": "挽回失败 SN 原因分布 (归一化 7 大类)",
            "x_axis": x_axis,
            "series": series,
            "legend": {"data": ["失败SN数", "占比%"]},
            "yAxis": [
                {"type": "value", "name": "SN数"},
                {"type": "value", "name": "占比%", "min": 0, "max": 100, "axisLabel": {"formatter": "{value}%"}},
            ],
        },
    }



# ==============================================================
# 2026-08-12 新增 4 个 HANDLER:
#   原因三分布(退款意愿/挽回成功) + 审核人/原因TOP 横向双柱
# ==============================================================
def _view_rr_reason_wish(db: Session, params: _PARAMS_T) -> _RET_T:
    """退款意愿 原因分布 (全分母SN, 柱+占比%双Y轴)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    rd = data.get("wish_reason_dist") or []
    return _build_reason_bar_data(rd, "退款意愿 原因分布 (全接待SN)", "SN数")

def _view_rr_reason_saved(db: Session, params: _PARAMS_T) -> _RET_T:
    """挽回成功 原因分布 (成功SN, 柱+占比%双Y轴)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    rd = data.get("success_reason_dist") or []
    return _build_reason_bar_data(rd, "挽回成功 SN 原因分布", "SN数")

def _build_reason_bar_data(rd: list[dict], title: str, y1_name: str) -> dict:
    """通用: 原因分布 → 柱(数量)+线(占比%) 双Y轴图表数据"""
    x_axis = [r.get("reason") or "未知" for r in rd]
    pal = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#6366f1", "#ec4899", "#0ea5e9"]
    bars_data = []
    for idx in range(len(rd)):
        bars_data.append({
            "value": int(rd[idx].get("order_cnt") or 0),
            "itemStyle": {"color": pal[idx % len(pal)], "borderRadius": [4, 4, 0, 0]},
        })
    series = [
        {"name": y1_name, "type": "bar", "data": bars_data, "barMaxWidth": 36,
         "label": {"show": True, "position": "top"}},
        {"name": "占比%", "type": "line", "yAxisIndex": 1, "smooth": True, "symbolSize": 7,
         "data": [round(float(r.get("ratio") or 0), 1) for r in rd],
         "itemStyle": {"color": "#7c3aed"}, "lineStyle": {"width": 3, "color": "#7c3aed"}},
    ]
    return {
        "chart_type": "bar",
        "data": {
            "title": title,
            "x_axis": x_axis,
            "series": series,
            "legend": {"data": [y1_name, "占比%"]},
            "yAxis": [
                {"type": "value", "name": y1_name},
                {"type": "value", "name": "占比%", "min": 0, "max": 100, "axisLabel": {"formatter": "{value}%"}},
            ],
        },
    }

def _view_refund_operator_hbar(db: Session, params: _PARAMS_T) -> _RET_T:
    """退费审核人 金额+单数 (横向双柱, TOP10)"""
    s, e = _rr_parse_params(params)
    ts, te, _, _, _, _ = _resolve_windows(None, s, e)
    rows = db.execute(text(
        """
        SELECT COALESCE(NULLIF(TRIM(r.auditor),''), '未知') AS auditor,
               SUM(COALESCE(r.actual_amount, r.refund_amount, 0)) AS amount,
               COUNT(*) AS cnt
        FROM refunds r
        WHERE r.created_at_raw IS NOT NULL
          AND r.created_at_raw >= :ts AND r.created_at_raw <= :te
          AND r.refund_method = '线上'
          AND r.status = '已退款'
          AND r.auditor IS NOT NULL
          AND NOT (r.auditor = ANY(:sys_bl))
        GROUP BY auditor
        ORDER BY amount DESC
        LIMIT 10
        """
    ), {"ts": ts, "te": te, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall()
    x_axis = [r.auditor for r in rows]
    amounts = [round(float(r.amount or 0), 2) for r in rows]
    counts = [int(r.cnt or 0) for r in rows]
    series = [
        {"name": "退款金额", "type": "bar", "yAxisIndex": 0, "data": amounts, "barMaxWidth": 18,
         "itemStyle": {"color": "#60a5fa", "borderRadius": [0, 4, 4, 0]},
         "label": {"show": True, "position": "right", "formatter": "¥{c}"}},
        {"name": "退款单数", "type": "bar", "yAxisIndex": 1, "data": counts, "barMaxWidth": 18,
         "itemStyle": {"color": "#fbbf24", "borderRadius": [0, 4, 4, 0]},
         "label": {"show": True, "position": "right", "formatter": "{c}单"}},
    ]
    return {
        "chart_type": "bar",
        "data": {
            "orientation": "horizontal",
            "title": "退费审核人 金额+单数 (TOP10)",
            "x_axis": x_axis,
            "series": series,
            "legend": {"data": ["退款金额", "退款单数"]},
            "yAxis": [
                {"type": "value", "name": "金额(元)", "axisLabel": {"formatter": "{value}"}},
                {"type": "value", "name": "单数", "axisLabel": {"formatter": "{value}"}},
            ],
        },
    }

def _view_refund_reason_top_hbar(db: Session, params: _PARAMS_T) -> _RET_T:
    """退费原因TOP10 金额+单数 (横向双柱)"""
    s, e = _rr_parse_params(params)
    ts, te, _, _, _, _ = _resolve_windows(None, s, e)
    rows = db.execute(text(
        """
        SELECT st.service_item AS reason,
               SUM(COALESCE(st.refund_amount, 0)) AS amount,
               COUNT(*) AS cnt
        FROM service_tickets st
        WHERE st.register_time IS NOT NULL
          AND st.register_time >= :ts AND st.register_time <= :te
          AND st.service_category = '退款'
          AND st.service_item IS NOT NULL
          AND NULLIF(TRIM(st.service_item),'') IS NOT NULL
        GROUP BY st.service_item
        ORDER BY amount DESC
        LIMIT 10
        """
    ), {"ts": ts, "te": te}).fetchall()
    x_axis = [r.reason for r in rows]
    amounts = [round(float(r.amount or 0), 2) for r in rows]
    counts = [int(r.cnt or 0) for r in rows]
    series = [
        {"name": "退款金额", "type": "bar", "yAxisIndex": 0, "data": amounts, "barMaxWidth": 18,
         "itemStyle": {"color": "#60a5fa", "borderRadius": [0, 4, 4, 0]},
         "label": {"show": True, "position": "right", "formatter": "¥{c}"}},
        {"name": "退款单数", "type": "bar", "yAxisIndex": 1, "data": counts, "barMaxWidth": 18,
         "itemStyle": {"color": "#fbbf24", "borderRadius": [0, 4, 4, 0]},
         "label": {"show": True, "position": "right", "formatter": "{c}单"}},
    ]
    return {
        "chart_type": "bar",
        "data": {
            "orientation": "horizontal",
            "title": "退费原因TOP10 金额+单数",
            "x_axis": x_axis,
            "series": series,
            "legend": {"data": ["退款金额", "退款单数"]},
            "yAxis": [
                {"type": "value", "name": "金额(元)"},
                {"type": "value", "name": "单数"},
            ],
        },
    }



# ==============================================================
# 服务请求管理 / 今日看板专项 6 个 AnalysisView HANDLERS
# (对应 P1-T3.1, P1-T3.2, P2-T6.1~T6.4 6 个待实现视图)
# 所有函数签名: (Session, params:{start/end/date}) -> dict[chart_type, data]
# ==============================================================
def _sh_parse_params(params: _PARAMS_T) -> tuple[_dt.datetime, _dt.datetime]:
    """解析视图参数 → 返回北京时间当日窗口 (ts naive-UTC-start, te naive-UTC-end inclusive)

    优先级:
      - params.date (单日模式): 该自然日 00:00~23:59:59.999999 北京时间
      - params.start / params.end: 直接用 (字符串 YYYY-MM-DD 或 datetime)
      - 缺省: 今天自然日 北京时间
    """
    import datetime as __dt
    from zoneinfo import ZoneInfo as _ZI
    _TZ = _ZI("Asia/Shanghai")

    def _from_iso(s: str) -> __dt.date | None:
        try:
            # 先尝试 YYYY-MM-DD
            return __dt.date.fromisoformat(s[:10])
        except Exception:
            return None
    # 1) date 单日模式
    if params and params.get("date"):
        d = _from_iso(str(params["date"])) or __dt.datetime.now(_TZ).date()
    elif params and (params.get("start") or params.get("end")):
        d = _from_iso(str(params.get("start"))) if params.get("start") else __dt.datetime.now(_TZ).date()
    else:
        d = __dt.datetime.now(_TZ).date()
    s_local = __dt.datetime.combine(d, __dt.time(0, 0, 0), tzinfo=_TZ)
    e_local = __dt.datetime.combine(d, __dt.time(23, 59, 59, 999999), tzinfo=_TZ)
    return (
        s_local.astimezone(__dt.timezone.utc).replace(tzinfo=None),
        e_local.astimezone(__dt.timezone.utc).replace(tzinfo=None),
    )

def _view_ticket_work_top20(db: Session, params: _PARAMS_T) -> _RET_T:
    """P1-T3.1: 工单工作内容 Top20 (服务类别 × 服务项)

    列: 服务类别 / 服务项 / 近7天工单量 / 同比±% / 在途积压 / 平均处理时长min / 涉及SN去重 / 涉及手机号去重
    """
    ts, te = _sh_parse_params(params)
    # 近7天窗口
    ts_7 = te - timedelta(days=7) + timedelta(microseconds=1)
    # 同比7天 (前7-14天)
    ys_7 = ts_7 - timedelta(days=7)
    ye_7 = ts_7 - timedelta(microseconds=1)

    # Base CTE: 服务类别归一化 + 手机号清洗
    cat = _ticket_category_case()  # 归一化后的 service_category
    phone_clean = _clean_col_expr(ServiceTicket.phone)

    # 近7天聚合
    base_sel = (
        select(
            cat.label("cat_norm"),
            func.coalesce(ServiceTicket.service_item, literal("未标注")).label("item_norm"),
            func.count().label("cnt_cur"),
            func.count(func.distinct(func.nullif(func.trim(ServiceTicket.device_sn), ""))).label("sn_cnt"),
            func.count(func.distinct(phone_clean)).label("phone_cnt"),
            func.coalesce(func.avg(case(
                (and_(ServiceTicket.handle_duration is not None, ServiceTicket.handle_duration > 0),
                 ServiceTicket.handle_duration / 60.0),
                else_=func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) / 60.0
            )), 0.0).label("avg_dur_min"),
            func.sum(case(
                (ServiceTicket.status.notin_(["已完成", "无需处理", "已取消"]), 1),
                else_=0
            )).label("open_cnt"),
        )
        .select_from(ServiceTicket)
        .where(ServiceTicket.register_time.isnot(None),
               ServiceTicket.register_time >= ts_7, ServiceTicket.register_time <= te)
        .group_by("cat_norm", "item_norm")
        .cte("cur_agg")
    )
    # 同比7天聚合 (键相同的才会JOIN)
    yoy_sel = (
        select(
            cat.label("y_cat"),
            func.coalesce(ServiceTicket.service_item, literal("未标注")).label("y_item"),
            func.count().label("cnt_yoy")
        )
        .select_from(ServiceTicket)
        .where(ServiceTicket.register_time.isnot(None),
               ServiceTicket.register_time >= ys_7, ServiceTicket.register_time <= ye_7)
        .group_by("y_cat", "y_item")
        .cte("yoy_agg")
    )
    stmt = (
        select(
            base_sel.c.cat_norm, base_sel.c.item_norm,
            base_sel.c.cnt_cur, yoy_sel.c.cnt_yoy,
            base_sel.c.open_cnt, base_sel.c.avg_dur_min, base_sel.c.sn_cnt, base_sel.c.phone_cnt,
        )
        .select_from(base_sel)
        .outerjoin(yoy_sel, and_(yoy_sel.c.y_cat == base_sel.c.cat_norm, yoy_sel.c.y_item == base_sel.c.item_norm))
        .order_by(base_sel.c.cnt_cur.desc())
        .limit(20)
    )
    rows_q = db.execute(stmt).all()
    rows = []
    for r in rows_q:
        cur = int(r.cnt_cur or 0)
        yoy = int(r.cnt_yoy or 0)
        diff = cur - yoy
        ratio = round(diff / yoy * 100, 1) if yoy else None
        rows.append({
            "服务类别": (r.cat_norm or "未分类") if r.cat_norm != "投诉" else "投诉",
            "服务项": (r.item_norm or "未标注")[:30],
            "近7天工单量": cur,
            "同比工单量": yoy,
            "同比Δ": (f"+{diff}" if diff >= 0 else f"{diff}"),
            "同比%": (f"{ratio:+.1f}%" if ratio is not None else "新"),
            "在途积压": int(r.open_cnt or 0),
            "平均处理时长(min)": round(float(r.avg_dur_min or 0), 1),
            "涉及SN(去重)": int(r.sn_cnt or 0),
            "涉及手机号(去重)": int(r.phone_cnt or 0),
        })
    cols = [
        "服务类别", "服务项", "近7天工单量", "同比工单量", "同比Δ", "同比%",
        "在途积压", "平均处理时长(min)", "涉及SN(去重)", "涉及手机号(去重)",
    ]
    return {
        "chart_type": "table",
        "data": {
            "title": "工单工作内容 Top20 (按 服务类别×服务项, 近7天工单量排序)",
            "columns": cols,
            "rows": rows,
        },
    }

def _view_complaint_root_heatmap(db: Session, params: _PARAMS_T) -> _RET_T:
    """P1-T3.2: 投诉子类 Top10 × 六大类根因 热力矩阵

    口径与投诉根因柱图 (today_complaint_root_cause) 保持一致:
      - 投诉工单 (三表 UNION) JOIN cs_record (SN ± 24h 窗口关联)
      - 根因: 网络质量类 / 套餐争议类 / 退款协商失败类 / 降成争议类 / 其它注销发票退换 / 未关联SN
    """
    ts, te = _sh_parse_params(params)
    # 三表 UNION ALL: sn, phone, complaint_sub, register_time
    # SQLAlchemy 2.0: union() 的元素必须是 SELECT, 不能是 CTE（此前误加 .cte() 导致 ArgumentError）
    union_subq = union(
        select(
            ServiceTicket.device_sn.label("sn"),
            ServiceTicket.phone.label("phone"),
            _complaint_subitem_case().label("sub"),
            ServiceTicket.register_time.label("rt"),
        ).where(
            ServiceTicket.register_time >= ts - timedelta(days=1),
            ServiceTicket.register_time <= te + timedelta(days=1),
            _ticket_category_case() == "投诉",
        ),
        select(
            ExternalComplaint.device_sn.label("sn"),
            ExternalComplaint.complainant_phone.label("phone"),
            func.coalesce(ExternalComplaint.complaint_category, literal("外部投诉")).label("sub"),
            ExternalComplaint.complaint_date.label("rt"),
        ).where(
            ExternalComplaint.complaint_date >= ts - timedelta(days=1),
            ExternalComplaint.complaint_date <= te + timedelta(days=1),
        ),
        select(
            CardComplaint.device_sn.label("sn"),
            CardComplaint.complainant_phone.label("phone"),
            func.coalesce(CardComplaint.problem_category, literal("卡商投诉")).label("sub"),
            CardComplaint.complaint_date.label("rt"),
        ).where(
            CardComplaint.complaint_date >= ts - timedelta(days=1),
            CardComplaint.complaint_date <= te + timedelta(days=1),
        ),
    )
    # 再建一级 CTE 便于 JOIN
    u = select(union_subq.c.sn, union_subq.c.phone, union_subq.c.sub, union_subq.c.rt).cte("u")

    # ±24h 窗口 JOIN cs_record, 拿根因分类
    from app.models.cs_record import CsRecord as _CsR
    stmt = (
        select(
            u.c.sub.label("投诉子类"),
            _complaint_root_case(_CsR.problem_category, _CsR.problem_type).label("根因"),
            func.count().label("c"),
        )
        .select_from(u)
        .outerjoin(
            _CsR,
            and_(
                func.nullif(func.trim(_CsR.device_sn), "").isnot(None),
                u.c.sn == func.nullif(func.trim(_CsR.device_sn), ""),
                _CsR.operation_time >= u.c.rt - text("INTERVAL '24 hours'"),
                _CsR.operation_time <= u.c.rt + text("INTERVAL '24 hours'"),
            ),
        )
        # 过滤窗口内登记的 (避免多 JOIN 后多算, 我们按 rt 再过滤)
        .where(u.c.rt >= ts, u.c.rt <= te)
        .group_by("投诉子类", "根因")
        .order_by(func.count().desc())
    )
    rows_q = db.execute(stmt).all()
    # 先拿到 Top10 投诉子类 + 6 根因桶
    sub_totals: dict[str, int] = {}
    matrix: dict[tuple[str, str], int] = {}
    ROOT_BUCKETS = [
        "网络质量类", "套餐争议类", "退款协商失败类", "降成争议类",
        "其它(注销/发票/退换)", "未关联SN",
    ]
    for r in rows_q:
        sub = (r["投诉子类"] or "未标注")[:14]
        rc = r["根因"] or "未关联SN"
        # 没关联上的 → 未关联SN 桶
        if rc not in ROOT_BUCKETS:
            # _complaint_root_case 已经归一化, 没命中的 else 值按规范归入最后兜底桶
            rc = "其它(注销/发票/退换)" if rc and "未关联" not in rc else "未关联SN"
        c = int(r.c or 0)
        sub_totals[sub] = sub_totals.get(sub, 0) + c
        matrix[(sub, rc)] = matrix.get((sub, rc), 0) + c
    # Top10 投诉子类
    top10_subs = [s for s, _ in sorted(sub_totals.items(), key=lambda kv: -kv[1])[:10]]
    if not top10_subs:
        return {"chart_type": "table", "data": {"columns": ["投诉子类"] + ROOT_BUCKETS + ["合计"], "rows": []}}
    # 行: 投诉子类 Top10; 列: 6 根因桶 + 合计; 值=该 cell 投诉量; 额外加一列占比% (cell / 该子类总量)
    rows = []
    for sub in top10_subs:
        total = sub_totals[sub]
        row = {"投诉子类": f"{sub}  (n={total})", "合计": total}
        for rc in ROOT_BUCKETS:
            cell = matrix.get((sub, rc), 0)
            pct = round(cell / total * 100, 1) if total else 0.0
            row[rc] = cell
            row[f"{rc}%"] = pct
        rows.append(row)
    cols = ["投诉子类"] + [x for pair in zip(ROOT_BUCKETS, [f"{b}%" for b in ROOT_BUCKETS]) for x in pair] + ["合计"]
    return {
        "chart_type": "table",
        "data": {
            "title": "投诉子类 Top10 × 六大类根因 热力矩阵 (SN±24h JOIN cs_record 归并根因)",
            "columns": cols,
            "rows": rows,
            "_matrix_cfg": {
                "top10_subs": top10_subs,
                "root_buckets": ROOT_BUCKETS,
                "matrix": {f"{k[0]}||{k[1]}": v for k, v in matrix.items()},
            },
        },
    }

def _view_today_sn_top(db: Session, params: _PARAMS_T) -> _RET_T:
    """P2-T6.1: 今日 SN TOP 覆盖 (按 接待工单量 排序, Top20)

    列: SN / 今日工单数 / 其中投诉 / 去重手机号 / 首次进线 / 最近进线 / 关联服务项摘要
    """
    ts, te = _sh_parse_params(params)
    stmt = (
        select(
            func.nullif(func.trim(ServiceTicket.device_sn), "").label("sn"),
            func.count().label("ticket_cnt"),
            func.sum(case((_ticket_category_case() == "投诉", 1), else_=0)).label("complaint_cnt"),
            func.count(func.distinct(_clean_col_expr(ServiceTicket.phone))).label("phone_cnt"),
            func.min(ServiceTicket.register_time).label("first_rt"),
            func.max(ServiceTicket.register_time).label("last_rt"),
            func.array_agg(func.distinct(func.coalesce(ServiceTicket.service_item, literal("未标注")))).label("items"),
        )
        .select_from(ServiceTicket)
        .where(
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te,
            func.nullif(func.trim(ServiceTicket.device_sn), "").isnot(None),
        )
        .group_by(text("sn"))
        .order_by(func.count().desc())
        .limit(20)
    )
    rows_q = db.execute(stmt).all()
    rows = []
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Shanghai")
    for r in rows_q:
        def _fmt(t):
            if not t: return ""
            try:
                return (t.replace(tzinfo=_dt.timezone.utc) if t.tzinfo is None else t).astimezone(TZ).strftime("%H:%M")
            except Exception:
                return ""
        items_list = list(r.items or [])[:3]
        rows.append({
            "SN": str(r.sn),
            "今日工单数": int(r.ticket_cnt or 0),
            "其中投诉": int(r.complaint_cnt or 0),
            "去重手机号": int(r.phone_cnt or 0),
            "首次进线": _fmt(r.first_rt),
            "最近进线": _fmt(r.last_rt),
            "服务项摘要": " / ".join([str(x)[:12] for x in items_list]),
        })
    cols = ["SN", "今日工单数", "其中投诉", "去重手机号", "首次进线", "最近进线", "服务项摘要"]
    return {
        "chart_type": "table",
        "data": {"title": "今日 SN TOP 覆盖 Top20 (按 今日工单数 排序)", "columns": cols, "rows": rows},
    }

def _view_today_phone_dup(db: Session, params: _PARAMS_T) -> _RET_T:
    """P2-T6.2: 今日手机号 TOP 重复投诉 (当日同一手机号 ≥ 2 次投诉, 三表 UNION)

    列: 脱敏手机号 / 今日投诉次数 (投诉三表内重复) / 今日总进线次数 (全部工单) / 关联SN列表 / 投诉子类摘要
    """
    ts, te = _sh_parse_params(params)
    # 投诉三表 UNION: 手机号, 投诉子类, SN
    comp_st = select(
        _clean_col_expr(ServiceTicket.phone).label("phone_c"),
        _complaint_subitem_case().label("sub"),
        ServiceTicket.device_sn.label("sn_c"),
        literal("ST").label("src"),
    ).where(
        ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te,
        _ticket_category_case() == "投诉",
    )
    comp_ec = select(
        _clean_col_expr(ExternalComplaint.complainant_phone).label("phone_c"),
        func.coalesce(ExternalComplaint.complaint_category, literal("外部投诉")).label("sub"),
        ExternalComplaint.device_sn.label("sn_c"),
        literal("EC").label("src"),
    ).where(
        ExternalComplaint.complaint_date >= ts, ExternalComplaint.complaint_date <= te,
    )
    comp_cc = select(
        _clean_col_expr(CardComplaint.complainant_phone).label("phone_c"),
        func.coalesce(CardComplaint.problem_category, literal("卡商投诉")).label("sub"),
        CardComplaint.device_sn.label("sn_c"),
        literal("CC").label("src"),
    ).where(
        CardComplaint.complaint_date >= ts, CardComplaint.complaint_date <= te,
    )
    comp_u = union(comp_st, comp_ec, comp_cc).cte("u")
    # 全部工单 (非投诉也统计) 用于总进线次数
    total_st = (
        select(
            _clean_col_expr(ServiceTicket.phone).label("phone_t"),
            func.count().label("total_cnt"),
            func.array_agg(func.distinct(func.nullif(func.trim(ServiceTicket.device_sn), ""))).label("sn_list"),
        )
        .where(ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te)
        .group_by("phone_t")
        .cte("ts_agg")
    )
    # 投诉聚合
    comp_agg = (
        select(
            comp_u.c.phone_c.label("phone"),
            func.count().label("comp_cnt"),
            func.array_agg(func.distinct(comp_u.c.sub)).label("sub_list"),
            func.array_agg(func.distinct(func.nullif(func.trim(comp_u.c.sn_c), ""))).label("sn_list"),
        )
        .where(comp_u.c.phone_c.isnot(None))
        .group_by("phone")
        .having(func.count() >= 2)
        .order_by(func.count().desc())
        .limit(20)
        .cte("cagg")
    )
    stmt = (
        select(
            comp_agg.c.phone, comp_agg.c.comp_cnt, comp_agg.c.sub_list,
            func.coalesce(total_st.c.total_cnt, comp_agg.c.comp_cnt).label("total_cnt"),
            func.coalesce(total_st.c.sn_list, comp_agg.c.sn_list).label("sn_list"),
        )
        .select_from(comp_agg)
        .outerjoin(total_st, total_st.c.phone_t == comp_agg.c.phone)
        .order_by(comp_agg.c.comp_cnt.desc())
    )
    rows_q = db.execute(stmt).all()
    rows = []
    for r in rows_q:
        # 脱敏手机号: 138****1234
        p = str(r.phone or "")
        masked = p[:3] + "****" + p[7:] if len(p) == 11 else p
        subs = list(r.sub_list or [])[:3]
        sns = list({x for x in (r.sn_list or []) if x})[:5]
        rows.append({
            "手机号(脱敏)": masked,
            "今日投诉次数": int(r.comp_cnt or 0),
            "今日总进线次数": int(r.total_cnt or 0),
            "投诉子类摘要": " / ".join([str(s)[:12] for s in subs]),
            "关联SN(去重)": " / ".join(sns),
        })
    cols = ["手机号(脱敏)", "今日投诉次数", "今日总进线次数", "投诉子类摘要", "关联SN(去重)"]
    return {
        "chart_type": "table",
        "data": {"title": "今日手机号 TOP 重复投诉 (当日 ≥ 2 次投诉, Top20)", "columns": cols, "rows": rows},
    }

def _view_today_handler_sn_rank(db: Session, params: _PARAMS_T) -> _RET_T:
    """P2-T6.3: 今日处理人 覆盖 SN 排行 (按 白名单真实完工 + 推算完工时间 落今日 → 去重 SN 数 排序)

    更准确地反映"谁今天真正帮了最多设备的用户解决问题", 而不是纯结单数量。
    """
    ts, te = _sh_parse_params(params)
    SYS_USERS = {"sys", "system", "SYS", "System", "客户", None}

    # Step 1: 近 30 天真实完成样本, handler 平均耗时 (复用 _ticket_effective_finish_time_where 的 Step 1 逻辑)
    hist_since = ts - timedelta(days=30)
    real_done_cond = and_(
        ServiceTicket.status == "已完成",
        ServiceTicket.handler.isnot(None),
        ~ServiceTicket.handler.in_(list(SYS_USERS) + ["客户"]),
        ServiceTicket.finish_time.isnot(None),
        ServiceTicket.register_time.isnot(None),
        ServiceTicket.finish_time >= ServiceTicket.register_time,
        ServiceTicket.finish_time <= ServiceTicket.register_time + text("INTERVAL '12 hours'"),
    )
    h_avg_rows = db.execute(
        select(
            ServiceTicket.handler,
            func.avg(case(
                (and_(ServiceTicket.handle_duration is not None, ServiceTicket.handle_duration > 0),
                 ServiceTicket.handle_duration),
                else_=func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time)
            )).label("avg_dur"),
            func.count().label("n"),
        )
        .where(real_done_cond, ServiceTicket.register_time >= hist_since)
        .group_by(ServiceTicket.handler)
    ).all()
    handler_avg: dict[str, float] = {}
    w_t, w_c = 0.0, 0
    for r in h_avg_rows:
        d = float(r._mapping["avg_dur"] or 0.0)
        n = int(r._mapping["n"] or 0)
        if n > 0 and d > 0:
            handler_avg[str(r.handler).strip()] = d
            w_t += d * n
            w_c += n
    global_avg = w_t / w_c if w_c else (28.0 * 60)

    # Step 2: 拉 status='已完成' + register 合理范围的工单, 算 effective_finish_time
    rows = db.execute(
        select(
            ServiceTicket.register_time, ServiceTicket.finish_time,
            ServiceTicket.handle_duration, ServiceTicket.handler,
            ServiceTicket.device_sn, ServiceTicket.service_category, ServiceTicket.service_item,
        )
        .where(
            ServiceTicket.status == "已完成",
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= hist_since,
            ServiceTicket.register_time <= te + timedelta(hours=13),
        )
    ).all()

    import datetime as __dt
    def _u(t):
        if t is None: return None
        return t.replace(tzinfo=__dt.timezone.utc) if t.tzinfo is None else t.astimezone(__dt.timezone.utc)
    _s, _e = _u(ts), _u(te)

    # {handler: set(sns)} 今日有效完工
    out: dict[str, set[str]] = {}
    ticket_cnt: dict[str, int] = {}
    for r in rows:
        reg_a = _u(r.register_time)
        if not reg_a: continue
        fin_a = _u(r.finish_time)
        is_real = bool(
            fin_a and r.handler and r.handler not in SYS_USERS
            and fin_a >= reg_a and fin_a <= reg_a + timedelta(hours=12)
        )
        if is_real:
            eff = fin_a
        else:
            if isinstance(r.handle_duration, (int, float)) and r.handle_duration > 0:
                eff = reg_a + timedelta(seconds=float(r.handle_duration))
            else:
                avg = handler_avg.get(str(r.handler).strip()) if r.handler else None
                eff = reg_a + timedelta(seconds=avg) if avg else reg_a + timedelta(seconds=global_avg)
        if not (_s <= eff <= _e): continue
        # 计数
        h = str(r.handler or "未分派").strip() or "未分派"
        if h not in out:
            out[h] = set()
            ticket_cnt[h] = 0
        ticket_cnt[h] += 1
        sn = (r.device_sn or "").strip()
        if sn:
            out[h].add(sn)

    # 排序
    rows_out = []
    for h, sns in sorted(out.items(), key=lambda kv: (-len(kv[1]), -ticket_cnt.get(kv[0], 0))):
        rows_out.append({
            "处理人": h,
            "今日覆盖SN数": len(sns),
            "今日真实结单量": ticket_cnt.get(h, 0),
        })
    cols = ["处理人", "今日覆盖SN数", "今日真实结单量"]
    return {
        "chart_type": "table",
        "data": {
            "title": "今日处理人 覆盖 SN 排行 (按 今日真实覆盖去重SN数 排序, 更能体现客服工作覆盖)",
            "columns": cols,
            "rows": rows_out,
        },
    }

def _view_today_cold_tickets(db: Session, params: _PARAMS_T) -> _RET_T:
    """P2-T6.4: 今日在途冷工单清单 (当前仍未结单 + 距登记 >24h + 最后更新人/最后更新时间)

    冷工单定义 (规范一致):
      - status 不属于 ['已完成','无需处理','已取消'] 三桶 (仍在途)
      - 当前时间 - register_time > 24 小时 (属于「压单」)
    按 "距登记时长(小时)" 倒序, 主管一眼看到压单最久的 50 条先处理。
    """
    # 冷工单不依赖"今日窗口",而是"当前所有在途"。
    # 不过 params.date 如果指定了日期, 就按「当日登记的在途工单 >24h」展示 (用于历史回溯)
    ts_utc, te_utc = _sh_parse_params(params)
    datetime.now() if True else _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    # now 转 naive UTC (因为 ServiceTicket.register_time 取出时若 tz-aware, 会兼容比较)
    import datetime as __dt
    now = __dt.datetime.now(__dt.timezone.utc).replace(tzinfo=None)

    cond_in_progress = ServiceTicket.status.notin_(["已完成", "无需处理", "已取消"])
    # 距登记 > 24h
    cold_cond = now - ServiceTicket.register_time >= text("INTERVAL '24 hours'")
    # 如果是"非今日日期"模式 (params.date 不等于今天) → 就限定 register_time 落在 [ts_utc,te_utc] (当日视角)
    __dt.date.today()  # system date (we'll compare to sh today to decide)
    from zoneinfo import ZoneInfo as _ZI
    _TODAY_SH = __dt.datetime.now(_ZI("Asia/Shanghai")).date()
    # 用 te_utc 反推 display 日期
    def _to_sh_date(naive_utc):
        return naive_utc.replace(tzinfo=__dt.timezone.utc).astimezone(_ZI("Asia/Shanghai")).date()
    display_date = _to_sh_date(te_utc)
    scope = and_(ServiceTicket.register_time >= (ts_utc - timedelta(days=14)),
                 ServiceTicket.register_time <= te_utc) if display_date != _TODAY_SH else and_(
                     ServiceTicket.register_time <= te_utc,
                     now - ServiceTicket.register_time <= text("INTERVAL '60 days'"),
                 )

    stmt = (
        select(
            ServiceTicket.ticket_no, ServiceTicket.register_time, ServiceTicket.status,
            ServiceTicket.service_category, ServiceTicket.service_item,
            ServiceTicket.handler, ServiceTicket.dispatcher,
            ServiceTicket.last_dispatch_time, ServiceTicket.last_urge_time,
            ServiceTicket.device_sn, _clean_col_expr(ServiceTicket.phone).label("phone"),
            ServiceTicket.urge_count,
        )
        .where(cond_in_progress, cold_cond, ServiceTicket.register_time.isnot(None), scope)
        .order_by((now - ServiceTicket.register_time).desc())
        .limit(50)
    )
    rows_q = db.execute(stmt).all()
    TZ = _ZI("Asia/Shanghai")
    rows = []
    for r in rows_q:
        rt = r.register_time
        if not rt: continue
        rt_aware = rt.replace(tzinfo=__dt.timezone.utc) if rt.tzinfo is None else rt
        hrs = (now.replace(tzinfo=__dt.timezone.utc) - rt_aware).total_seconds() / 3600.0
        # 最后活动时间: 最后派单时间 / 最后催单时间 / 登记时间 取最大
        times = [rt_aware]
        for t in (r.last_dispatch_time, r.last_urge_time):
            if t is None: continue
            t_aware = t.replace(tzinfo=__dt.timezone.utc) if t.tzinfo is None else t
            times.append(t_aware)
        last_activity = max(times)
        rows.append({
            "工单号": r.ticket_no,
            "距登记(h)": round(hrs, 1),
            "状态": (r.status or "未标注")[:8],
            "服务类别": (r.service_category or "未分类")[:8],
            "服务项": (r.service_item or "未标注")[:18],
            "处理人": r.handler or "—",
            "派发人": r.dispatcher or "—",
            "最后活动": last_activity.astimezone(TZ).strftime("%m-%d %H:%M"),
            "催单次数": int(r.urge_count or 0),
            "SN": (r.device_sn or "")[:16],
            "手机号(脱敏)": (lambda p: p[:3] + "****" + p[7:] if p and len(p) == 11 else (p or ""))(r.phone),
            "登记时间": rt_aware.astimezone(TZ).strftime("%m-%d %H:%M"),
        })
    cols = [
        "工单号", "距登记(h)", "状态", "服务类别", "服务项", "处理人", "派发人",
        "最后活动", "催单次数", "SN", "手机号(脱敏)", "登记时间",
    ]
    return {
        "chart_type": "table",
        "data": {
            "title": "在途冷工单 Top50 (仍未结 + 距登记 > 24h, 按压单时长倒序, 主管优先处理)",
            "columns": cols,
            "rows": rows,
            "_scope": {"display_date": display_date.isoformat(), "now": __dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M")},
        },
    }

HANDLERS: dict[str, _Callable[[Session, _PARAMS_T], _RET_T]] = {
    "preset_refund_recovery_kpi":            _view_rr_kpi,
    "preset_refund_recovery_daily_trend":    _view_rr_daily_trend,
    "preset_refund_recovery_hourly_today":   _view_rr_hourly_today,
    "preset_refund_recovery_operator_rank":  _view_rr_operator_rank,
    "preset_refund_recovery_reason_dist":    _view_rr_reason_dist,
    # ---- 2026-08-12 新增: 原因三分布 + 审核人/原因TOP 横向双柱 ----
    "preset_refund_recovery_reason_wish":    _view_rr_reason_wish,
    "preset_refund_recovery_reason_saved":   _view_rr_reason_saved,
    "preset_refund_operator_v2":             _view_refund_operator_hbar,
    "preset_refund_reason_top":              _view_refund_reason_top_hbar,
    # ---- 今日看板 / 服务请求专项 (本次新增 6 个 handlers) ----
    "preset_ticket_work_content_top20":      _view_ticket_work_top20,
    "preset_complaint_subtype_rootcause_heatmap": _view_complaint_root_heatmap,
    "preset_today_sn_top_coverage":          _view_today_sn_top,
    "preset_today_phone_dup_complaint":      _view_today_phone_dup,
    "preset_today_handler_sn_rank":          _view_today_handler_sn_rank,
    "preset_today_cold_tickets":             _view_today_cold_tickets,
}
