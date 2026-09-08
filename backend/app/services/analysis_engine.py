"""分析引擎执行器 - 哑执行器,根据视图配置生成 SQL 并执行

核心设计:
1. 引擎不理解业务含义,只看视图配置(segments JSONB)生成 SQLAlchemy 查询
2. DataSourceField.semantic_role 仅作 UI 提示,字段实际用途由配置决定
3. 跨表累加:分表聚合后相加(aggregate separately then add),不 JOIN,不行级对齐
4. 分类规则:查询时转 CASE WHEN 注入 SQL,不污染业务表
5. 所有字段名通过 getattr(model, name) 获取(防注入),值参数化传递
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.analysis_view import AnalysisView, ClassificationRule
from app.models.card_complaint import CardComplaint
from app.models.cash_flow_wide import CashFlowWide
from app.models.cs_record import CsRecord
from app.models.data_source import DataSource
from app.models.device_combo import DeviceCombo
from app.models.external_complaint import ExternalComplaint
from app.models.merchant_refund import MerchantRefund
from app.models.person import Person, PersonAlias
from app.models.refund import Refund
from app.models.refund_record_report import RefundRecordReport
from app.models.refund_wide import RefundWide
from app.models.scrm_session import ScrmSession
from app.models.scrm_session_transfer import ScrmSessionTransfer
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal

# 模型注册表:table_name → model class(用于根据 DataSource.table_name 反射获取列)
_MODEL_REGISTRY: dict[str, type] = {
    Refund.__tablename__: Refund,
    CashFlowWide.__tablename__: CashFlowWide,
    RefundWide.__tablename__: RefundWide,
    WalletWithdrawal.__tablename__: WalletWithdrawal,
    ServiceTicket.__tablename__: ServiceTicket,
    DeviceCombo.__tablename__: DeviceCombo,
    MerchantRefund.__tablename__: MerchantRefund,
    RefundRecordReport.__tablename__: RefundRecordReport,
    ScrmSession.__tablename__: ScrmSession,
    ScrmSessionTransfer.__tablename__: ScrmSessionTransfer,
    CsRecord.__tablename__: CsRecord,
    ExternalComplaint.__tablename__: ExternalComplaint,
    CardComplaint.__tablename__: CardComplaint,
}


logger = logging.getLogger(__name__)


# ---------- 主入口 ----------


def execute_view(db: Session, view: AnalysisView | dict, params: dict | None = None) -> dict:
    """执行分析视图,返回图表数据

    Args:
        db: 数据库会话
        view: AnalysisView 实例(已存视图)或配置字典(inline 预览)
        params: {start?: str, end?: str, filters_override?: list[dict], time_grain?: str}

    Returns:
        {chart_type, data: {x_axis?, series?, items?, columns?, rows?}}
    """
    params = params or {}

    # ── 手动视图分派 (2026-08-03 加): 若 view.code 在 urge_analysis.HANDLERS / analytics.HANDLERS 中注册,直接调用专用实现
    # 用于 engine 无法表达的复杂派生逻辑:如催单分桶/待处理时长/投诉转化率/退款挽回等
    try:
        from app.services.analytics import HANDLERS as _ANALYTICS_HANDLERS
        from app.services.urge_analysis import HANDLERS as _URGE_HANDLERS
        _HANDLER_POOL: dict = {}
        _HANDLER_POOL.update(_URGE_HANDLERS or {})
        _HANDLER_POOL.update(_ANALYTICS_HANDLERS or {})
        if isinstance(view, AnalysisView):
            vc = view.code
        else:
            vc = view.get("code") if isinstance(view, dict) else None
        if vc and vc in _HANDLER_POOL:
            return _HANDLER_POOL[vc](db, params)
    except Exception as _e:
        logger.warning(f"手动视图分派失败, fallback 到 engine 默认逻辑: {_e}")

    # 统一为字典配置
    if isinstance(view, AnalysisView):
        config = {
            "chart_type": view.chart_type,
            "segments": view.segments,
            "merge_strategy": view.merge_strategy,
            "classification_rule_ids": view.classification_rule_ids or [],
            "time_range_default": view.time_range_default,
            "ratios": view.ratios or [],
            "cumulative": view.cumulative or [],
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

    # 累计差额(池子水位):基于已合并的 inflow/outflow measure 计算运行总和
    # merged 需按时间维排序;_merge_segment_results 已按 dim_keys[0] 排序,
    # 但时间维可能不是 dim_keys[0],这里按 role=time 的维度 alias 重排确保正确
    cumulative = config.get("cumulative") or []
    if cumulative:
        time_alias = None
        for d in (config["segments"][0].get("dimensions", []) if config["segments"] else []):
            if d.get("role") == "time":
                time_alias = d.get("alias") or d.get("field")
                break
        if time_alias:
            merged.sort(key=lambda r: (r.get(time_alias) is None, r.get(time_alias)))
        for c in cumulative:
            running = 0.0
            for row in merged:
                inflow = float(row.get(c["inflow"], 0) or 0)
                outflow = float(row.get(c.get("outflow"), 0) or 0) if c.get("outflow") else 0.0
                running += (inflow - outflow)
                row[c["alias"]] = round(running, 2)

    # 转换为图表数据
    return _to_chart_data(merged, config["chart_type"], config["segments"], ratios, cumulative)


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
        # 运行时覆盖:execute API params 传 time_grain 时,覆盖预设视图的固定粒度
        # (饼图/表格/明细无 time 维度,此分支不触发,无副作用)
        if role == "time" and params.get("time_grain"):
            time_grain = params["time_grain"]
        if role == "time" and time_grain and time_grain != "none":
            col = _apply_time_grain(col, time_grain)
        elif role == "person":
            # 人员维度:把业务表 raw 别名字符串解析为归并后 Person.display_name
            col = _resolve_person_name(col)
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
            agg_expr = {
                "sum": func.sum(col0),
                "count": func.count(col0),
                "avg": func.avg(col0),
                "count_distinct": func.count(func.distinct(col0)),
            }.get(agg, func.sum(col0))
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
        time_filter = _build_time_filter(model, seg, time_range, fallback_time_field=ds.time_field)
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

    # 提取维度 alias(从第一个 segment 推断,含 classification_target 生成的分类列)
    first_seg = segment_results[0]["segment"]
    dim_keys = _extract_dim_keys_from_segments([first_seg])

    # 校验各 segment 维度 alias 一致(不一致会静默丢数据:其他 segment 的行无法对齐到 merged)
    for sr in segment_results[1:]:
        other_dim_keys = _extract_dim_keys_from_segments([sr["segment"]])
        if other_dim_keys != dim_keys:
            logger.warning(
                "多 segment 维度 alias 不一致,数据可能无法对齐: %s vs %s (segment: %s)",
                dim_keys, other_dim_keys, sr["segment"].get("source_code"),
            )

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


def _to_chart_data(merged: list[dict], chart_type: str, segments: list[dict], ratios: list[dict] | None = None, cumulative: list[dict] | None = None) -> dict:
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
    # 累计差额别名追加为输出列(独立 overlay,不参与堆积)
    cumulative_keys = [c["alias"] for c in (cumulative or []) if c.get("alias")]
    cumulative_keys = [k for k in cumulative_keys if k not in measure_keys and k not in ratio_keys]

    if chart_type == "table":
        return {
            "chart_type": "table",
            "data": {
                "columns": dim_keys + measure_keys + ratio_keys + cumulative_keys,
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
        # 超过 11 项时取 TOP10,其余归并"其他"(保证占比和=100%,避免饼图碎片化)
        if len(items) > 11:
            top = items[:10]
            other_sum = round(sum(x["value"] for x in items[10:]), 2)
            top.append({"name": "其他", "value": other_sum})
            items = top
        return {"chart_type": "pie", "data": {"items": items, "measure_key": mkey}}

    # line / bar
    x_axis = [str(_join_dim(r, dim_keys)) if _join_dim(r, dim_keys) is not None else "(空)" for r in merged]
    # stack_group:segment 级配置,measure alias → 堆积组名(同组 series 在 ECharts 堆叠)
    stack_map = _extract_stack_group_map(segments)
    # series_type:segment 级配置,measure alias → series 类型(line/bar)
    # 用于柱状+曲线混合图:某 segment 配 series_type="line" 时,其 measure 在 bar 图中以曲线 overlay 渲染
    series_type_map = _extract_series_type_map(segments)
    series = []
    for mk in measure_keys + ratio_keys:
        s = {"name": mk, "data": [float(r.get(mk, 0) or 0) for r in merged]}
        if stack_map.get(mk):
            s["stack"] = stack_map[mk]
        if series_type_map.get(mk):
            s["series_type"] = series_type_map[mk]
        series.append(s)
    # 累计差额作为独立 overlay series(不参与堆积,如池子水位线)
    # 标记 series_type=line,使前端在 bar 视图中以曲线 overlay 渲染(柱状+曲线混合图)
    for ck in cumulative_keys:
        series.append({"name": ck, "data": [float(r.get(ck, 0) or 0) for r in merged], "series_type": "line"})
    return {
        "chart_type": chart_type,
        "data": {
            "x_axis": x_axis,
            "series": series,
            "dim_keys": dim_keys,
            "measure_keys": measure_keys + ratio_keys + cumulative_keys,
        },
    }


# ---------- 辅助函数 ----------


def _get_column(model, field_name: str):
    """安全获取模型列(防注入:字段名必须存在于模型)"""
    col = getattr(model, field_name, None)
    if col is None:
        raise ValueError(f"字段不存在: {model.__tablename__}.{field_name}")
    return col


def _resolve_person_name(raw_col):
    """业务表人员字段(别名原值)→ 归并后 Person.display_name 标量子查询。

    role="person" 维度专用:业务表 operator/auditor/handler 等字段存的是别名原值
    (如 '企微-何琴'/'YKM-XZ'/'店铺-张三'),通过 person_aliases.alias(唯一索引)
    → persons.display_name 解析为归并后的人员姓名,使视图按真人而非 raw 字符串聚合。

    - 已归并别名 → 对应 Person 显示名
    - 待归并/person_id IS NULL 别名 → NULL(图表显示"(空)",提示去归并)
    - 未提取的新别名(不在 person_aliases) → NULL
    - sys/system 系统别名(person_id=NULL) → NULL(配合视图 raw 层 notin 过滤更佳)
    """
    subq = (
        select(Person.display_name)
        .join(PersonAlias, PersonAlias.person_id == Person.id)
        .where(PersonAlias.alias == raw_col)
        .limit(1)
    )
    return subq.scalar_subquery()


def _apply_time_grain(col, grain: str):
    """时间粒度:hour/day/week/month/quarter/year → to_char

    日期采用紧凑格式(YY/MM/DD 等),带前导零 → 字典序天然等于时间序
    (order_by group_cols[0] 无需特殊处理)。周保留 ISO 年(IYYY)以保证跨年周正确性;
    年单独显示需 4 位避免歧义。
    """
    if grain == "hour":
        # 00-23,用于客服工作负荷按时段分布分析
        return func.to_char(col, "HH24")
    if grain == "day":
        # 26/08/01
        return func.to_char(col, "YY/MM/DD")
    if grain == "week":
        # 2026-31(ISO 年-周,保留 4 位年避免跨年周错位)
        return func.to_char(col, "IYYY-IW")
    if grain == "month":
        # 26/08
        return func.to_char(col, "YY/MM")
    if grain == "quarter":
        # 26/Q3;"Q" 用双引号包裹转义为字面量,否则被当占位符
        return func.to_char(col, 'YY/"Q"Q')
    if grain == "year":
        return func.to_char(col, "YYYY")
    if grain == "none":
        return col
    # 默认按日
    return func.to_char(col, "YY/MM/DD")


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


def _build_time_filter(model, seg: dict, time_range: dict, fallback_time_field: str | None = None):
    """构建时间筛选条件

    优先用 segment 中维度 role=time 的字段;否则回退到 DataSource.time_field
    (使非时间维度视图如"按审核人/状态/原因"也能受看板时间范围过滤)
    """
    time_field = None
    for dim in seg.get("dimensions", []):
        if dim.get("role") == "time":
            time_field = dim["field"]
            break
    if not time_field:
        time_field = fallback_time_field
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
    """从 segments 配置提取维度 alias(取第一个 segment 的)

    含 classification_target 生成的分类列(alias = f"{target}_class")
    """
    if not segments:
        return []
    seg = segments[0]
    keys: list[str] = []
    # 分类列在最前(与 _execute_segment 中 select_cols 顺序一致)
    if seg.get("classification_target"):
        keys.append(f"{seg['classification_target']}_class")
    keys.extend(d.get("alias") or d["field"] for d in seg.get("dimensions", []))
    return keys


def _extract_measure_keys(segments: list[dict]) -> list[str]:
    """从 segments 提取所有 measure alias(去重)"""
    keys: list[str] = []
    for seg in segments:
        for m in seg.get("measures", []):
            mk = m.get("alias") or (m["field"] if m["field"] != "*" else "count")
            if mk not in keys:
                keys.append(mk)
    return keys


def _extract_stack_group_map(segments: list[dict]) -> dict[str, str]:
    """从 segments 提取 measure alias → stack_group 映射(堆积图分组)。

    stack_group 是 segment 级配置:同 stack_group 的不同 segment(用不同 measure alias)
    在前端 ECharts 会堆叠成一根柱/线。未配 stack_group 的 segment 输出独立 series(overlay)。

    注意:同 segment 的多个 measure 都会映射到同一 stack_group,意味着它们也会互相堆叠
    (通常非预期,因金额/笔数单位不同)。堆积图建议每 segment 只放 1 个 measure。
    """
    m: dict[str, str] = {}
    for seg in segments:
        sg = seg.get("stack_group")
        if not sg:
            continue
        for measure in seg.get("measures", []):
            mk = measure.get("alias") or (measure["field"] if measure["field"] != "*" else "count")
            m[mk] = sg
    return m


def _extract_series_type_map(segments: list[dict]) -> dict[str, str]:
    """从 segments 提取 measure alias → series_type 映射(柱状+曲线混合图)。

    series_type 是 segment 级配置:覆盖 chart_type 的默认渲染类型。
    典型场景:chart_type=bar 的视图中,某 segment 配 series_type="line",
    其 measure 以曲线 overlay 渲染(如商户号实际现金流曲线 vs 估算柱状堆积)。
    与 stack_group 互不冲突:同 segment 可同时配 stack_group(堆积)和 series_type(类型)。
    """
    m: dict[str, str] = {}
    for seg in segments:
        st = seg.get("series_type")
        if not st:
            continue
        for measure in seg.get("measures", []):
            mk = measure.get("alias") or (measure["field"] if measure["field"] != "*" else "count")
            m[mk] = st
    return m


def _join_dim(row: dict, dim_keys: list[str]) -> str:
    """拼接维度值(多维度时用 / 分隔)"""
    if not dim_keys:
        return ""
    parts = [str(row.get(k)) for k in dim_keys if row.get(k) is not None]
    return " / ".join(parts) if parts else ""
