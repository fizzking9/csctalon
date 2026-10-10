# Group/Specialty 双体系设计

> 版本：v1.0 · 日期：2026-08-17
> 关联代码：[group.py](../backend/app/models/group.py) / [specialty.py](../backend/app/models/specialty.py)
> 关联 API：[groups.py](../backend/app/api/v1/groups.py) / [specialties.py](../backend/app/api/v1/specialties.py)
> 关联 migration：[n1b2c3d4e5f6_add_groups_and_specialties_tables.py](../backend/alembic/versions/n1b2c3d4e5f6_add_groups_and_specialties_tables.py)

## 一、设计动机

客服中心存在两个正交的管理维度，过去混在 `Person.specialty` 单字段中无法区分：

| 维度 | Group（行政分组） | Specialty（业务专项） |
|---|---|---|
| 性质 | 组织管理维度 | 业务内容维度 |
| 用途 | 考勤、绩效、日常管理 | 工作量统计、效率分析 |
| 示例 | 董梦玲组、叶珍凤组、张蕾组 | 咨询接待、套餐专项、退款挽回 |
| 特点 | 一人一组，组长管理 | 一人可多专项，可跨组 |
| 变更频率 | 低（行政调整） | 中（业务调整） |

**核心问题**：同一人可能属于"董梦玲组"（行政），但负责"咨询接待+套餐专项"（业务）；同一"退款挽回"专项可能跨董梦玲组和朱海洋组的人员。

## 二、数据模型

### 2.1 Group 体系（3 张表）

#### groups — 行政分组定义

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BigInteger PK | |
| name | String(64) UNIQUE | 组名，如"董梦玲组" |
| department | String(32) | 所属部门：企微/店铺/研发 |
| description | Text | 备注 |
| is_active | Boolean | 是否启用 |
| display_order | Integer | 显示排序 |
| current_leader_id | FK→persons.id | 当前组长（冗余镜像） |
| current_deputy_leader_id | FK→persons.id | 当前副主管（冗余镜像） |
| created_at / updated_at | DateTime | |

#### group_leadership_history — 组长变更历史（SCD Type 2）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BigInteger PK | |
| group_id | FK→groups.id CASCADE | |
| person_id | FK→persons.id | 被任命人 |
| role | String(16) | leader=组长 / deputy=副主管 |
| effective_from | Date | 生效日期 |
| effective_to | Date NULL | 失效日期（NULL=至今） |
| is_current | Boolean | 是否当前任职 |
| change_reason | Text | 变更原因 |
| source | String(32) | manual / xlsx_import |
| created_at / updated_at | DateTime | |

**索引**：group_id / person_id / is_current / (group_id, role, is_current) 复合索引

#### person_group_memberships — 人员分组归属历史（SCD Type 2）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BigInteger PK | |
| person_id | FK→persons.id CASCADE | |
| group_id | FK→groups.id CASCADE | |
| effective_from | Date | 入组日期 |
| effective_to | Date NULL | 离组日期（NULL=至今） |
| is_current | Boolean | 是否当前归属 |
| change_reason | Text | 入组/调组/离组 |
| source | String(32) | |
| created_at / updated_at | DateTime | |

**索引**：person_id / group_id / is_current / (person_id, is_current) 复合索引

### 2.2 Specialty 体系（3 张表）

#### specialties — 业务专项定义

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BigInteger PK | |
| code | String(32) UNIQUE | 编码：consulting/complaint 等 |
| name | String(64) UNIQUE | 专项名称 |
| description | Text | |
| difficulty_weight | Float | 难度系数（1.0/1.8/2.0/2.2/3.0/5.0） |
| is_temporary | Boolean | 是否临时专项 |
| is_active | Boolean | |
| display_order | Integer | |
| valid_from / valid_to | Date | 专项生效/结束日期 |
| current_lead_id | FK→persons.id | 当前负责人 |
| current_deputy_lead_id | FK→persons.id | 当前副负责人 |
| created_at / updated_at | DateTime | |

#### specialty_leadership_history — 专项负责人变更历史（SCD Type 2）

结构同 `group_leadership_history`，将 group_id 替换为 specialty_id，role 为 lead/deputy。

#### person_specialty_assignments — 人员专项分配历史（SCD Type 2）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BigInteger PK | |
| person_id | FK→persons.id CASCADE | |
| specialty_id | FK→specialties.id CASCADE | |
| is_primary | Boolean | 是否主专项（true=主/false=辅） |
| effective_from | Date | 分配生效日期 |
| effective_to | Date NULL | 分配结束日期 |
| is_current | Boolean | |
| change_reason | Text | |
| source | String(32) | |
| workload_ratio | Float | 工作量占比（默认 1.0） |
| created_at / updated_at | DateTime | |

**索引**：person_id / specialty_id / is_current / (person_id, is_current) / (specialty_id, is_current) 复合索引

## 三、6 个标准专项种子数据

migration 中自带种子数据，首次建表自动插入：

| code | name | difficulty_weight | display_order |
|---|---|---|---|
| consulting | 咨询接待 | 1.0 | 1 |
| package | 套餐专项 | 1.8 | 2 |
| refund_processing | 退款处理 | 2.0 | 3 |
| refund_recovery | 退款挽回 | 2.2 | 4 |
| internal_complaint | 内部投诉 | 3.0 | 5 |
| external_complaint | 外部投诉 | 5.0 | 6 |

使用 `ON CONFLICT (name) DO NOTHING` 保证幂等。

## 四、SCD Type 2 历史追溯

### 4.1 设计模式

所有人员归属变更均采用 SCD Type 2（Slowly Changing Dimension）：

```
人员A从 8月1日 起在 董梦玲组，8月15日 调到 叶珍凤组

记录1: effective_from=08-01, effective_to=08-14, is_current=false
记录2: effective_from=08-15, effective_to=NULL,   is_current=true
```

### 4.2 查询模式

```sql
-- 查某人当前归属
SELECT * FROM person_group_memberships
WHERE person_id = ? AND is_current = true;

-- 查某人在某天的归属
SELECT * FROM person_group_memberships
WHERE person_id = ?
  AND effective_from <= '2026-08-10'
  AND (effective_to IS NULL OR effective_to >= '2026-08-10');
```

### 4.3 变更操作

调岗时需在事务中完成：
1. 旧记录 `effective_to = 昨天`，`is_current = false`
2. 新记录 `effective_from = 今天`，`is_current = true`
3. 更新 `groups.current_leader_id` 等冗余镜像字段

## 五、与人均效率的关联

`person_specialty_assignments` 是人均效率算法的核心数据源：

- 按天加权：查询范围内每天的专项分配，按天数加权平均系数
- 本职/转接判定：当天登记的专项 = 本职（系数 1.0），未登记的 = 转接（系数 0.2）
- SCD 无数据回退：`Person.specialty` 字段作为默认全天专项

详见 [人均效率算法说明.md](../standards/人均效率算法说明.md)。

## 六、冗余镜像字段设计

`groups.current_leader_id` 和 `specialties.current_lead_id` 是冗余字段，避免每次查询都 JOIN 历史表。

**维护方式**：变更历史记录时同步更新镜像字段（在 service 层保证事务一致性）。

## 七、5 个行政分组

| 组名 | 部门 | 主管 |
|---|---|---|
| 董梦玲组 | 店铺 | 张蕾（总主管） |
| 吴晓龙组 | 店铺 | 张蕾 |
| 叶珍凤组 | 企微 | 张蕾 |
| 朱海洋组 | 店铺 | 张蕾 |
| 张蕾组 | 店铺 | 张蕾（兼任） |

> 张蕾为总主管， overseeing 所有 5 个组。

## 八、前端交互

### 8.1 人员管理页左面板

- 树形折叠结构：部门 → 分组 → 人员
- 点击人员节点筛选右侧表格
- 部门名简化：企微客服部→企微、店铺客服部→店铺

### 8.2 人员筛选

- 部门筛选 + 分组筛选 + 专项筛选
- 专项筛选默认只匹配本职（`current_specialty` 或 `hr_category` 精确匹配）
- "包含跨专项处理"复选框：勾选后才启用工作量条件
