# 按天匹配专项的工作量分计算改造

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context

当前人均效率计算在 `today_staff_efficiency` 中，使用 `Person.specialty`（单一当前值）判定所有工作桶的"本职/转接"系数。如果人员在查询周期内变更了专项（如 8月1-10日咨询接待 → 8月11日起退款挽回），旧工作会被错误地按新专项系数计算。

改造目标：按天匹配专项，对查询周期内每一天使用该天实际生效的专项系数。

## 方案：按天加权平均系数

### 核心思路

不改变 SQL 聚合粒度（仍是按人汇总），而是在 Python 侧预建**按天专项映射表**，对每个专项桶计算**天数加权平均系数**。

**前提假设**：工作分布在各天大致均匀（对于多天查询，这个近似足够准确；对于单天查询，系数完全精确）。

### 具体步骤

#### 1. 预建按天专项映射表

在 `today_staff_efficiency` 的工作量计算循环之前（约 line 4801），新增一段代码：

```python
# 1. 收集所有有工作记录的人员的 person_id
work_person_ids = set()
for r in rows:
    if r.person_id:
        work_person_ids.add(r.person_id)

# 2. 查询 PersonSpecialtyAssignment，获取查询周期内每天的专项
from datetime import date as _date_cls, timedelta
query_start = ts.date() if hasattr(ts, 'date') else _date_cls.fromisoformat(str(ts)[:10])
query_end = te.date() if hasattr(te, 'date') else _date_cls.fromisoformat(str(te)[:10])

# 生成日期列表
date_list = []
d = query_start
while d <= query_end:
    date_list.append(d)
    d += timedelta(days=1)

# 一次性查出所有 work_person_ids 的专项分配历史
specialty_assignments = db.execute(
    select(
        PersonSpecialtyAssignment.person_id,
        PersonSpecialtyAssignment.effective_from,
        PersonSpecialtyAssignment.effective_to,
        Specialty.name.label("specialty_name"),
        Specialty.difficulty_weight,
    )
    .select_from(PersonSpecialtyAssignment)
    .join(Specialty, PersonSpecialtyAssignment.specialty_id == Specialty.id)
    .where(
        PersonSpecialtyAssignment.person_id.in_(work_person_ids),
        PersonSpecialtyAssignment.is_primary == True,
        # 专项分配区间与查询区间有交集
        or_(
            PersonSpecialtyAssignment.effective_to.is_(None),
            PersonSpecialtyAssignment.effective_to >= query_start,
        ),
        PersonSpecialtyAssignment.effective_from <= query_end,
    )
).all()

# 3. 构建 person_id → {date: specialty_name} 映射
person_daily_specialty: dict[int, dict[_date_cls, str]] = {}
for sa in specialty_assignments:
    pid = sa.person_id
    sp_name = sa.specialty_name
    eff_from = sa.effective_from
    eff_to = sa.effective_to or query_end
    if pid not in person_daily_specialty:
        person_daily_specialty[pid] = {}
    for d in date_list:
        if eff_from <= d <= eff_to:
            # 如果同一天有多个专项分配，取 is_primary=True 的（上面已过滤）
            person_daily_specialty[pid][d] = sp_name

# 4. 构建 person_id → {specialty_name: days_count}
person_specialty_days: dict[int, dict[str, int]] = {}
for pid, daily_map in person_daily_specialty.items():
    counts: dict[str, int] = {}
    for sp in daily_map.values():
        counts[sp] = counts.get(sp, 0) + 1
    person_specialty_days[pid] = counts
```

#### 2. 改造工作量分系数计算

在现有循环中（约 line 4864-4887），将单一 `hr_cat` 替换为按桶计算的加权系数。

**修改前**（line 4864-4887）：
```python
hr_cat = _hr_map(r.sp, position)
mgmt = (position in ("主管", "组长", "副主管")) or hr_cat == "管理层"

for (bucket_key, cat_name, sp_set) in CAT_BUCKETS:
    sv, pv = cat_data[bucket_key]
    if sv == 0 and pv == 0:
        continue
    if mgmt:
        own = True
        coef = CAT_DEFAULT_WEIGHT[cat_name]
    elif hr_cat is not None and hr_cat in sp_set:
        own = True
        coef = WEIGHT[hr_cat]
    else:
        own = False
        coef = TRANSFER_PENALTY
    wp = sv * coef
    work_score += wp
```

**修改后**：
```python
position = r.pos or ""
# 管理岗判定不变
mgmt = (position in ("主管", "组长", "副主管"))
# 当前主专项（用于展示，不用于计算）
hr_cat = _hr_map(r.sp, position)

# 获取该人员的按天专项分布
pid = r.person_id
sp_days = person_specialty_days.get(pid, {}) if pid else {}
total_days = sum(sp_days.values()) if sp_days else 0

for (bucket_key, cat_name, sp_set) in CAT_BUCKETS:
    sv, pv = cat_data[bucket_key]
    if sv == 0 and pv == 0:
        bd[cat_name] = {...}
        continue
    
    if mgmt:
        # 管理岗: 跨类豁免，用大类默认系数（不变）
        own = True
        coef = CAT_DEFAULT_WEIGHT[cat_name]
    elif total_days > 0:
        # 按天加权平均系数
        weighted_coef = 0.0
        own_days = 0
        for sp_name, days in sp_days.items():
            sp_cat = _hr_map(sp_name, position)
            if sp_cat and sp_cat in sp_set:
                weighted_coef += WEIGHT.get(sp_cat, 1.0) * days
                own_days += days
            else:
                weighted_coef += TRANSFER_PENALTY * days
        coef = weighted_coef / total_days
        own = own_days > total_days / 2  # 大部分天数是本职则标记为本职
    else:
        # 无按天数据，回退到原逻辑
        if hr_cat is not None and hr_cat in sp_set:
            own = True
            coef = WEIGHT.get(hr_cat, 1.0)
        else:
            own = False
            coef = TRANSFER_PENALTY
    
    wp = sv * coef; ep = pv * coef
    work_score += wp; eff_score += ep
    bd[cat_name] = {
        "sessions": sv, "people": pv,
        "weight": round(coef, 3), "coef": round(coef, 3), "is_own": own,
        "work_contrib": round(wp, 1), "eff_contrib": round(ep, 1),
    }
```

### 关键设计决策

1. **天数加权平均**：假设工作在各天均匀分布，用天数比例加权。对于单天查询，结果完全精确；对于多天查询，误差在可接受范围。

2. **管理岗不变**：主管/组长/副主管的跨类豁免逻辑不变，仍使用 `CAT_DEFAULT_WEIGHT`。

3. **回退兼容**：当 `PersonSpecialtyAssignment` 无数据时（新人/未分配专项），回退到原单一 `hr_cat` 逻辑。

4. **性能**：一次 SQL 查询 + Python 字典构建，不增加 SQL 查询次数。

## 变更文件

| 文件 | 变更 |
|------|------|
| `backend/app/services/analytics.py` | 在 `today_staff_efficiency` 函数中新增按天专项映射构建 + 改造系数计算逻辑 |

## 验证

1. 修改覃克泳的专项：8月1-10日=咨询接待，8月11日起=退款挽回
2. 查询8月1-14日人均效率，确认：
   - 咨询接待桶的系数接近 1.0（大部分天数本职）
   - 退款挽回桶的系数接近 2.2（大部分天数本职）
3. 查询8月11日单天，确认退款挽回桶系数=2.2
4. 管理岗人员的系数不受影响
