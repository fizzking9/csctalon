# 看板连通 + Phase 2 批量执行 实施方案

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context

看板体系重构(commit 925375f)已落地三层架构的"看板层",但留下两处待补:
1. **视图 ↔ 看板未连通**:视图构建器产出的自定义视图无法便捷地"发布为看板"进入决策看板;看板里看到的预设视图也无法 fork 成可编辑副本。用户确认两个方向都要做:"发布为看板"(view→dashboard) + "另存为视图"(dashboard→view,卡片 fork 为新视图)。
2. **Phase 2 性能与体验**:当前每个看板卡片各自发一次 `/analysis-views/execute` 请求(10 卡片 = 10 次并发 HTTP),且时间范围只有 7/30/90/默认 四档快捷按钮,缺自定义日期。需补服务端批量执行端点 + 自定义日期范围,前端改为一次批量取结果后分发给各卡片(用户已确认重构 ViewItemCard 用预取结果)。

目标:打通视图与看板的双向流转;把看板渲染从 N 次请求降为 1 次,并支持任意日期区间。

---

## Feature 1: 视图 ↔ 看板连通

### 1a. 发布为看板(view → dashboard)

**后端**:无新端点,复用 `POST /dashboards`(createDashboard,已存在)。

**前端**:
- 新建 `web/src/components/PublishDashboardDialog.vue`:可复用对话框,props 接收 `{ viewId, viewCode, viewName, timeRangeDefault }`,内含表单(名称默认=视图名、分类下拉默认"自定义"、可见性 radio 默认"manager"、span 默认 24),确认时调 `createDashboard({ name, category, visibility, items:[{view_id, view_code, title:null, span, visible:true}], time_range_default })`,成功后 emit `success(dashboard)`。
- `web/src/views/analytics/ViewRender.vue`:工具栏(刷新按钮旁)加"发布为看板"按钮,打开上述对话框;成功后 `router.push('/dashboards/view/' + id)`。
- `web/src/views/analytics/ViewList.vue`:操作列加"发布为看板"行按钮(查看/编辑/删除 旁),打开同一对话框。
- `web/src/router/index.js`:新增路由 `/dashboards/view/:id` → `DashboardView` 传 `:dashboard-id` prop,`meta:{manager:true, hidden:true}`。解决自定义看板无 code、`/dashboards/by-code/:code` 打不开的问题(发布出的看板 code 为 null)。

### 1b. 另存为视图(dashboard 卡片 → fork 新视图)

**后端**:无新端点,复用 `GET /analysis-views/{id}` + `POST /analysis-views`(getAnalysisView + createAnalysisView)。

**前端**:
- `web/src/components/ViewItemCard.vue`:卡片头部(刷新按钮旁)加"另存为视图"小按钮。
  - 点击 → `ElMessageBox.confirm('将复制该视图为可编辑的自定义视图?')`
  - 确认 → `getAnalysisView(viewId)` 取完整配置 → `createAnalysisView({ code:null, name:`${view.name}(副本)`, description, chart_type, segments, merge_strategy, classification_rule_ids, time_range_default, ratios })`(is_preset 由后端 create_view 固定为 false)
  - 成功 → `ElMessage.success('已复制为自定义视图,可在视图构建器编辑')`
  - import 补 `createAnalysisView`(已有 `getAnalysisView`)。

---

## Feature 2: Phase 2 批量执行 + 自定义日期

### 2a. 后端批量执行端点

**`backend/app/schemas/dashboard.py`** 新增:
```python
class DashboardExecuteRequest(BaseModel):
    start: str | None = None
    end: str | None = None

class DashboardItemResult(BaseModel):
    view_id: int
    view_code: str | None = None
    title: str | None = None
    result: dict | None = None
    error: str | None = None
```

**`backend/app/api/v1/dashboards.py`** 新增 `POST /{dashboard_id}/execute`:
- 入参 `DashboardExecuteRequest`(start/end 可空,空则各视图回退自身 time_range_default —— 复用 `_resolve_time_range` 既有逻辑)
- 加载 dashboard,404 校验 + `_check_visibility(d, user.role)` 行级权限
- 遍历 `items`(visible !== false),逐个 `db.get(AnalysisView, view_id)` → `execute_view(db, v, {"start":payload.start,"end":payload.end})`
- **错误隔离**:per-item try/except,失败项填 `error=str(e)`,成功项填 `result`
- 返回 `R(data={"dashboard_id":id, "start":..., "end":..., "items":[DashboardItemResult...]})`
- 复用 `execute_view`(analysis_engine.py:44),无需改引擎;params.start/end 优先于 view.time_range_default 的逻辑已存在(_resolve_time_range:458)
- import `execute_view` from analysis_engine、`AnalysisView` model

### 2b. 前端批量渲染重构

**`web/src/api/dashboard.js`** 新增:
```js
export function executeDashboard(id, params) {
  return request.post(`/dashboards/${id}/execute`, params)
}
```

**`web/src/views/dashboards/DashboardView.vue`** 改造:
- 删除依赖各卡片独立请求的模式;新增 `loadAllItems()`:调 `executeDashboard(id, {start, end})` → 把 `items` 数组转为 `{view_id: {result, error}}` 映射
- `visibleItems` 渲染时给每个 `ViewItemCard` 传 `:prefetched-result` 和 `:prefetched-error`(从映射取)
- `reloadKey` 变化 / 时间范围变化 → 重新 `loadAllItems()`;`batchLoading` 状态驱动卡片 loading
- 保留 Special.vue 不变(它用 `:dashboard-id` 复用 DashboardView,自动受益)

**`web/src/components/ViewItemCard.vue`** 改造(双模式):
- 新增 props:`prefetchedResult`(Object)、`prefetchedError`(String)
- 计算逻辑:`const usePrefetched = computed(() => props.prefetchedResult !== undefined || props.prefetchedError !== undefined)`
  - 若 usePrefetched:`result = prefetchedResult`、`errorMsg = prefetchedError || ''`、`loading` 由父级 `batchLoading` 传入或省略;不再 onMounted/watch 独立请求
  - 否则:保持现有独立请求逻辑(onMounted loadData、watch start/end/reloadKey)—— 供单卡刷新按钮与未来独立使用
- 刷新按钮:始终走独立 `executeAnalysisView({view_id})` 单卡请求(刷新一张图不必重跑全量);刷新后用独立结果覆盖显示
- "另存为视图"按钮(Feature 1b)同在此组件

### 2c. 自定义日期范围

**`web/src/views/dashboards/DashboardView.vue`** 时间范围 UI:
- radio-group 增加"自定义"选项:`近7天 / 近30天 / 近90天 / 默认 / 自定义`
- 选"自定义"时显示 `el-date-picker type="daterange"`(v-model 绑 `customRange`)
- `effectiveStart`/`effectiveEnd` computed:
  - rangeDays>0 → dayjs 减天数(现有逻辑)
  - rangeDays===0 且非自定义 → 空(各视图用自身默认)
  - 自定义 → customRange[0]/[1]
- 批量请求用 effectiveStart/End;切换日期自动触发 `loadAllItems()`

---

## 涉及文件清单

**后端**(2 文件):
- `backend/app/schemas/dashboard.py` — 加 DashboardExecuteRequest / DashboardItemResult
- `backend/app/api/v1/dashboards.py` — 加 POST /{id}/execute

**前端**(7 文件,1 新建):
- `web/src/components/PublishDashboardDialog.vue` — 新建,发布为看板可复用对话框
- `web/src/components/ViewItemCard.vue` — 加另存为视图 + prefetchedResult 双模式
- `web/src/views/dashboards/DashboardView.vue` — 批量调用 + 自定义日期
- `web/src/views/analytics/ViewRender.vue` — 加发布为看板按钮
- `web/src/views/analytics/ViewList.vue` — 加发布为看板行按钮
- `web/src/api/dashboard.js` — 加 executeDashboard
- `web/src/router/index.js` — 加 /dashboards/view/:id 路由

无新模型、无迁移、无新数据源。

---

## 复用的既有逻辑

- `execute_view(db, view, params)`(analysis_engine.py:44)— 批量端点逐项调用,不重写聚合
- `_resolve_time_range`(analysis_engine.py:458)— params.start/end 优先于 view.time_range_default,批量端点传 {start,end} 即可统一时间范围
- `createDashboard` / `getAnalysisView` / `createAnalysisView` — 连通功能全复用现有 CRUD,无新端点
- `_check_visibility`(dashboards.py:32)— 批量端点复用行级权限校验

## 验证

1. **重建后端** `docker compose up -d --build backend`,验证 `POST /dashboards/1/execute` 返回 10 项结果(退款看板),curl 检查 items 数组长度与各 result.chart_type
2. **重建前端** `docker compose up -d --build web`
3. **浏览器端到端**(browser_use):
   - 退款看板:一次批量请求,10 卡片渲染 0 错误;切换"自定义"日期 → el-date-picker 出现 → 选区间 → 卡片重载
   - ViewRender 页:点"发布为看板" → 对话框 → 保存 → 跳转 /dashboards/view/:id 渲染单视图看板
   - ViewList 页:行内"发布为看板"同样可用
   - 看板卡片"另存为视图" → 确认 → 视图列表出现"{原名}(副本)"自定义视图
   - 单卡刷新按钮 → 仅该卡独立重载
4. **网络请求核对**:打开退款看板时 Network 应只见 1 次 `/dashboards/{id}/execute`(对比旧版 10 次 `/analysis-views/execute`)
5. **回归**:投诉/接待/绩效看板仍正常;Special 专项页正常;旧分析视图页不受影响
