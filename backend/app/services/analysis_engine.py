"""分析引擎执行器 - 哑执行器,根据视图配置生成 SQL 并执行

核心设计:
1. 引擎不理解业务含义,只看视图配置(segments JSONB)生成 SQLAlchemy 查询
2. DataSourceField.semantic_role 仅作 UI 提示,字段实际用途由配置决定
3. 跨表累加:分表聚合后相加(aggregate separately then add),不 JOIN,不行级对齐
4. 分类规则:查询时转 CASE WHEN 注入 SQL,不污染业务表
5. 所有字段名通过 getattr(model, name) 获取(防注入),值参数化传递
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.analysis_view import AnalysisView, ClassificationRule
from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.data_source import DataSource
from app.models.device_combo import DeviceCombo
from app.models.external_complaint import ExternalComplaint
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal

# 模型注册表:table_name → model class(用于根据 DataSource.table_name 反射获取列)
_MODEL_REGISTRY: dict[str, type] = {
    Refund.__tablename__: Refund,
    WalletWithdrawal.__tablename__: WalletWithdrawal,
    ServiceTicket.__tablename__: ServiceTicket,
    DeviceCombo.__tablename__: DeviceCombo,
    MerchantRefund.__tablename__: MerchantRefund,
    CsRecord.__tablename__: CsRecord,
    ExternalComplaint.__tablename__: ExternalComplaint,
    CardComplaint.__tablename__: CardComplaint,
}


# ---------- 主入口 ----------


def execute_view(db: Session, view: AnalysisView | dict, params: dict | None = None) -> dict:
    """执行分析视图,返回图表数据

    Args:
        db: 数据库会话
        view: AnalysisView 实例(已存视图)或配置字典(inline 预览)
        params: {start?: str, end?: str, filters_override?: list[dict]}

    Returns:
        {chart_type, data: {x_axis?, series?, items?, columns?, rows?}}
    """
    params = params or {}
    # 统一为字典配置
    if isinstance(view, AnalysisView):
        config = {
            "chart_type": view.chart_type,
            "segments": view.segments,
            "merge_strategy": view.merge_strategy,
            "classification_rule_ids": view.classification_rule_ids or [],
            "time_range_default": view.time_range_default,
            "ratios": view.ratios or [],
        }
    else:
        config = view

    rule_ids = config.get("classification_rule_ids") or []
    rules = _load_rules(db, rule_ids)
    time_range = _resolve_time_range(config, params)

    # 分表聚合:每个 segment 独立查询
    segment_results = []
    for seg in config["segments"]:
        rows = _execute_segment(db, seg, rules, time_range, params)
        segment_results.append({"segment": seg, "rows": rows})

    # 合并多源(分表聚合后相加)
    merged = _merge_segment_results(segment_results, config.get("merge_strategy", "add"))

    # 比率度量:基于已合并的分子/分母 measure 计算(合并后计算,跨表累加正确)
    ratios = config.get("ratios") or []
    if ratios:
        for row in merged:
            for r in ratios:
                num = float(row.get(r["numerator"], 0) or 0)
                den = float(row.get(r["denominator"], 0) or 0)
                val = (num / den) if den else None
                if val is not None and r.get("as_percent"):
                    val = round(val * 100, 2)
                elif val is not None:
                    val = round(val, 4)
                row[r["alias"]] = val

    # 转换为图表数据
    return _to_chart_data(merged, config["chart_type"], config["segments"], ratios)


# ---------- 单 segment 执行 ----------


def _execute_segment(
    db: Session, seg: dict, rules: list[ClassificationRule], time_range: dict | None, params: dict
) -> list[dict]:
    """构建并执行单 segment 的 SQLAlchemy 查询"""
    source_code = seg["source_code"]
    ds = db.scalar(select(DataSource).where(DataSource.code == source_code))
    if not ds:
        raise ValueError(f"数据源未注册: {source_code}")

    model = _MODEL_REGISTRY.get(ds.table_name)
    if not model:
        raise ValueError(f"模型未注册: {ds.table_name}")

    select_cols = []
    group_cols = []
    dim_keys = []  # 维度 alias 列表(用于结果映射)

    # 1. 分类规则注入(如果配置了 classification_target)
    classification_alias = None
    if seg.get("classification_target") and rules:
        case_expr = _build_classification_case(model, seg["classification_target"], rules)
        classification_alias = f"{seg['classification_target']}_class"
        labeled = case_expr.label(classification_alias)
        select_cols.append(labeled)
        group_cols.append(labeled)
        dim_keys.append(classification_alias)

    # 2. 维度列
    for dim in seg.get("dimensions", []):
        field_name = dim["field"]
        col = _get_column(model, field_name)
        role = dim.get("role")
        time_grain = dim.get("time_grain")
        if role == "time" and time_grain and time_grain != "none":
            col = _apply_time_grain(col, time_grain)
        alias = dim.get("alias") or field_name
        labeled = col.label(alias)
        select_cols.append(labeled)
        group_cols.append(labeled)
        dim_keys.append(alias)

    # 3. 度量列
    measure_keys = []
    for m in seg.get("measures", []):
        field_name = m["field"]
        agg = m.get("agg", "sum")
        # 构建聚合表达式(先聚合 → 可选 FILTER → coalesce 兜底 0)
        if field_name == "*":
            agg_expr = func.count()
        else:
            col0 = _get_column(model, field_name)
            agg_expr = {"sum": func.sum(col0), "count": func.count(col0), "avg": func.avg(col0)}.get(agg, func.sum(col0))
        # measure 级 filter(PostgreSQL FILTER 子句):如 count(*) FILTER (WHERE user_accepted='已接受')
        m_filters = m.get("filter")
        if m_filters:
            fconds = _build_filters(model, m_filters)
            if fconds:
                agg_expr = agg_expr.filter(*fconds)
        col = func.coalesce(agg_expr, 0)
        alias = m.get("alias") or (field_name if field_name != "*" else "count")
        select_cols.append(col.label(alias))
        measure_keys.append(alias)

    # 4. WHERE 子句
    conditions = []
    conditions.extend(_build_filters(model, seg.get("filters", [])))
    if time_range:
        time_filter = _build_time_filter(model, seg, time_range)
        if time_filter is not None:
            conditions.append(time_filter)
    # params 中的 override filters 叠加
    conditions.extend(_build_filters(model, params.get("filters_override", [])))

    # 5. 构建语句
    stmt = select(*select_cols)
    if conditions:
        stmt = stmt.where(*conditions)
    if group_cols:
        stmt = stmt.group_by(*group_cols)
    # 默认按第一个维度排序
    if group_cols:
        stmt = stmt.order_by(group_cols[0])
    if seg.get("limit"):
        stmt = stmt.limit(int(seg["limit"]))

    rows = db.execute(stmt).all()
    # 转为字典列表,数值转 float(便于前端处理与合并相加)
    result = []
    for r in rows:
        mapping = r._mapping
        row_dict = {}
        for k in dim_keys + measure_keys:
            v = mapping.get(k)
            if k in measure_keys and v is not None:
                row_dict[k] = float(v)
            elif k in measure_keys:
                row_dict[k] = 0.0
            else:
                row_dict[k] = v
        result.append(row_dict)
    return result


# ---------- 分类规则转 CASE WHEN ----------


def _build_classification_case(model, target_field: str, rules: list[ClassificationRule]):
    """把 ClassificationRule.rules 转成 SQLAlchemy case() 表达式

    多个规则按顺序匹配,第一个匹配的 label 生效(类似 SQL CASE WHEN 顺序)
    """
    whens = []
    for rule_obj in rules:
        if not rule_obj.is_active:
            continue
        for rule in rule_obj.rules:
            conditions = []
            for cond in rule.get("conditions", []):
                col = _get_column(model, cond["field"])
                conditions.append(_build_op_condition(col, cond))
            if not conditions:
                continue
            match_all = rule.get("match") == "all"
            combiner = and_ if match_all else or_
            whens.append((combiner(*conditions), rule["label"]))

    default_label = rules[0].default_label if rules else "其他"
    if not whens:
        # 无有效规则,返回默认标签
        return case(("其他",), else_=default_label)
    return case(*whens, else_=default_label)


# ---------- 多源合并(分表聚合后相加) ----------


def _merge_segment_results(segment_results: list[dict], strategy: str) -> list[dict]:
    """分表聚合后相加:按维度键对齐,measure 相加

    多 segment 时,各 segment 的维度 alias 必须一致才能对齐(如都用"日期")
    measure 按相同 alias 相加
    """
    if len(segment_results) == 1:
        return segment_results[0]["rows"]

    if strategy != "add":
        # 其他策略暂不支持,默认 add
        pass

    # 提取维度 alias(从第一个 segment 推断)
    first_seg = segment_results[0]["segment"]
    dim_keys = [d.get("alias") or d["field"] for d in first_seg.get("dimensions", [])]

    # 收集所有 measure alias(各 segment 可能不同,合并时统一累加)
    all_measure_keys: list[str] = []
    for sr in segment_results:
        for m in sr["segment"].get("measures", []):
            mk = m.get("alias") or (m["field"] if m["field"] != "*" else "count")
            if mk not in all_measure_keys:
                all_measure_keys.append(mk)

    merged: dict[tuple, dict] = {}
    for sr in segment_results:
        seg_measure_keys = [
            m.get("alias") or (m["field"] if m["field"] != "*" else "count")
            for m in sr["segment"].get("measures", [])
        ]
        for row in sr["rows"]:
            # 维度键(用第一个 segment 的 dim_keys 提取)
            key = tuple(row.get(k) for k in dim_keys)
            if key not in merged:
                merged[key] = {k: row.get(k) for k in dim_keys}
                # 初始化所有 measure 为 0
                for mk in all_measure_keys:
                    merged[key][mk] = 0.0
            # 累加该 segment 的 measure 到对应 alias
            for mk in seg_measure_keys:
                if mk in all_measure_keys:
                    merged[key][mk] = float(merged[key].get(mk, 0)) + float(row.get(mk, 0) or 0)

    # 按维度排序
    result = list(merged.values())
    if dim_keys:
        result.sort(key=lambda r: (r.get(dim_keys[0]) is None, r.get(dim_keys[0])))
    return result


# ---------- 图表数据转换 ----------


def _to_chart_data(merged: list[dict], chart_type: str, segments: list[dict], ratios: list[dict] | None = None) -> dict:
    """根据 chart_type 组装前端可用的图表数据

    ratios 中的 alias 已在 execute_view 阶段写入 merged 行,这里只负责让它们出现在
    表格列 / 图表 series 中(默认追加在度量之后)。
    """
    dim_keys = _extract_dim_keys_from_segments(segments)
    measure_keys = _extract_measure_keys(segments)
    # 比率别名追加为输出列(仅保留 merged 中实际存在的)
    ratio_keys = [r["alias"] for r in (ratios or []) if r.get("alias")]
    # 去重(避免与 measure 同名重复)
    ratio_keys = [k for k in ratio_keys if k not in measure_keys]

    if chart_type == "table":
        return {
            "chart_type": "table",
            "data": {
                "columns": dim_keys + measure_keys + ratio_keys,
                "rows": merged,
            },
        }

    if chart_type == "pie":
        # pie 用第一个 measure 作为 value,维度拼接作为 name
        mkey = measure_keys[0] if measure_keys else "value"
        items = [
            {"name": _join_dim(r, dim_keys) or "(空)", "value": float(r.get(mkey, 0) or 0)}
            for r in merged
        ]
        # pie 按值降序
        items.sort(key=lambda x: x["value"], reverse=True)
        return {"chart_type": "pie", "data": {"items": items, "measure_key": mkey}}

    # line / bar
    x_axis = [str(_join_dim(r, dim_keys)) if _join_dim(r, dim_keys) is not None else "(空)" for r in merged]
    series = [
        {"name": mk, "data": [float(r.get(mk, 0) or 0) for r in merged]}
        for mk in measure_keys + ratio_keys
    ]
    return {
        "chart_type": chart_type,
        "data": {
            "x_axis": x_axis,
            "series": series,
            "dim_keys": dim_keys,
            "measure_keys": measure_keys + ratio_keys,
        },
    }


# ---------- 辅助函数 ----------


def _get_column(model, field_name: str):
    """安全获取模型列(防注入:字段名必须存在于模型)"""
    col = getattr(model, field_name, None)
    if col is None:
        raise ValueError(f"字段不存在: {model.__tablename__}.{field_name}")
    return col


def _apply_time_grain(col, grain: str):
    """时间粒度:hour/day/week/month → to_char"""
    if grain == "hour":
        # 00-23,用于客服工作负荷按时段分布分析
        return func.to_char(col, "HH24")
    if grain == "day":
        return func.to_char(col, "YYYY-MM-DD")
    if grain == "week":
        return func.to_char(col, "IYYY-IW")
    if grain == "month":
        return func.to_char(col, "YYYY-MM")
    if grain == "none":
        return col
    # 默认按日
    return func.to_char(col, "YYYY-MM-DD")


def _apply_agg(col, agg: str):
    """聚合函数"""
    if agg == "sum":
        return func.coalesce(func.sum(col), 0)
    if agg == "count":
        return func.coalesce(func.count(), 0)
    if agg == "avg":
        return func.coalesce(func.avg(col), 0)
    if agg == "count_distinct":
        return func.coalesce(func.count(func.distinct(col)), 0)
    # 默认 sum
    return func.coalesce(func.sum(col), 0)


def _build_op_condition(col, cond: dict):
    """根据 op 构建条件表达式"""
    op = cond.get("op", "eq")
    value = cond.get("value")

    if op == "eq":
        return col == value
    if op == "ne":
        return col != value
    if op == "in":
        return col.in_(value if isinstance(value, list) else [value])
    if op == "notin":
        return col.notin_(value if isinstance(value, list) else [value])
    if op == "contains":
        return col.like(f"%{value}%")
    if op == "not_contains":
        return ~col.like(f"%{value}%")
    if op == "gt":
        return col > value
    if op == "lt":
        return col < value
    if op == "gte":
        return col >= value
    if op == "lte":
        return col <= value
    if op == "between":
        # value = [start, end]
        return col.between(value[0], value[1])
    if op == "is_null":
        return col.is_(None)
    if op == "is_not_null":
        return col.isnot(None)
    raise ValueError(f"不支持的操作符: {op}")


def _build_filters(model, filters: list[dict]) -> list:
    """构建 WHERE 条件列表"""
    conditions = []
    for f in filters or []:
        col = _get_column(model, f["field"])
        conditions.append(_build_op_condition(col, f))
    return conditions


def _build_time_filter(model, seg: dict, time_range: dict):
    """构建时间筛选条件

    优先用 segment 中维度 role=time 的字段,否则用 DataSource.time_field
    """
    time_field = None
    for dim in seg.get("dimensions", []):
        if dim.get("role") == "time":
            time_field = dim["field"]
            break
    if not time_field:
        return None

    col = _get_column(model, time_field)
    conditions = []
    if time_range.get("start"):
        conditions.append(col >= time_range["start"])
    if time_range.get("end"):
        conditions.append(col <= time_range["end"])
    if not conditions:
        return None
    return and_(*conditions) if len(conditions) > 1 else conditions[0]


def _resolve_time_range(config: dict, params: dict) -> dict | None:
    """解析时间范围:优先用 params,其次 config.time_range_default"""
    start = params.get("start")
    end = params.get("end")
    if start or end:
        return {"start": _parse_dt(start), "end": _parse_dt(end)}

    default = config.get("time_range_default")
    if not default:
        return None
    days = default.get("days", 30)
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    return {"start": start_dt, "end": end_dt}


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    # 支持 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _load_rules(db: Session, rule_ids: list[int]) -> list[ClassificationRule]:
    """加载分类规则"""
    if not rule_ids:
        return []
    rows = db.execute(
        select(ClassificationRule).where(
            ClassificationRule.id.in_(rule_ids),
            ClassificationRule.is_active.is_(True),
        )
    ).scalars().all()
    return list(rows)


def _extract_dim_keys_from_segments(segments: list[dict]) -> list[str]:
    """从 segments 配置提取维度 alias(取第一个 segment 的)"""
    if not segments:
        return []
    seg = segments[0]
    return [d.get("alias") or d["field"] for d in seg.get("dimensions", [])]


def _extract_measure_keys(segments: list[dict]) -> list[str]:
    """从 segments 提取所有 measure alias(去重)"""
    keys: list[str] = []
    for seg in segments:
        for m in seg.get("measures", []):
            mk = m.get("alias") or (m["field"] if m["field"] != "*" else "count")
            if mk not in keys:
                keys.append(mk)
    return keys


def _join_dim(row: dict, dim_keys: list[str]) -> str:
    """拼接维度值(多维度时用 / 分隔)"""
    if not dim_keys:
        return ""
    parts = [str(row.get(k)) for k in dim_keys if row.get(k) is not None]
    return " / ".join(parts) if parts else ""
