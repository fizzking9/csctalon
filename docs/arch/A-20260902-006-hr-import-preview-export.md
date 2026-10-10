# HR 数据导入预览/确认 + 导出

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context（为什么做）

用户定义了 HR 数据的正确工作流：① 支持文件导入**导出**；② 导入若需更新/合并，**先呈现视图、确认才合并**；③ 其他表单的人员名字只做归一、不改人员数据（上轮已满足）；④ 增加人员要慎重单独加、不随便刷新 HR 表（上轮已满足）。

当前 HR 导入走自定义 `/persons/import-snapshot`，**一把直接导入**，没有 diff 预览/确认步骤；且**没有导出**能力。本计划补齐第①②点：导出 + 导入预览/确认。

复用与约束：
- 现有 `compute_diff`（app/services/diff_preview.py:161）是**主键策略**（existing/new/abnormal），不适配 staff 的"别名/display_name 匹配 + SCD2 任职"语义 → 需专用预览函数。
- 现有 `upsert_staff_snapshot`（staff_import.py）的匹配逻辑已抽成 `_find_person_by_alias` / `_register_alias`，预览函数复用这些**只读**匹配、不写库。
- 导入格式列由 `data_parser.COLUMN_TEMPLATES["staff"]`（data_parser.py:183）定义：姓名/客服名/名称1/名称2/备注/专项/岗位/更新时间 + 生命周期列。导出对齐此格式以支持 round-trip。
- 无现成 xlsx 下载模式，新增标准 `StreamingResponse` + pandas `ExcelWriter`。

## 后端改动

### 1. app/services/staff_import.py — 新增 preview_staff_snapshot（只读 dry-run）
复用 `_find_person_by_alias`、`_to_date`、`apply_mapping`，**不写库**。对每行 HR 数据：
- 匹配：名称1→名称2→客服名 别名命中？→ 否则 display_name 命中（0/1/≥2）。
- 判定动作 `action`：
  - `conflict`：display_name 命中 ≥2 人（需人工归并，确认时跳过）
  - `new`：0 命中 → 将新建 person + 别名 + 任职 + 标确认
  - `update`：命中已有 person，且存在变更项之一（任职将新增/更新、有待加别名、未确认）
  - `skip`：命中已有 person，且无任何变更（同任职 + 别名齐全 + 已确认）
- `changes` 明细：`aliases_to_add[]`、`assignment`(new/update/skip)、`will_confirm`(bool)、`position_change`/`specialty_change`(old→new 或 null)、`matched_person_id`/`matched_name`。
- 返回 `{summary:{total,new,update,skip,conflict}, rows:[...]}`。

### 2. app/api/v1/staff_assignments.py — 新增预览 + 导出端点
- `POST /persons/import-snapshot/preview`：Body 同 import（stored_path/filename/sheet/encoding/column_mapping）。`load_dataframe` → `preview_staff_snapshot`，返回 diff（不建 UploadRecord、不写库）。权限 AdminOrSupervisor。
- `GET /persons/export`：导出 HR 主表 xlsx。pandas `ExcelWriter(io.BytesIO)` + `StreamingResponse(media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")`，`Content-Disposition: attachment; filename=人力资源数据_导出.xlsx`。
  - 列对齐 COLUMN_TEMPLATES["staff"]：姓名(display_name)/客服名/名称1/名称2/备注/专项/岗位/更新时间(当前任职 effective_from) + 生命周期四列。
  - 别名回填：按 `person_aliases.source_field`（名称1/名称2/客服名，source_table=staff_snapshot 优先）取值填入对应列；display_name→姓名。当前任职取 position/specialty。
  - 只导出 `is_active=true` 的 person。
  - 权限 CurrentUser（只读下载）。

### 3. app/schemas/staff_assignment.py — 新增 diff Pydantic 模型（可选）
`StaffSnapshotDiffRow` / `StaffSnapshotDiff`，供 `/preview` response_model 用（也可直接 R(dict)）。轻量起见用 dict 返回即可，不强求 schema。

## 前端改动

### 4. web/src/api/staffAssignment.js — 新增封装
- `previewSnapshot(payload)` → POST /persons/import-snapshot/preview
- `exportPersons()` → GET /persons/export（`responseType: 'blob'`）

### 5. web/src/views/system/Persons.vue — 导入插预览步 + 导出按钮
- 现有 importStep 0(上传解析)/1(列映射+确认)/2(结果) → 改为 0/1(列映射+**预览**)/2(**diff 表**+确认合并)/3(结果)。
  - 步1 按钮"确认导入"→改"预览"，调 `previewSnapshot`，结果存 `importDiff`，进 step 2。
  - 步2 渲染 diff 表：列 = 行号/姓名/匹配人员/动作(tag:新建绿/更新蓝/跳过灰/冲突红)/变更明细(将加别名X,Y｜任职:岗位A→B｜待确认)。顶部 summary 计数。冲突行标红提示"需在前端归并面板处理"。
  - 步2 按钮"确认合并"→ 调既有 `importSnapshot`（冲突行后端本就跳过）→ step 3 结果。
- 人员列表页头加"导出"按钮 → `exportPersons()` → 触发浏览器下载 blob。

## 验证

1. `docker compose build backend web && up -d backend web`。
2. 导出：浏览器点"导出"→ 下载 人力资源数据_导出.xlsx，打开核对 42 行、列头对齐导入格式、洪佳行 名称1=投诉-洪佳/名称2=随身WiFi-小洪/客服名=洪洪/岗位=客服/专项=投诉接待。
3. 导入预览：上传 人力资源数据.xlsx → 映射 → 预览，42 行 summary 应≈ update 0/skip 41/conflict 1(罗健非,因上轮已删空壳现应 skip)——实际以"已导入过的快照重导"为 skip 为主；点"确认合并"→ 后端幂等(0 insert/update)，结果 step 显示。
4. round-trip：用步骤2导出的 xlsx 重新导入预览 → 全部 skip（无变更），证明导出格式与导入对称。
5. 回归：点查询 `GET /persons/{id}/assignment-at` 仍正常；待归并别名面板不受影响。
