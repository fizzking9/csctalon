# 看板(Dashboard)体系重构实施方案

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context

产品已确立三层架构：**数据源 → 视图 → 看板**。视图层（AnalysisView + execute_view 引擎）已成熟，24 个预设视图已 seed 覆盖运营/现金流/退款分类/人员操作/客服接待/投诉专项 6 组。但"看板层"缺失：现有 4 个决策看板是硬编码页面（Operation/Cash/Category/Operator），调用 [DEPRECATED] 的 analytics 旧 API，无法配置、无权限分层、与视图引擎脱节。

本次新增**看板(Dashboard)层**：把多个视图按业务主题聚合到一个可配置页面，支持布局/时间范围/权限，面向决策消费。产出 4 个常规预设看板（退款/投诉/接待/绩效）+ 专项跟进页 + 看板配置页（数据中心）。旧 4 个硬编码看板菜单替换为新看板，文件保留兼容。

## 设计要点

- **复用视图引擎**：看板不重新实现聚合，每个 item 调现有 `POST /analysis-views/execute`（view_id 模式），前端 `Promise.allSettled` 并发，单视图失败隔离。MVP 不建服务端批量端点。
- **单表 + items JSONB**：Dashboard 模型借鉴 AnalysisView 模式，items 数组承载布局（顺序即展示顺序）。
- **权限双轨**：`visibility` 字段（public/manager/admin）做 API 行级过滤；路由 `meta.manager` 做页面级过滤；菜单 `v-if="isManager"`。
- **专项跟进复用框架**：不另建表，专项看板 = category=专项 的 Dashboard，专项跟进页是 category=专项 的看板切换入口。
- **绩效看板只展示操作量统计**，不算绩效得分（项目约束：绩效算法在别处）。

## 数据模型

### `backend/app/models/dashboard.py`（新建）

```
Dashboard(IDMixin, TimestampMixin, Base):
  __tablename__ = "dashboards"
  code: String(64) unique nullable        # 预设看板代码 preset_dashboard_xxx
  name: String(128) not null
  description: Text nullable
  category: String(32) not null index      # 退款/投诉/接待/绩效/专项/自定义
  items: JSONB not null                    # [{view_id,view_code,title,span,visible}]
  time_range_default: JSONB nullable       # {"days":30}
  is_preset: Boolean default False
  status: String(16) default "active"      # active/archived
  visibility: String(16) default "manager" # public/manager/admin
  owner_id: FK(users.id) nullable
  sort_order: Integer default 0
```

items 结构：`{view_id:int, view_code:str, title:str|null, span:int(1-24,默认12), visible:bool(默认true)}`。顺序即展示顺序。

## 4 个常规预设看板组成

### 退款看板 `preset_dashboard_refund`（category=退款，10 视图全整合）

| view_code | chart | span |
|---|---|---|
| preset_refund_trend | line | 16 |
| preset_refund_status | pie | 8 |
| preset_refund_reason_top | bar | 12 |
| preset_refund_method | pie | 12 |
| preset_cash_trend | line | 24 |
| preset_wallet_vs_withdraw | bar | 12 |
| preset_merchant_cash | table | 12 |
| preset_category_service_item | pie | 8 |
| preset_category_merchant | pie | 8 |
| preset_category_package | pie | 8 |

### 投诉看板 `preset_dashboard_complaint`（7 视图）
external_complaint_trend(12) / card_complaint_trend(12) / external_complaint_category(12) / card_complaint_category(12) / external_complaint_by_follower(12) / card_complaint_by_handler(12) / external_complaint_valid_rate(24)

### 接待看板 `preset_dashboard_reception`（4 视图）
cs_reception_trend(12) / cs_reception_by_operator(12) / cs_workload_hour(12) / cs_recover_rate(12)

### 绩效看板 `preset_dashboard_performance`（3 视图，description 注明"仅展示操作量统计,非绩效得分"）
operator_ticket(8) / operator_audit(8) / operator_refund(8)

以上均 `time_range_default={"days":30}`，`visibility="manager"`。

## 后端实施

1. **`backend/app/models/dashboard.py`** 新建 Dashboard 模型（字段见上）。
2. **`backend/app/models/__init__.py`** 加 `from app.models.dashboard import Dashboard` + `__all__`。
3. **`backend/alembic/versions/n4d5e6f7b8c9_add_dashboards.py`** 新建迁移：建 dashboards 表（code unique idx / category idx / status idx），down_revision=m3c4d5e6f7a8。参照现有迁移文件写法。
4. **`backend/app/services/preset_dashboards_seed.py`** 新建：`PRESET_DASHBOARDS`（4 个预设看板配置）+ `ensure_preset_dashboards_registered(db)`。幂等 upsert by code；预加载 `AnalysisView.code→id` 映射填充 items.view_id，找不到的 view_code 跳过并计数。参照 [preset_views_seed.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/preset_views_seed.py) 模式。
5. **`backend/app/schemas/dashboard.py`** 新建：DashboardItem / DashboardOut / DashboardCreate / DashboardUpdate / DashboardListOut。
6. **`backend/app/api/v1/dashboards.py`** 新建路由 `/dashboards`：
   - `GET ""` 列表：支持 category/status 筛选；按 `_allowed_visibility(user.role)` 过滤（admin=全部 / supervisor=public+manager / agent=public）；分页。
   - `GET "/{id}"` 详情：校验 visibility，403 若无权。
   - `POST ""` 创建：`AdminOrSupervisor`；is_preset 强制 False。
   - `PUT "/{id}"` 更新：`AdminOrSupervisor`；预设看板 code/category disabled（不可改）。
   - `DELETE "/{id}"` 删除：`AdminOrSupervisor`；预设不可删（400）。
   - 复用 [deps.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/deps.py) 的 `CurrentUser`/`AdminOrSupervisor`/`DBSession`。
   - **不建** `/execute` 批量端点（MVP 前端并发调 `/analysis-views/execute`）。
7. **`backend/app/api/v1/router.py`** 加 `api_router.include_router(dashboards.router)`。
8. **`backend/app/main.py`** `_init_data_sources_and_presets()`（L73-83）在 `ensure_preset_views_registered` 之后加 `ensure_preset_dashboards_registered(db)`，日志输出注册统计。

`_allowed_visibility`：
```
admin → [public, manager, admin]
supervisor → [public, manager]
agent → [public]
```

## 前端实施

1. **`web/src/api/dashboard.js`** 新建：listDashboards/getDashboard/createDashboard/updateDashboard/deleteDashboard。
2. **`web/src/components/ViewItemCard.vue`** 新建：单视图卡片。props: viewId/title/start/end/reloadKey。内部 `getAnalysisView(viewId)` 取配置 + `executeAnalysisView({view_id,params})` 取数据；chart_option/table 渲染逻辑从 [ViewRender.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/views/analytics/ViewRender.vue) 抽出复用；含独立 loading/错误态/重试/单卡刷新；watch(start,end,reloadKey) 重载。
3. **`web/src/views/dashboards/DashboardView.vue`** 新建：看板渲染页。
   - 顶部：看板名 + category 标签 + 时间范围选择器（近7/30/90天/默认）+ 刷新按钮 + description alert。
   - 主体：`el-row` + `el-col :span="item.span"` 渲染 `ViewItemCard`（visible 项）。
   - 时间范围：用户选 > 看板 time_range_default > 各视图自己的；"默认"档不传 start/end。
   - 路由参数推断 dashboard：用 `route.name` 映射 code（DashboardRefund→preset_dashboard_refund 等）或 `route.params.code`。
4. **`web/src/views/dashboards/Designer.vue`** 新建：看板配置页（数据中心）。参照 [ClassificationRules.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/views/system/ClassificationRules.vue) 的列表+dialog 模式。列表（code/name/category/items数/visibility/status/操作）+ 编辑 dialog（元信息表单 + items 编辑表：上移/下移/标题覆盖/span/可见/删除 + "添加视图"打开视图选择 dialog 多选）。编辑预设看板时 code/category disabled。
5. **`web/src/views/dashboards/Special.vue`** 新建：专项跟进页。顶部 el-select 切换 category=专项 的看板 + "配置专项看板"按钮跳 Designer；主体复用 `DashboardView` 组件（传 dashboardId）；无专项看板时 el-empty 提示。
6. **`web/src/router/index.js`** 新增路由（决策看板区，meta.manager）：
   - `dashboards/refund` → DashboardView（DashboardRefund）
   - `dashboards/complaint` → DashboardView（DashboardComplaint）
   - `dashboards/reception` → DashboardView（DashboardReception）
   - `dashboards/performance` → DashboardView（DashboardPerformance）
   - `dashboards/special` → Special
   - `dashboards/by-code/:code` → DashboardView（hidden，通用入口）
   - 数据中心区：`dashboards/designer` → Designer（meta.manager）
   - 旧 4 路由（analytics/operation 等）保留不删。
7. **`web/src/layouts/MainLayout.vue`**：
   - 数据中心子菜单加 `<el-menu-item index="/dashboards/designer">看板配置</el-menu-item>`。
   - 决策看板子菜单（加 `v-if="authStore.isManager"`）替换为：退款看板/投诉看板/接待看板/绩效看板/专项跟进 5 项。

## 复用的现有资产

- [analysis_engine.execute_view](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/analysis_engine.py) — 视图执行，看板 item 直接调 `/analysis-views/execute` 复用
- [preset_views_seed.PRESET_VIEWS](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/preset_views_seed.py) — 24 预设视图，看板 items 引用其 code
- [deps.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/deps.py) — CurrentUser/AdminOrSupervisor/DBSession
- [BaseChart.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/components/BaseChart.vue) — echarts 通用组件
- [ViewRender.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/views/analytics/ViewRender.vue) — chart_option/table 渲染逻辑抽出
- [ClassificationRules.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/views/system/ClassificationRules.vue) — CRUD 配置页模式
- [stores/auth.js](file:///Users/tanzk/dev-tmp/csctalon/web/src/stores/auth.js) — isManager/isAdmin 权限
- 路由守卫 [router/index.js L181-194](file:///Users/tanzk/dev-tmp/csctalon/web/src/router/index.js) — meta.admin/manager 已支持

## 分阶段

**本次 MVP（核心闭环）**：上述全部后端 8 项 + 前端 7 项。交付 4 常规预设看板可用 + 看板可配置 + 专项跟进页。

**后续 Phase 2**：服务端批量 execute 端点（ThreadPoolExecutor）；自定义日期范围；Designer 拖拽排序（vuedraggable）；ViewRender 重构为 ItemCard+配置详情；看板级 filters_override；旧 analytics API 标 include_in_schema=False。

**远期 Phase 3**：看板模板导出导入；看板订阅/邮件推送；分享链接；supervisor 本组数据隔离（引擎层 group filter）。

## 验证

### 后端
1. 迁移成功：`\d dashboards` 表存在；启动日志"预设看板注册完成: presets=4, created=4"。
2. `GET /dashboards`（admin token）返回 4 预设看板，items.view_id 均非 null。
3. `GET /dashboards`（agent token）返回空（visibility=manager 被过滤）。
4. visibility 403：agent 直接 `GET /dashboards/{id}` 返回 403。
5. 预设不可删：`DELETE /dashboards/{preset_id}` 返回 400。
6. item 引用有效：取退款看板某 view_id，`POST /analysis-views/execute {"view_id":X,"params":{"start":"2026-06-01","end":"2026-06-30"}}` 返回 chart 数据。

### 前端端到端
| 场景 | 预期 |
|---|---|
| admin 菜单 | 决策看板 5 项 + 数据中心有"看板配置" |
| agent 菜单 | 决策看板子菜单不显示 |
| 退款看板 | 10 图表并发加载，布局 16/8+12/12+24+12/12+8/8/8，默认近30天 |
| 切近7天 | 所有图表重载，x_axis 范围变 7 天 |
| 单卡刷新 | 仅该卡片重载 |
| 投诉看板 | 7 图表，结案率表格 span=24 整行 |
| 绩效看板 | 顶部 description 显示"仅展示操作量统计,非绩效得分" |
| Designer 新建 | 选 category=专项，加 2 视图 span 12/12，保存，专项跟进页可切换到它 |
| Designer 编辑预设 | code/category disabled |
| 专项跟进 | 下拉显示新建专项看板，选中后渲染 |
| agent 访问 /dashboards/refund | 守卫重定向 /dashboard |
| 旧页面兼容 | 直接访问 /analytics/operation 仍正常（旧 API 保留） |

### 数据对照
同时间范围下，新退款看板的 preset_refund_trend 数据与旧 Operation.vue 的 `/analytics/operation/trend` 应完全一致（preset_views_seed 已验证过引擎等价性）。

## 关键文件清单

**新建（后端 6）**：
- `backend/app/models/dashboard.py`
- `backend/app/schemas/dashboard.py`
- `backend/app/api/v1/dashboards.py`
- `backend/app/services/preset_dashboards_seed.py`
- `backend/alembic/versions/n4d5e6f7b8c9_add_dashboards.py`

**新建（前端 5）**：
- `web/src/api/dashboard.js`
- `web/src/components/ViewItemCard.vue`
- `web/src/views/dashboards/DashboardView.vue`
- `web/src/views/dashboards/Designer.vue`
- `web/src/views/dashboards/Special.vue`

**修改（5）**：
- `backend/app/models/__init__.py`（导入 Dashboard）
- `backend/app/api/v1/router.py`（注册 dashboards.router）
- `backend/app/main.py`（lifespan 加 ensure_preset_dashboards_registered）
- `web/src/router/index.js`（新增路由）
- `web/src/layouts/MainLayout.vue`（菜单调整）
