# `_pending/` 待审核设计文档 — 锁定候选清单

**生成日期**：2026-09-01
**说明**：本目录存放从 `.trae/documents/` 迁移的 20 份历史设计文档。以下按 2026-09-01 代码核对结果列出**锁定候选**，供**人工终审**使用。

> ⚠️ **锁定流程（必须人工执行）**：
> 1. 人工逐份审核，确认文档与实际实现一致
> 2. 按 `docs/arch/README.md` 规范重命名（`A-YYYYMMDD-序号-简述.md`）
> 3. 文件头标注 `状态: LOCKED（v1.0）`，移到 `docs/arch/` 根目录
> 4. 打 git tag：`arch/A-YYYYMMDD-序号`
>
> **AI 不代为锁定**（人机职责边界：架构锁定归人）。

## 核对结果（2026-09-01）

| # | 文档 | 实现状态 | 核对依据 | 建议 |
|---|---|---|---|---|
| 1 | P0-stage1-analysis-engine-foundation.md | ✅ 已实现 | 数据源注册/diff预览/人员别名/按月归档 | 可锁定 |
| 2 | P0-stage2-analysis-engine-executor.md | ✅ 已实现 | 引擎执行器/24预设视图/动态分类规则 | 可锁定 |
| 3 | complaint-and-specialty-improvements.md | ✅ 已实现 | 专项下拉/三饼图/三源处理人分布 | 可锁定 |
| 4 | dashboard-connectivity-and-batch.md | ✅ 已实现 | 视图↔看板流转/批量执行/time_grain | 可锁定 |
| 5 | dashboard-system-rebuild.md | ✅ 已实现 | Dashboard模型/4预设看板/权限双轨 | 可锁定 |
| 6 | editable-report-kanban.md | ✅ 已实现 | 报告模板/框架编辑器 | 可锁定 |
| 7 | export-feature-design.md | ✅ 已实现 | 文档自标"占位未实现"但多模块已落地导出 | **需人工复核**（状态过时） |
| 8 | hr-import-preview-export.md | ✅ 已实现 | 导入预览/HR主表导出 | 可锁定 |
| 9 | metric-alerting-notification-design.md | ✅ 已实现 | 指标/预警/通知通道 | 可锁定 |
| 10 | person-hr-master-decouple.md | ✅ 已实现 | 别名待归并/上传自动归并 | 可锁定 |
| 11 | persons-page-layout-refactor.md | ✅ 已实现 | 左右分栏/批量编辑 | 可锁定 |
| 12 | refund-cashflow-views-rebuild.md | ✅ 已实现 | 引擎能力/宽表/审计时序 | 可锁定 |
| 13 | refund-dashboard-redesign.md | 🟡 部分实现 | 功能落地但 Tab 组织方式不同（2 Tab vs 设计 5 Tab） | **需人工复核**（结构偏差） |
| 14 | staff-assignment-history.md | ✅ 已实现 | SCD2任职历史/生命周期 | 可锁定 |
| 15 | time-aware-specialty-workload.md | ✅ 已实现 | 按天加权平均系数 | 可锁定 |
| 16 | 商户号款项全量导入方案.md | ✅ 已实现 | 全量保序/主键降级/分批插入 | 可锁定 |
| 17 | 商户号现金流视图修正方案.md | ✅ 已实现 | 现金流趋势v4/五路核算 | 可锁定 |
| 18 | 日期显示压紧修订.md | ✅ 已实现 | 后端YY/MM/DD+前端formatDate | 可锁定 |
| 19 | 时间字段统计规范修订.md | ✅ 已实现 | SYS四写法排除/不可靠字段隐藏 | 可锁定 |
| 20 | 趋势视图时间粒度切换.md | ✅ 已实现 | 日/周/月切换器 | 可锁定 |

## 人工终审优先级建议

- **优先锁定（第一批，5 份）**：`dashboard-system-rebuild`、`refund-cashflow-views-rebuild`、`staff-assignment-history`、`metric-alerting-notification-design`、`时间字段统计规范修订`——对应核心已实现模块，后续开发引用最多。
- **需先修订再锁定（2 份）**：`export-feature-design`（状态过时）、`refund-dashboard-redesign`（结构与实现偏差）。
- **其余 13 份**：实现状态均确认，可批量审核锁定。

## 锁定后动作

- 移入 `docs/arch/` 根目录，命名 `A-YYYYMMDD-序号-简述.md`
- 打 git tag：`git tag arch/A-20260901-001`（按实际锁定序号递增）
- 更新本 README，移除已锁定项
