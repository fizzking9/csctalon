# P0 阶段 2：分析引擎执行器 + ViewBuilder + 4 大看板迁移

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context（为什么做）

v0.2 审计确认系统核心从「4 个固定硬编码看板」转向「自助式数据分析引擎」。阶段 1 已完成数据源元数据注册（DataSource + DataSourceField，4 张表 ~45 个字段语义已落库）和导入增强。阶段 2 交付引擎执行器本体，让「拖拽配置 → 即时出图」跑通，并把现有 4 大硬编码看板迁移为预设视图，验证引擎能力的同时清理旧代码。

**核心设计哲学（用户确认）**：引擎是「哑执行器」——不理解业务含义，只根据视图配置生成 SQL。DataSourceField 的 semantic_role 仅作 UI 分组提示（维度区/度量区/时间区），字段在视图中的实际用途由配置人员在 ViewBuilder 中的放置位置决定。这样系统对字段的理解问题被简化为「配置问题」而非「代码问题」。

## 已确认决策

| # | 决策 | 选择 |
|---|------|------|
| 1 | 跨表累加方式 | 分表聚合后相加（aggregate separately then add），**不 JOIN，不行级对齐** |
| 2 | 动态分类规则时机 | **查询时动态计算**（CASE WHEN 注入 SQL），不污染业务表，规则可随时调整立即生效 |
| 3 | 引擎执行模型 | 哑执行器：只看视图配置，不依赖 semantic_role 硬编码逻辑；semantic_role 仅作 UI 提示 |
| 4 | 字段用途来源 | ViewBuilder 中的放置位置（用户拖到维度区=维度，拖到度量区=度量），非 DataSourceField.semantic_role |
| 5 | 阶段 2 范围 | 引擎核心 + ViewBuilder + 迁移 4 大看板为预设视图（旧硬编码端点保留兼容但标记 deprecated） |
| 6 | 视图配置存储 | 单表 JSONB 存 segments 配置（简单、灵活、易迭代，避免多表 JOIN 复杂性） |
| 7 | 分类规则存储 | 独立 ClassificationRule 模型（全局复用，可被多个视图引用） |
| 8 | 时间粒度 | day/week/month/none 四档，用 PostgreSQL `to_char` 或 `date_trunc` |
| 9 | 图表类型 | line/bar/pie/table 四种（覆盖现有看板需求，后续可扩展） |

## 数据模型（2 张新表）

### AnalysisView（分析视图）
新建 [backend/app/models/analysis_view.py](file:///Users/tanzk/dev/csctalon/backend/app/models/analysis_view.py)

```python
class AnalysisView(IDMixin, TimestampMixin, Base):
    """分析视图 - 一行 = 一个用户自定义/预设分析视图"""
    __tablename__ = "analysis_views"

    code: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True, comment="预设视图代码（preset 才有，如 cash_trend）")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="视图名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="line/bar/pie/table")
    segments: Mapped[list] = mapped_column(JSONB, nullable=False, comment="数据源片段配置数组")
    merge_strategy: Mapped[str] = mapped_column(String(16), default="add", nullable=False, comment="add=分表聚合后相加")
    classification_rule_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, comment="引用的规则 ID 列表")
    time_range_default: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="默认时间范围 {days:30}")
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="预设视图（迁移的4大看板）")
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, comment="active/archived")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
```

**segments JSONB 结构**（核心配置，引擎据此生成 SQL）：
```json
[
  {
    "source_code": "refund",
    "label": "直接退款",
    "dimensions": [
      {"field": "created_at_raw", "alias": "日期", "role": "time", "time_grain": "day"}
    ],
    "measures": [
      {"field": "refund_amount", "agg": "sum", "alias": "退款金额"},
      {"field": "*", "agg": "count", "alias": "笔数"}
    ],
    "filters": [
      {"field": "refund_method", "op": "eq", "value": "线上"},
      {"field": "operator", "op": "notin", "value": ["sys", "system"]}
    ],
    "classification_target": "operator",
    "limit": null,
    "order_by": null
  }
]
```

### ClassificationRule（分类规则）
同文件

```python
class ClassificationRule(IDMixin, TimestampMixin, Base):
    """分类规则 - 把维度值动态归类为业务标签（如清退/涨价/限速 → 专项类型）"""
    __tablename__ = "classification_rules"

    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="规则名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules: Mapped[list] = mapped_column(JSONB, nullable=False, comment="规则定义数组")
    default_label: Mapped[str] = mapped_column(String(64), default="其他", nullable=False, comment="不匹配时的默认标签")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
```

**rules JSONB 结构**（CASE WHEN 生成依据）：
```json
[
  {
    "label": "清退专项",
    "match": "any",
    "conditions": [
      {"field": "refund_reason", "op": "contains", "value": "清退"},
      {"field": "operator", "op": "in", "value": ["企微-张三", "企微-李四"]}
    ]
  },
  {
    "label": "涨价专项",
    "match": "all",
    "conditions": [
      {"field": "refund_reason", "op": "contains", "value": "涨价"}
    ]
  }
]
```

**支持的 op**：eq/ne/in/notin/contains/not_contains/gt/lt/gte/lte/between/is_null/is_not_null

## 引擎执行器 engine.py

新建 [backend/app/services/analysis_engine.py](file:///Users/tanzk/dev/csctalon/backend/app/services)

### 核心函数

```python
def execute_view(db: Session, view: AnalysisView, params: dict) -> dict:
    """执行分析视图，返回图表数据

    params: {start?: str, end?: str, filters_override?: list}
    返回: {chart_type, dimensions, series, rows, summary}
    """
    rules = _load_rules(db, view.classification_rule_ids)
    time_range = _resolve_time_range(view, params)

    # 分表聚合：每个 segment 独立查询，返回 [{dim_values, measures}]
    segment_results = []
    for seg in view.segments:
        rows = _execute_segment(db, seg, rules, time_range, params)
        segment_results.append({"segment": seg, "rows": rows})

    # 合并：按维度对齐后相加（merge_strategy=add）
    merged = _merge_segment_results(segment_results, view.merge_strategy)

    # 转换为图表数据格式
    return _to_chart_data(merged, view.chart_type, view.segments)
```

### _execute_segment（单片段查询）

```python
def _execute_segment(db, seg, rules, time_range, params) -> list[dict]:
    """构建并执行单 segment 的 SQLAlchemy 查询"""
    ds = _get_data_source(db, seg["source_code"])
    model = _get_model_by_table(ds.table_name)

    # 1. 构建 SELECT 子句
    select_cols = []
    group_cols = []
    for dim in seg["dimensions"]:
        col = getattr(model, dim["field"])
        if dim.get("role") == "time" and dim.get("time_grain"):
            col = _apply_time_grain(col, dim["time_grain"])  # to_char/date_trunc
            col = col.label(dim.get("alias", dim["field"]))
        else:
            col = col.label(dim.get("alias", dim["field"]))
        select_cols.append(col)
        group_cols.append(col)

    for m in seg["measures"]:
        if m["field"] == "*":
            col = func.count().label(m.get("alias", "count"))
        else:
            col = _apply_agg(getattr(model, m["field"]), m["agg"]).label(m.get("alias", m["field"]))
        select_cols.append(col)

    # 2. 分类规则注入（如果配置了 classification_target）
    if seg.get("classification_target") and rules:
        case_expr = _build_classification_case(model, seg["classification_target"], rules)
        select_cols.insert(0, case_expr.label(seg["classification_target"] + "_class"))
        group_cols.insert(0, case_expr)

    # 3. WHERE 子句
    conditions = _build_filters(model, seg.get("filters", []))
    if time_range and seg_has_time_field(seg):
        conditions.append(_build_time_filter(model, seg, time_range))
    # params 中的 override filters 也叠加
    conditions.extend(_build_filters(model, params.get("filters_override", [])))

    # 4. 执行
    stmt = select(*select_cols)
    if conditions:
        stmt = stmt.where(*conditions)
    if group_cols:
        stmt = stmt.group_by(*group_cols)
    if seg.get("order_by"):
        stmt = stmt.order_by(*_build_order(model, seg["order_by"]))
    if seg.get("limit"):
        stmt = stmt.limit(seg["limit"])

    rows = db.execute(stmt).all()
    return [r._mapping for r in rows]
```

### _build_classification_case（分类规则转 CASE WHEN）

```python
def _build_classification_case(model, target_field, rules):
    """把 ClassificationRule.rules 转成 SQLAlchemy case() 表达式"""
    target_col = getattr(model, target_field)
    whens = []
    for rule in rules.rules:
        conditions = []
        for cond in rule["conditions"]:
            col = getattr(model, cond["field"])
            conditions.append(_build_op_condition(col, cond))
        match_all = and_(*conditions) if rule.get("match") == "all" else or_(*conditions)
        whens.append((match_all, rule["label"]))
    return case(*whens, else_=rules.default_label)
```

### _merge_segment_results（合并多源）

```python
def _merge_segment_results(segment_results, strategy) -> list[dict]:
    """分表聚合后相加：按维度键对齐，measure 相加"""
    if len(segment_results) == 1:
        return segment_results[0]["rows"]

    merged: dict[tuple, dict] = {}
    dim_keys = _extract_dim_keys(segment_results)  # 维度字段名列表
    for sr in segment_results:
        for row in sr["rows"]:
            key = tuple(row.get(k) for k in dim_keys)
            if key not in merged:
                merged[key] = {k: row.get(k) for k in dim_keys}
                for m in sr["segment"]["measures"]:
                    merged[key][m.get("alias", m["field"])] = 0
            for m in sr["segment"]["measures"]:
                mkey = m.get("alias", m["field"])
                merged[key][mkey] = float(merged[key].get(mkey, 0)) + float(row.get(mkey, 0) or 0)
    return list(merged.values())
```

### _to_chart_data（转图表数据）

```python
def _to_chart_data(merged, chart_type, segments) -> dict:
    """根据 chart_type 组装前端可用的图表数据"""
    dim_keys = _extract_dim_keys_from_segments(segments)
    measure_keys = _extract_measure_keys(segments)

    if chart_type == "table":
        return {"chart_type": "table", "columns": dim_keys + measure_keys, "rows": merged}

    if chart_type == "pie":
        return {
            "chart_type": "pie",
            "items": [{"name": _join_dim(r, dim_keys), "value": r.get(measure_keys[0], 0)} for r in merged],
        }

    # line / bar
    return {
        "chart_type": chart_type,
        "x_axis": [str(_join_dim(r, dim_keys)) for r in merged],
        "series": [{"name": mk, "data": [r.get(mk, 0) for r in merged]} for mk in measure_keys],
    }
```

## API 端点

新建 [backend/app/api/v1/analysis_views.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1)

### 视图管理
- `GET /analysis-views` — 列表（支持 preset/active 筛选）
- `GET /analysis-views/{id}` — 详情
- `POST /analysis-views` — 创建
- `PUT /analysis-views/{id}` — 更新
- `DELETE /analysis-views/{id}` — 删除（预设视图不可删）
- `POST /analysis-views/execute` — 执行视图（body: `{view_id?: int, inline_config?: dict, params?: dict}`）

### 分类规则管理
- `GET /classification-rules` — 列表
- `POST /classification-rules` — 创建
- `PUT /classification-rules/{id}` — 更新
- `DELETE /classification-rules/{id}` — 删除
- `POST /classification-rules/preview` — 预览规则效果（传 sample data 或指定 source）

### 字段元数据查询（ViewBuilder 用）
- 复用已有 `GET /data-sources/{code}/fields`（返回 semantic_role 分组的字段列表）

## 4 大看板迁移为预设视图

通过种子脚本注册 4 个 `is_preset=true` 的 AnalysisView，替代 `analytics.py` 中的硬编码函数：

### 预设视图 1：退款趋势（运营分析）
```json
{
  "code": "preset_refund_trend",
  "name": "退款趋势(按日)",
  "chart_type": "line",
  "segments": [{
    "source_code": "refund",
    "dimensions": [{"field": "created_at_raw", "alias": "日期", "role": "time", "time_grain": "day"}],
    "measures": [
      {"field": "refund_amount", "agg": "sum", "alias": "退款金额"},
      {"field": "*", "agg": "count", "alias": "笔数"}
    ],
    "filters": []
  }]
}
```

### 预设视图 2：真实现金流趋势（现金流分析 - 双源相加）
```json
{
  "code": "preset_cash_trend",
  "name": "真实现金流趋势",
  "chart_type": "line",
  "merge_strategy": "add",
  "segments": [
    {
      "source_code": "refund",
      "label": "直接退款",
      "dimensions": [{"field": "created_at_raw", "alias": "日期", "role": "time", "time_grain": "day"}],
      "measures": [{"field": "refund_amount", "agg": "sum", "alias": "金额"}],
      "filters": [
        {"field": "refund_method", "op": "eq", "value": "线上"},
        {"field": "operator", "op": "notin", "value": ["sys", "system", "SYS", "System"]}
      ]
    },
    {
      "source_code": "wallet",
      "label": "用户提现",
      "dimensions": [{"field": "op_time", "alias": "日期", "role": "time", "time_grain": "day"}],
      "measures": [{"field": "op_amount", "agg": "sum", "alias": "金额"}],
      "filters": [{"field": "op_type", "op": "eq", "value": "用户提现"}]
    }
  ]
}
```

### 预设视图 3：退款分类（按服务项/商户/套餐）
3 个独立预设视图（`preset_category_service_item` / `preset_category_merchant` / `preset_category_package`），均 chart_type=pie。

### 预设视图 4：人员操作统计
3 个独立预设视图（`preset_operator_ticket` / `preset_operator_audit` / `preset_operator_refund`），均 chart_type=bar，按 person 字段分组。

## 实现步骤（按依赖排序）

### 后端（步骤 1-12）

1. **新模型**：[analysis_view.py](file:///Users/tanzk/dev/csctalon/backend/app/models/analysis_view.py)（AnalysisView + ClassificationRule）

2. **注册模型**：[models/__init__.py](file:///Users/tanzk/dev/csctalon/backend/app/models/__init__.py) 加 import

3. **alembic 迁移**：`python -m alembic revision --autogenerate -m "add analysis_views and classification_rules"`，review 产物确认含 2 新表

4. **schemas**：新建 [schemas/analysis_view.py](file:///Users/tanzk/dev/csctalon/backend/app/schemas)
   - AnalysisViewOut / AnalysisViewCreate / AnalysisViewUpdate / AnalysisViewExecute
   - ClassificationRuleOut / ClassificationRuleCreate / ClassificationRuleUpdate
   - ExecuteResult（chart_type + 动态字段）

5. **引擎核心**：新建 [services/analysis_engine.py](file:///Users/tanzk/dev/csctalon/backend/app/services)
   - `execute_view(db, view, params)` 主入口
   - `_execute_segment` / `_build_classification_case` / `_merge_segment_results` / `_to_chart_data`
   - `_apply_time_grain`（day→`to_char(col,'YYYY-MM-DD')`，week→`to_char(col,'IYYY-IW')`，month→`to_char(col,'YYYY-MM')`，none→原值）
   - `_apply_agg`（sum→`func.sum`，count→`func.count`，avg→`func.avg`）
   - `_build_op_condition`（eq/ne/in/notin/contains/gt/lt 等操作符映射）
   - `_build_filters` / `_build_time_filter` / `_resolve_time_range`
   - 模型注册表 `_MODEL_REGISTRY`（table_name → model class，用于反射获取 SQLAlchemy 列）

6. **预设视图种子**：新建 [services/preset_views_seed.py](file:///Users/tanzk/dev/csctalon/backend/app/services)
   - `PRESET_VIEWS`：8 个预设视图配置（4 大看板拆分）
   - `ensure_preset_views_registered(db)`：幂等注册（按 code upsert）

7. **视图管理 API**：新建 [api/v1/analysis_views.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1)
   - CRUD + execute 端点
   - execute 端点支持 `view_id`（已存视图）或 `inline_config`（ViewBuilder 实时预览用，不入库）

8. **分类规则 API**：同文件或新建 `api/v1/classification_rules.py`
   - CRUD + preview 端点

9. **注册路由**：改 [api/v1/router.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/router.py) 挂载 analysis_views + classification_rules

10. **启动时注册预设**：改 [main.py](file:///Users/tanzk/dev/csctalon/backend/app/main.py) lifespan，在 `ensure_data_sources_registered` 后调 `ensure_preset_views_registered`

11. **旧端点兼容**：[api/v1/analytics.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/analytics.py) 保留但内部改为调用 `execute_view(db, preset_view, params)`，删除 `services/analytics.py` 中的硬编码函数（或保留作为对照，标记 deprecated）

12. **测试**：新建 [tests/test_analysis_engine.py](file:///Users/tanzk/dev/csctalon/backend/tests)
    - 单 segment 查询（退款趋势）
    - 双 segment 相加（真实现金流）
    - 分类规则注入（CASE WHEN 生成正确）
    - 时间粒度切换（day/week/month）
    - 各种 op 过滤器

### 前端（步骤 13-20）

13. **api 模块**：新建 [api/analysis-view.js](file:///Users/tanzk/dev/csctalon/web/src/api)
    - listAnalysisViews / getAnalysisView / createAnalysisView / updateAnalysisView / deleteAnalysisView
    - executeAnalysisView(payload) — POST /analysis-views/execute
    - listClassificationRules / createClassificationRule / updateClassificationRule / deleteClassificationRule
    - previewClassificationRule(payload)

14. **ViewBuilder.vue**：新建 [views/analytics/ViewBuilder.vue](file:///Users/tanzk/dev/csctalon/web/src/views/analytics)
    - **三栏布局**：
      - 左栏：数据源选择 + 字段面板（从 `/data-sources/{code}/fields` 加载，按 semantic_role 分组：维度/度量/时间/分类/人名/SN）
      - 中栏：配置区（segments 标签页，每个 segment 一个卡片：数据源、维度区、度量区、筛选区、时间粒度、分类规则）
      - 右栏：实时预览（执行 inline_config，BaseChart 渲染）
    - **交互**：左栏字段可拖拽到中栏对应区域（或点击添加）
    - **多 segment**：支持添加/删除 segment，每个 segment 独立配置
    - **保存**：保存为 AnalysisView（输入名称、图表类型）
    - **分类规则编辑器**：内嵌规则编辑（if field op value then label）

15. **ViewList.vue**：新建 [views/analytics/ViewList.vue](file:///Users/tanzk/dev/csctalon/web/src/views/analytics)
    - 已保存视图列表（区分预设/自定义）
    - 点击视图 → 进入 ViewRender.vue 渲染

16. **ViewRender.vue**：新建 [views/analytics/ViewRender.vue](file:///Users/tanzk/dev/csctalon/web/src/views/analytics)
    - 根据 view_id 加载视图配置 → 调 execute → 渲染图表
    - 支持日期范围筛选、筛选器覆盖
    - 复用 BaseChart.vue

17. **分类规则管理页**：新建 [views/system/ClassificationRules.vue](file:///Users/tanzk/dev/csctalon/web/src/views/system)
    - 规则列表 + 编辑器（条件组合 → 标签）

18. **路由更新**：改 [router/index.js](file:///Users/tanzk/dev/csctalon/web/src/router/index.js)
    - 新增 `/analytics/builder`（ViewBuilder）
    - 新增 `/analytics/views`（ViewList）
    - 新增 `/analytics/views/:id`（ViewRender）
    - 保留旧 `/analytics/operation` 等路由，内部改为重定向到对应预设视图渲染页
    - 新增 `/system/classification-rules`

19. **菜单更新**：改 [layouts/MainLayout.vue](file:///Users/tanzk/dev/csctalon/web/src/layouts/MainLayout.vue)
    - 分析看板下新增：视图构建器、我的视图
    - 系统管理下新增：分类规则

20. **样式**：ViewBuilder 的三栏布局、拖拽提示、字段分组样式

## 关键复用点（不要重写）

- [services/analytics.py:cash_out_trend](file:///Users/tanzk/dev/csctalon/backend/app/services/analytics.py#L110) — 「分表聚合后相加」的范例，engine.py 的 `_merge_segment_results` 照此泛化
- [models/data_source.py](file:///Users/tanzk/dev/csctalon/backend/app/models/data_source.py) — DataSource/DataSourceField 已建好，ViewBuilder 直接用
- [services/data_source_registry.py:FIELD_OVERRIDES](file:///Users/tanzk/dev/csctalon/backend/app/services/data_source_registry.py#L60) — 字段语义已注册，ViewBuilder 据此分组
- [api/v1/data_sources.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/data_sources.py) — `GET /data-sources/{code}/fields` 已有，ViewBuilder 复用
- [components/BaseChart.vue](file:///Users/tanzk/dev/csctalon/web/src/components/BaseChart.vue) — ECharts 封装，ViewRender 复用
- [schemas/common.py](file:///Users/tanzk/dev/csctalon/backend/app/schemas/common.py) 的 `R` + `PageBase`
- [api/deps.py](file:///Users/tanzk/dev/csctalon/backend/app/api/deps.py) 的 `CurrentUser`/`DBSession`

## 验证方案（端到端）

### 1. 启动与迁移
```bash
cd backend && python -m alembic upgrade head   # 应建出 analysis_views + classification_rules 表
uvicorn app.main:app --reload                   # 启动日志见 "registered N preset views"
# 验证预设视图
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/analysis-views?preset=true
# 期望: 8 条预设视图
```

### 2. 引擎执行（核心）
```bash
# 执行预设视图:退款趋势
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"view_id": 1, "params": {"start": "2026-06-01", "end": "2026-07-21"}}' \
  localhost:8000/api/v1/analysis-views/execute
# 期望: chart_type=line, x_axis=日期数组, series=[{name:退款金额,...},{name:笔数,...}]

# 执行真实现金流趋势(双源相加)
curl -X POST ... -d '{"view_id": 2, "params": {...}}'
# 期望: 金额 = 直接退款 + 用户提现, 与旧 analytics.cash_out_trend 结果一致

# inline 预览(ViewBuilder 用)
curl -X POST ... -d '{"inline_config": {"chart_type":"bar","segments":[...]}, "params": {...}}'
# 期望: 返回 chart 数据,不入库
```

### 3. 分类规则
```bash
# 创建规则
curl -X POST ... -d '{"name":"专项分类","rules":[{"label":"清退","match":"any","conditions":[{"field":"refund_reason","op":"contains","value":"清退"}]}],"default_label":"其他"}'
# 预览效果
curl -X POST .../classification-rules/preview -d '{"rule_id":1,"source_code":"refund","target_field":"operator"}'
# 期望: 返回各 operator → 标签的映射
```

### 4. 4 大看板迁移验证
- 访问旧路由 `/analytics/operation` → 重定向到预设视图渲染页
- 对比迁移前后数据一致（同一日期范围下，退款趋势、真实现金流、分类统计数值相同）
- 旧 `analytics.py` 硬编码函数标记 deprecated 或删除

### 5. ViewBuilder 交互
- `/analytics/builder` 页面：左栏显示 4 数据源 + 字段分组
- 拖拽 refund.refund_amount 到度量区 → 右栏实时出图
- 添加第二个 segment（wallet）→ 双源相加预览
- 保存视图 → 在 `/analytics/views` 列表可见
- 点击视图 → ViewRender 页渲染正确

### 6. 异常场景
- 未注册数据源 code → 400
- segment 配置缺维度 → 400
- 分类规则引用不存在的字段 → 400
- 时间字段未配置 → 不应用时间筛选（返回全量）
- 多 segment 维度字段名不一致 → 按 alias 对齐（alias 相同才合并）

## 不在本阶段范围

- 智能助手处理记录数据源接入（字段已确认 13 列）→ P2
- 排班报表（复杂多级表头）→ P3
- 通用事项模块统一、发布页、关联匹配弱化 → 阶段 3
- 视图分享/权限控制（owner_id 已留字段，权限逻辑后续）→ 阶段 3
- 图表导出 PDF/Excel → 后续
- 操作时间热力图（15分钟/30分钟粒度）→ 后续（engine 已支持，需前端热力图组件）

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 动态 SQL 注入安全 | 所有字段名/值通过 SQLAlchemy ORM 参数化，不拼接原始 SQL |
| 多 segment 维度对齐失败 | 合并时按 alias 对齐，alias 不一致则报错提示 |
| 分类规则 CASE WHEN 性能 | 规则数量预计 <20，CASE WHEN 性能可接受；必要时可加索引 |
| 4 大看板迁移后数据不一致 | 保留旧端点 deprecated 期，对比验证后再删 |
| ViewBuilder 拖拽交互复杂 | 阶段 2 先用「点击添加」简化交互，拖拽后续优化 |
