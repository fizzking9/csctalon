# 客服人员岗位/专项/分组 任职历史(SCD Type 2)+ 生命周期

## Context(为什么做)

现有 `人力资源数据.xlsx` 是一张**单时间点快照**(`姓名/客服名/名称1/名称2/备注/专项/岗位/更新时间`),
而 `Person` 模型只保存**当前状态**(`role/group_name/is_active`),无任何历史。

问题:
1. 岗位会调整、分组按周调、专项会变 —— 都是**时变维度**。同一客服上周是「接待」、本周转「投诉接待」,
   上周接待数据按当时专项核算,本周按新专项核算;单快照列无法还原"某天发生时他是什么岗位/专项"。
2. 入职/试用/转正/离职 是离散生命周期事件,当前完全没记录,无法做在职/流失/转正率分析。
3. 岗位/专项直接影响接待数据判断与绩效核算口径,必须能"按当时任职"关联历史业务数据。

目标:建一张 **SCD Type 2 任职历史表**(`staff_assignments`)记录岗位/专项/分组的有效期区间,
并在 `Person` 上补**生命周期字段**;提供 CRUD API + HR 快照 xlsx 导入 + 前端管理 UI;
让"按当时岗位/专项关联"在数据层可行(分析引擎的点查询 join 列为后续工作,本次仅交付 helper)。

用户确认要点:
- 名称1/名称2/客服名 是不同系统的登录/关联名,用于人员归一;同一系统内不同名也可能是同一人 ——
  这正是 `PersonAlias` + `alias_normalized` 归并机制,导入时必须把这些名注册为 alias。
- 本次范围:**数据层 + 管理UI + xlsx导入**(分析引擎点查询 join 推迟,仅留 helper API)。
- 生命周期录入:**xlsx导入支持可选列 + UI 手填**双通道。

---

## 设计决策(已与用户确认)

### 1. SCD2 单历史表 + 生命周期字段挂 Person
- 岗位/专项/分组 → `staff_assignments` 表,带 `effective_from/effective_to/is_current` 区间。
- 入职/试用/转正/离职 → `Person` 标量列(每人至多一次的离散事件,不进 SCD2)。

### 2. 当前岗位/专项/分组 镜像到 Person(ORM 层同步,不用 trigger/视图)
- `Person.position/specialty/group_name` 作为"当前镜像缓存",由 `create/update/delete_assignment`
  内部统一刷新(复用现有 `Person.group_name`,前端老代码读它仍可用)。
- 写入路径收口:`PersonUpdate` **移除** `group_name`/不加 `position/specialty` 写入字段 ——
  岗位/专项/分组变更只能走 `StaffAssignment` API,避免双写不一致。
- `MergeNewRequest` 保留 `group_name`(前端"归并到新建"对话框兼容),后端 `merge_new` 收到后改为
  建一条 `source='manual'` 的初始任职记录。

### 3. 唯一性:部分唯一索引 `WHERE is_current = true`
- 保证每人至多一条 current;与 `report_templates` 的 `uq_report_templates_default_per_type` 同款模式。
- 不用 EXCLUDE + daterange(需 btree_gist 扩展,项目无扩展依赖,引入成本高)。
- 区间重叠由 `create_assignment` 内应用层 SELECT 兜底校验。

### 4. HR Excel → Person 匹配(用户强调 alias 是归一关键)
匹配优先级(在 `staff_import.py`):
1. `名称1` 精确查 `PersonAlias.alias` → 命中即用
2. `名称2` 精确查 `PersonAlias.alias` → 命中即用
3. `客服名` 精确查 `PersonAlias.alias` → 命中即用
4. `姓名` 查 `Person.display_name`:
   - 0 命中 → 新建 Person(display_name=姓名)+ 注册 名称1/名称2/客服名 为 PersonAlias(`is_confirmed=false`)
   - 1 命中 → 用该 Person
   - ≥2 命中 → 记 `result.errors` 跳过(同名多人,绝不自动选第一个,防错并)

幂等规则(同份快照重复导入不重复):
- 同 person + 同 `effective_from` 的记录存在:
  - `(effective_from, position, specialty, group_name)` 完全一致 → 跳过(无变化)
  - 同 `effective_from` 但内容不同 → UPDATE 该行
- current assignment 内容不同且 `effective_from` 不同 → 关 current(`effective_to = new.effective_from - 1d`, `is_current=false`)+ 插新 current
- 无任何 assignment → 直接插为 current

### 5. 分析引擎点查询 join —— 本次不实现
`analysis_engine._execute_segment` 只对已注册 DataSource 物理表取列,不 JOIN Person。
本次仅交付 Python helper `get_assignment_at(db, person_id, date)` + REST `GET /persons/{id}/assignment-at`,
作为后续"虚拟人员维度 join"特性的接入点(需扩展引擎支持 fact table person 字段→StaffAssignment 按 time_field 落区间)。

### 6. 不注册 staff_assignments 为 DataSource
维度表非事实源,不进 `DATA_SOURCE_SEEDS`,不对分析引擎暴露。

---

## 实施步骤

### Phase 1 后端模型 + 迁移

**1.1** 新建 [staff_assignment.py](file:///Users/tanzk/dev/csctalon/backend/app/models/staff_assignment.py)
- `StaffAssignment(IDMixin, TimestampMixin, Base)`:
  - `person_id` FK→persons.id ondelete=CASCADE indexed
  - `effective_from: Date` not null indexed
  - `effective_to: Date` nullable
  - `is_current: Boolean` default False indexed
  - `position: String(32)` nullable
  - `specialty: String(64)` nullable
  - `group_name: String(64)` nullable
  - `change_reason: Text` nullable
  - `source: String(32)` default 'manual'
- `__table_args__`:
  - `Index("ix_staff_assignments_person_effective", "person_id", "effective_from", "effective_to")`
  - `Index("uq_staff_assignments_current_per_person", "person_id", unique=True, postgresql_where=text("is_current = true"))`

**1.2** 改 [person.py](file:///Users/tanzk/dev/csctalon/backend/app/models/person.py) — `Person` 加字段:
- `position` String(32) nullable(当前岗位镜像)
- `specialty` String(64) nullable(当前专项镜像)
- `hire_date/probation_end_date/regularization_date/resignation_date` Date nullable
- `employment_status` String(16) default 'active' not null(active/probation/regular/resigned,派生但存储)
- `group_name` 注释更新为"当前组(镜像自最新任职)"

**1.3** 改 [__init__.py](file:///Users/tanzk/dev/csctalon/backend/app/models/__init__.py) — import + `__all__` 加 `StaffAssignment`

**1.4** 新建迁移 [u0d1e2f3a4b5_add_staff_assignments.py](file:///Users/tanzk/dev/csctalon/backend/alembic/versions/u0d1e2f3a4b5_add_staff_assignments.py)
- `revision="u0d1e2f3a4b5"`,`down_revision="t9c0d1e2f3a4"`(当前 head)
- `op.create_table("staff_assignments", ...)` + ForeignKeyConstraint(ondelete CASCADE) + PrimaryKeyConstraint
- 索引:`ix_staff_assignments_person_id` / `ix_staff_assignments_effective_from` / `ix_staff_assignments_is_current` / `ix_staff_assignments_person_effective` / `uq_staff_assignments_current_per_person`(部分唯一)
- `op.add_column("persons", ...)` × 7 + `op.create_index("ix_persons_employment_status", ...)`
- `downgrade()` 严格反向

### Phase 2 后端 Schema

**2.1** 新建 [staff_assignment.py](file:///Users/tanzk/dev/csctalon/backend/app/schemas/staff_assignment.py)
- `StaffAssignmentOut(from_attributes)`:全字段
- `StaffAssignmentCreate`:effective_from(date 必填)/position/specialty/group_name/change_reason/source(默认 manual)/effective_to(可选)
- `StaffAssignmentUpdate`:effective_to/change_reason/position/specialty/group_name
- `StaffAssignmentAtOut`:同 Out 或 null

**2.2** 改 [person.py](file:///Users/tanzk/dev/csctalon/backend/app/schemas/person.py)
- `PersonOut` 加 position/specialty/hire_date/probation_end_date/regularization_date/resignation_date/employment_status
- `PersonUpdate`:**移除** `group_name`(决策 2);加 hire_date/probation_end_date/regularization_date/resignation_date/employment_status(均 Optional)
- `MergeNewRequest` 保留 `group_name`(前端兼容)

### Phase 3 后端服务层

**3.1** 新建 [staff_assignment_service.py](file:///Users/tanzk/dev/csctalon/backend/app/services/staff_assignment_service.py)
(db 始终第一参数,遵循 `_execute_upsert` 规则):
- `list_assignments(db, person_id)`:按 effective_from desc
- `create_assignment(db, person_id, payload, result=None)`:
  - 校验 effective_from;应用层 SELECT 同 person 区间相交记录兜底
  - 若 existing current.effective_from == new.effective_from → UPDATE 内容
  - 否则关旧 current(effective_to = new.effective_from - 1d, is_current=false)
  - 插新 current → `_apply_current_to_person` 镜像 → flush+commit
- `update_assignment(db, person_id, aid, payload)`:改非键字段;若改了 current 的 position/specialty/group_name 则同步镜像
- `delete_assignment(db, person_id, aid)`:删 current → 提升 effective_from 最大前一条为 current + 镜像;删后无 assignment 则 Person.position/specialty/group_name 置 null
- `get_assignment_at(db, person_id, date)`:WHERE effective_from<=date AND (effective_to IS NULL OR effective_to>=date) ORDER BY effective_from DESC LIMIT 1;无命中返回 None
- `_apply_current_to_person(db, person, assignment)`:镜像 position/specialty/group_name
- `recalculate_employment_status(person)`:按 resignation_date→regularization_date→probation_end_date 推导

**3.2** 新建 [staff_import.py](file:///Users/tanzk/dev/csctalon/backend/app/services/staff_import.py)
- `upsert_staff_snapshot(df, mapping, db, result)`:apply_mapping 取列 → `_to_datetime(snapshot_date).date()` → 按决策 4 优先级匹配 Person(alias 精确→display_name)→ 调 `create_assignment` 复用幂等/re-chain(避免重复实现)→ 同步 Person.remark
- 生命周期列(可选):若 mapping 含 hire_date/regularization_date 等且 df 有值,写回 Person

**3.3** 改 [data_parser.py](file:///Users/tanzk/dev/csctalon/backend/app/services/data_parser.py) — `COLUMN_TEMPLATES["staff"]`:
```
display_name←姓名, alias_kefu←客服名, alias_name1←名称1, alias_name2←名称2,
remark←备注, specialty←专项, position←岗位, snapshot_date←更新时间,
# 可选生命周期列(Excel 以后补列时自动识别):
hire_date←入职日期, probation_end_date←试用期到期, regularization_date←转正日期, resignation_date←离职日期
```

**3.4** 改 [uploads.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/uploads.py) — `DATA_TYPES` 加 `"staff"`(仅影响 /uploads/parse 的 gate 与 suggest_mapping,UPSERT_DISPATCH 不加)

### Phase 4 后端 API

**4.1** 新建 [staff_assignments.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/staff_assignments.py)
`router = APIRouter(prefix="/persons", tags=["系统-人员任职"])`:
- `GET /persons/{person_id}/assignments` — AdminOrSupervisor,返回 list[StaffAssignmentOut]
- `POST /persons/{person_id}/assignments` — AdminOrSupervisor,body=StaffAssignmentCreate
- `PUT /persons/{person_id}/assignments/{aid}` — AdminUser,body=StaffAssignmentUpdate
- `DELETE /persons/{person_id}/assignments/{aid}` — AdminUser
- `GET /persons/{person_id}/assignment-at?date=YYYY-MM-DD` — CurrentUser,返回 StaffAssignmentAtOut 或 null
- `POST /persons/import-snapshot` — AdminUser,body={stored_path, sheet_name, encoding, column_mapping}:
  - `load_dataframe` → 建 UploadRecord(data_type="staff_snapshot", source="manual") → `upsert_staff_snapshot` → 更新统计 + status → 返回 R[ImportResponse]

**4.2** 改 [persons.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/persons.py)
- `update_person`:写完后若生命周期日期变更,调 `recalculate_employment_status(p)` 重算写回
- `merge_new`:建 Person 后若 group_name 非空,调 `create_assignment(db, p.id, StaffAssignmentCreate(effective_from=date.today(), group_name=..., source='manual'))`

**4.3** 改 [router.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/router.py) — include staff_assignments.router

### Phase 5 前端

**5.1** 新建 [staffAssignment.js](file:///Users/tanzk/dev/csctalon/web/src/api/staffAssignment.js)
listAssignments/createAssignment/updateAssignment/deleteAssignment/getAssignmentAt/importSnapshot

**5.2** 改 [Persons.vue](file:///Users/tanzk/dev/csctalon/web/src/views/system/Persons.vue) 详情抽屉:
- `el-descriptions` 加:岗位/专项/入职日期/试用期到期/转正日期/离职日期/在职状态(el-tag)+ 生命周期编辑入口
- 新增"任职历史"区段:`el-table`(effective_from/effective_to/position/specialty/group_name/source/is_current tag)+ 编辑/删除操作
- "新增任职记录"`el-button`→`el-dialog`:el-date-picker(effective_from)、el-select(position:客服/组长/主管)、el-select(specialty:接待/投诉接待/主管)、el-input(group_name/change_reason)
- 顶部"导入人力快照"`el-button`:复用 `/uploads/parse`(data_type='staff')→ 显示 suggest_mapping → `/persons/import-snapshot`

**5.3** "归并到新建"对话框:保留"组"输入;提交后静默调 createAssignment 写初始任职

**5.4** 不新增路由/菜单(Persons.vue 已在 `/system/persons`,meta admin:true)

### Phase 6 验证

**6.1 后端**
- 启动应用:lifespan 自动 `alembic upgrade head`,确认表/索引创建
- psql `\d staff_assignments` 见 `uq_staff_assignments_current_per_person` 带 `WHERE is_current = true`
- 同 person_id 插第二条 is_current=true → 唯一约束报错
- `POST /persons/{id}/assignments` 两次(不同 effective_from)→ 第一条 effective_to=effective_from-1, is_current=false
- `GET /persons/{id}/assignment-at?date=...` → 返回正确区间记录
- 删 current → 前一条提升为 current + Person 镜像刷新
- 导入 HR 样例 xlsx → 别名匹配/新建 Person/幂等再导入不重复
- 多人同名场景:两行同名不同别名 → 第二次无别名匹配进 errors

**6.2 前端**
- 详情抽屉正确展示任职历史 + 生命周期
- 新增/编辑/删除任职记录后镜像刷新
- "导入人力快照"端到端跑通

**6.3 回归**
- `person_extractor` 自动建 Person 仍正常(无 assignment 不报错)
- `analysis_engine` 不受影响(Person 不在 _MODEL_REGISTRY;DATA_TYPES 仅多 'staff',UPSERT_DISPATCH 未变)
- 现有各 data_type 导入流程不受影响

---

## 关键文件清单

新建:
- [backend/app/models/staff_assignment.py](file:///Users/tanzk/dev/csctalon/backend/app/models/staff_assignment.py)
- [backend/app/schemas/staff_assignment.py](file:///Users/tanzk/dev/csctalon/backend/app/schemas/staff_assignment.py)
- [backend/app/services/staff_assignment_service.py](file:///Users/tanzk/dev/csctalon/backend/app/services/staff_assignment_service.py)
- [backend/app/services/staff_import.py](file:///Users/tanzk/dev/csctalon/backend/app/services/staff_import.py)
- [backend/app/api/v1/staff_assignments.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/staff_assignments.py)
- [backend/alembic/versions/u0d1e2f3a4b5_add_staff_assignments.py](file:///Users/tanzk/dev/csctalon/backend/alembic/versions/u0d1e2f3a4b5_add_staff_assignments.py)
- [web/src/api/staffAssignment.js](file:///Users/tanzk/dev/csctalon/web/src/api/staffAssignment.js)

修改:
- [backend/app/models/person.py](file:///Users/tanzk/dev/csctalon/backend/app/models/person.py)
- [backend/app/models/__init__.py](file:///Users/tanzk/dev/csctalon/backend/app/models/__init__.py)
- [backend/app/schemas/person.py](file:///Users/tanzk/dev/csctalon/backend/app/schemas/person.py)
- [backend/app/services/data_parser.py](file:///Users/tanzk/dev/csctalon/backend/app/services/data_parser.py)
- [backend/app/api/v1/uploads.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/uploads.py)
- [backend/app/api/v1/persons.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/persons.py)
- [backend/app/api/v1/router.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/router.py)
- [web/src/views/system/Persons.vue](file:///Users/tanzk/dev/csctalon/web/src/views/system/Persons.vue)

## 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| PersonUpdate 移除 group_name 致老前端 422 | 中 | 前后端同 PR 发布;merge_new 保留 group_name;axios 拦截器已 flatten 422 |
| HR 名称1/2 在业务表从未出现 → alias 匹配失败 | 高 | display_name 兜底 + 新建 Person + 注册 alias;下次 person_extractor 跑时 alias 已存在不重建 |
| 多人同名错并 | 高 | 多命中进 errors 跳过,UI 提示人工归并 |
| 镜像字段与历史不一致(直改 DB) | 低 | ORM 层是唯一写入入口 |
| 老数据 Person 有 group_name 无 assignment | 低 | 不回填;UI 显示"无任职记录";后续可单独脚本 source='legacy' 回填(本次不做) |

## 明确推迟项(文档化)

- **分析引擎点查询 join**:本次不实现 `cs_records.operator→PersonAlias→Person→StaffAssignment WHERE operation_time BETWEEN effective_from AND COALESCE(effective_to, now)`。仅交付 `get_assignment_at` Python helper + `GET /persons/{id}/assignment-at` REST 端点,作为后续引擎"虚拟人员维度 join"接入点。
- **老 Person.group_name 回填为 legacy assignment**:不在本次范围。
