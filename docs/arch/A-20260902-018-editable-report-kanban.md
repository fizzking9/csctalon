# 可编辑报告看板（ReportTemplate）实现计划

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context

当前周报看板（WeeklyReport）使用**固定10项框架**（`WEEKLY_FRAMEWORK_SECTIONS` 硬编码），`_normalize_framework` 强制对齐这10项，无法增删/调整顺序。专项报告（Report）只有 snapshot JSON，没有看板体验。对照实际周报PDF还发现"组长培训记录"等内容无法加入。

**目标**：把"固定框架"升级为"基于模板的可编辑看板"，适用于周报/专项报告/审计报告等多种类型。管理员维护模板库，一键生成时从默认模板复制 framework，之后可自由编辑（增删section、改标题、调顺序）。现有10项自动成为"周报默认模板"种子，兼容现有数据。

## 核心设计

### 1. ReportTemplate 模型（新建 `backend/app/models/report_template.py`）
```
ReportTemplate:
  code(唯一,预设用如 preset_template_weekly) / name / description
  report_type(weekly/special_audit/custom)
  framework JSONB: [{key, title, section_type, view_ids[], owner_id, summary_placeholder}]  ← 模板侧无data/summary
  is_default(该类型默认模板,部分唯一索引) / is_preset(预设不可删)
  status(active/archived) / visibility(public/manager/admin) / owner_id / sort_order
```
模板的 section 不含 `data`/`summary`/`related_report_ids`（实例数据）；实例化时由 `instantiate_framework(template)` 填充空值。

### 2. _normalize_framework 改造（`backend/app/api/v1/weekly_reports.py`）
从"强制对齐10项"改为"保留用户自定义 + 字段补齐 + 脏数据清洗"。不再引用 `WEEKLY_FRAMEWORK_SECTIONS`，保留用户增删的 section 和顺序。兼容旧格式 view_id→view_ids。

### 3. Report 加 framework 字段（`backend/app/models/report.py`）
nullable=True，兼容现有无 framework 的 Report。confirm 流程双模式：
- **看板模式**（有 framework）：固化各 section.data 快照，自动派生轻量 snapshot
- **纯快照模式**（无 framework）：向后兼容，confirm 必须传 snapshot

### 4. WeeklySectionBinding 合并到模板
binding 的 view_ids/owner_id/section_key 与 template.framework 同构，合并减少配置点。保留表结构（不删，避免回滚风险），`/bindings` API 转为 deprecated 代理读写默认模板。

### 5. 前端看板编辑器（新建 `web/src/components/FrameworkEditor.vue`）
**排序用上下移按钮**（不引入拖拽库，项目依赖克制，section数量少够用）。每个 section 一张 el-card：标题输入 + section_type 下拉 + 关联视图多选 + 负责人 + 摘要 + ↑↓+× 操作按钮。草稿态可编辑，确认后只读。

### 6. 模板管理页（新建 `web/src/views/management/ReportTemplates.vue`）
列表（名称/类型/默认/预设/状态/操作）+ 编辑dialog（复用 FrameworkEditor）+ 设为默认 + 复制 + 删除(预设禁删)。

## 实现步骤

### 阶段A：后端
1. **A1** 新建 `report_template.py` 模型 + `__init__.py` 注册
2. **A2** 新建迁移 `t9c0d1e2f3a4_report_templates.py`：建表 + reports加framework + 数据迁移(10项+binding合并写入周报默认模板)
3. **A3** 新建 `preset_templates_seed.py` 种子(周报默认模板+专项审计模板)，main.py lifespan 注册
4. **A4** 新建 `report_templates.py` API(CRUD+copy+set-default+default+framework)，router.py 注册
5. **A5** 改造 `weekly_reports.py`：_normalize_framework保留自定义 / generate读默认模板 / bindings转代理 / drill-report套special_audit模板
6. **A6** 改造 `reports.py`：serialize加framework / create+update支持framework / confirm支持看板固化+派生snapshot
7. **A7** 改造 `report.py`：加framework字段

### 阶段B：前端
8. **B1** 新建 `web/src/api/report-template.js`
9. **B2** 新建 `web/src/components/FrameworkEditor.vue`（核心组件）
10. **B3** 改造 `WeeklyDetail.vue`：framework渲染替换为FrameworkEditor + 套模板按钮
11. **B4** 改造 `WeeklyList.vue`：绑定配置dialog改为默认模板快捷入口
12. **B5** 新建 `ReportTemplates.vue` 模板管理页
13. **B6** 改造 `ReportDetail.vue`：增加framework看板区域 + confirm流程适配
14. **B7** `router/index.js` 注册 `/management/templates` 路由 + `MainLayout.vue` 加菜单项

### 阶段C：验证
15. **C1** 重建容器（build backend && build web）
16. **C2** API验证：模板CRUD → 一键生成(从模板) → 增删section → confirm固化 → 专项下钻套模板
17. **C3** 兼容验证：旧周报(10项)正常打开/编辑/确认
18. **C4** 浏览器验证：模板管理页 → 周报看板编辑 → 专项报告看板

## 关键文件清单

**后端新建**：
- `backend/app/models/report_template.py`
- `backend/app/api/v1/report_templates.py`
- `backend/app/services/preset_templates_seed.py`
- `backend/alembic/versions/t9c0d1e2f3a4_report_templates.py`

**后端改造**：
- `backend/app/api/v1/weekly_reports.py`（_normalize_framework/generate/bindings/drill-report）
- `backend/app/api/v1/reports.py`（serialize/create/update/confirm）
- `backend/app/models/report.py`（加framework字段）
- `backend/app/models/__init__.py` + `backend/app/api/v1/router.py` + `backend/app/main.py`

**前端新建**：
- `web/src/components/FrameworkEditor.vue`
- `web/src/api/report-template.js`
- `web/src/views/management/ReportTemplates.vue`

**前端改造**：
- `web/src/views/management/WeeklyDetail.vue`（套FrameworkEditor）
- `web/src/views/management/WeeklyList.vue`（模板入口）
- `web/src/views/management/ReportDetail.vue`（看板区域）
- `web/src/router/index.js` + `web/src/layouts/MainLayout.vue`（路由+菜单）

## 复用现有模式
- 种子注册：参考 `preset_views_seed.py` 的 `ensure_view_registered` 按 code upsert 幂等模式
- 可见性过滤：参考 `dashboards.py` 的 `_allowed_visibility` 模式
- 视图渲染：复用 `ViewItemCard.vue`（props: viewId/title/start/end/prefetchedResult）
- 注释：复用 `ReportCommentTimeline.vue`

## 风险与兼容
- 旧周报 framework 保留原样（新 normalize 不丢弃 section）
- `/bindings` API 保留为 deprecated 代理（兼容旧前端）
- 种子仅更新 is_preset=true 模板的 framework，不强制覆盖 is_default
- Report.framework 可选，无 framework 时退回纯 snapshot 模式
- 模板删除为软删(status=archived)，实例 framework 是副本不反向依赖模板

## 验证方式
1. `docker compose build backend && up -d backend`：检查迁移执行(建表+种子)
2. curl 全链路：模板CRUD → 一键生成 → 编辑framework → confirm → 专项下钻
3. `docker compose build web && up -d web`：浏览器验证模板管理页+看板编辑器
4. 旧周报(#7)打开验证兼容性
