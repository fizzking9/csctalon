# 人员管理页面布局重构方案

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context(为什么改)

当前 [Persons.vue](file:///Users/tanzk/dev/csctalon/web/src/views/system/Persons.vue) 存在三类体验问题(用户反馈):

1. **布局浪费、操作不便**:主区是宽表格(10 列)+ 详情用 70% 抽屉。抽屉里 `el-descriptions column=2` 在 70% 宽度下大量横向空白;待归并别名面板始终展开占满纵向空间,即便不处理也挤占视野。
2. **可编辑性差**:部门/角色/在职状态等只能通过单独的"编辑生命周期"dialog 改,且部门/角色还没在编辑表单里;岗位/专项要另开"任职记录"dialog。没有 inline 编辑,没有批量编辑。
3. **详情数据密度低**:13 个字段用 2 列 descriptions 排在 70% 抽屉里,信息稀疏,大页面留白多。

目标:改成**左右分栏 master-detail** 布局(左侧列表常驻 + 右侧详情/编辑常驻,淘汰抽屉),待归并别名折叠,详情 inline 可编辑 + 批量编辑,提高数据密度。用户已确认布局方向=左右分栏,批量编辑字段=部门/角色/在职状态/启用确认/入职时间(分组/专项走任职记录批量建)。

## 总体布局

```
顶部工具栏(导出/导入/提取/重建/纳入店铺/纳入企微/归并建议)            ← 保留,横排
┌─────────────────────────────────────────────────────────────┐
│ 待归并别名 (el-collapse,默认折叠只显示计数,展开看表格+批量归并)   │ ← 折叠
├──────────────────────────┬──────────────────────────────────┤
│ 左栏 38%(人员列表)        │ 右栏 62%(详情/编辑,常驻)          │
│  筛选: status + 表头筛选   │  顶部:显示名(可编辑) + 部门tag +     │
│  工具栏: [批量编辑 N]      │        操作(保存/确认/停用)         │
│  el-table 紧凑(checkbox    │  基本信息 el-form inline 可编辑:     │
│   + 显示名/部门/角色/在职   │    部门select 角色select 在职select │
│   状态,行点击选中)         │    入职/试用期/转正/离职 date        │
│  分页                      │    备注 input + 启用switch           │
│                            │  任职历史(紧凑表 + 新增/编辑 dialog)│
│                            │  别名列表(出现位置 tag 群)          │
└──────────────────────────┴──────────────────────────────────┘
```

- **左栏**:行点击(非 checkbox)→ 选中该人员(高亮),右栏立即显示详情;checkbox → 批量选择(不切换右栏)。
- **右栏**:常驻,无选中时显示占位提示。淘汰 el-drawer。
- **待归并别名**:el-collapse 折叠,header 显示计数,展开后保留现有表格 + 批量按建议归并/归并新建/归并已有。

## 后端改动

### 新增 `POST /persons/batch-update`(Admin)
[persons.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/persons.py) 加端点:
```python
class BatchUpdateRequest(BaseModel):
    ids: list[int]
    department: str | None = None
    role: str | None = None
    employment_status: str | None = None
    is_active: bool | None = None
    is_confirmed: bool | None = None
    hire_date: date | None = None
    group_name: str | None = None       # → 批量建任职记录
    specialty: str | None = None        # → 批量建任职记录
```
逻辑:
- Person 原生字段(department/role/employment_status/is_active/is_confirmed/hire_date):`UPDATE persons SET <非空字段> WHERE id IN (ids)`,跳过未提供的(None)。
- 分组/专项(group_name/specialty 任一非空):对每个 id 调 `create_assignment(db, pid, StaffAssignmentCreate(effective_from=今天, group_name, specialty, source="manual"))`(复用 [staff_assignment_service.py](file:///Users/tanzk/dev/csctalon/backend/app/services/staff_assignment_service.py))。
- 返回 `{updated: N, assignments_created: M}`。
- schema 加 `BatchUpdateRequest`(放 [person.py](file:///Users/tanzk/dev/csctalon/backend/app/schemas/person.py))。

### 现有端点复用
- inline 编辑:复用 `PUT /persons/{id}`(已支持 department/role/employment_status/hire_date 等字段经 setattr)。
- 任职历史新增/编辑:复用 StaffAssignment API。

## 前端改动(Persons.vue 重构)

### 1. 主区改左右分栏(flex)
- 外层 `display:flex; gap:12px; height: calc(100vh - <顶部高度>)`
- 左栏 `width:38%` + 右栏 `flex:1`,各自 `overflow:auto`
- 删除 `el-drawer` 详情抽屉,改为右栏常驻 `<div>` 区。

### 2. 待归并别名折叠
- `el-card` → `el-collapse`(单 item),`v-model` 绑定展开状态(默认 `['']` 收起或 `[]`)
- header 显示 `待归并别名 ({{ count }})`,展开后内容不变(表格 + 3 个批量按钮)
- 无待归并别名时隐藏整个 collapse。

### 3. 左栏列表精简
- 列精简为:checkbox / 显示名 / 部门(tag) / 角色 / 在职状态(tag) / 操作(确认归并/停用)
  - 去掉别名数/岗位/专项/确认/状态/详情按钮(这些移到右栏;行点击看详情,不再需要"详情"按钮)
- 保留表头排序(ID 可去,或留)/筛选(部门/角色/在职状态 filters 保留)+ status radio
- 行 `@row-click="selectPerson"` 选中(`highlight-current-row` + `current-row-key`),右栏加载详情
- checkbox `@selection-change` 维护 `selectedIds`,左栏工具栏显示「批量编辑(N)」按钮(选中≥1 启用)

### 4. 右栏详情 inline 编辑
- 顶部:显示名 `el-input`(inline)+ 部门 tag + 「保存」「确认归并」「停用/启用」按钮
- 基本信息:`el-form` 网格(label-position=top,2-3 列),字段 inline 可编辑:
  - 部门 `el-select`(企微客服部/店铺客服部 + allow-create)
  - 角色 `el-select`(客服/主管/管理员)
  - 在职状态 `el-select`(在职/试用期/转正/离职)
  - 入职日期 / 试用期到期 / 转正日期 / 离职日期 `el-date-picker`
  - 备注 `el-input`
  - 启用 `el-switch`
- 「保存」→ 调 `updatePerson(id, form)`(PUT)
- 任职历史:紧凑表格(保留现有列)+ 新增/编辑 dialog(复用现有 assignmentDialog)
- 别名列表:保留(出现位置 tag 群),复用现有

### 5. 批量编辑 dialog
- 左栏选中 ≥1 → 点「批量编辑」→ dialog:
  - checkbox 勾选要改的字段(部门/角色/在职状态/启用/确认/入职日期/分组/专项)
  - 勾选的字段显示对应输入控件
  - 确认 → 调 `batchUpdatePersons(ids, fields)` → 成功提示 + 重新加载
- person.js 加 `batchUpdatePersons(payload)`

### 6. 删除/简化
- 删除"编辑生命周期"dialog(inline 编辑取代)
- 保留:归并新建/归并已有/归并建议/任职新增编辑/导入预览/批量结果 dialog

## 关键文件

| 文件 | 改动 |
|---|---|
| [backend/app/api/v1/persons.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/persons.py) | 加 `POST /persons/batch-update` 端点 |
| [backend/app/schemas/person.py](file:///Users/tanzk/dev/csctalon/backend/app/schemas/person.py) | 加 `BatchUpdateRequest` |
| [web/src/views/system/Persons.vue](file:///Users/tanzk/dev/csctalon/web/src/views/system/Persons.vue) | 布局重构(分栏/折叠/inline/批量)+ 删抽屉+编辑生命周期dialog |
| [web/src/api/person.js](file:///Users/tanzk/dev/csctalon/web/src/api/person.js) | 加 `batchUpdatePersons` |

## 验证

1. 重建 backend + web:`docker compose build backend web && up -d`
2. 布局:页面左右分栏,左栏点人员右栏立即显示;待归并别名默认折叠,点开看表格。
3. inline 编辑:右栏改部门/角色/在职状态/入职日期/备注 → 保存 → 刷新确认持久化。
4. 批量编辑:左栏勾选多人 → 批量编辑 → 改部门+在职状态 → 确认 → 查 DB 确认 UPDATE;勾分组/专项 → 确认任职历史新增记录。
5. 排序/筛选:左栏表头排序/筛选仍生效(后端)。
6. 回归:归并建议/纳入店铺/纳入企微/导出导入仍正常。
