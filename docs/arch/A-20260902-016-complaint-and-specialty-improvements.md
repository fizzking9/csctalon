# 投诉与专项优化实施方案

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context

用户反馈运营看板和人员管理的四个问题：
1. 人员编辑的专项可选择项不全（只有3个硬编码选项），需做成可配置字典
2. 投诉子类分布将内部/商户号/外部平台混在一起，需分开展示
3. 投诉处理人排行只显示内部投诉，需单列外部投诉表
4. 未闭环投诉显示"200条"但列表为空，数据不一致

---

## 问题1: 人员编辑专项选项不全 → 改为动态字典

### 现状
- 后端 `STANDARD_SPECIALTIES` (specialty.py:25-32) 定义6个专项，API `/specialties` 已返回完整列表
- 前端 `Persons.vue` 已加载 `specialtiesList` 用于筛选下拉框（第95行）
- 但任职分配表单（第470-472行）和批量编辑表单（第533-534行）硬编码了3个选项

### 修改
**文件: `web/src/views/system/Persons.vue`**

1. 第470-472行，将硬编码替换为动态选项：
```html
<el-select v-model="assignmentForm.specialty" clearable filterable style="width: 100%">
  <el-option v-for="s in specialtiesList" :key="s.id" :label="s.name" :value="s.name" />
</el-select>
```

2. 第533-534行，同样替换：
```html
<el-select v-model="batchForm.specialty" clearable filterable style="width: calc(100% - 100px)">
  <el-option v-for="s in specialtiesList" :key="s.id" :label="s.name" :value="s.name" />
</el-select>
```

### 验证
- 打开人员管理 → 点击编辑 → 专项下拉框应显示6个选项（咨询接待/套餐专项/退款处理/退款挽回/内部投诉/外部投诉）
- 批量编辑对话框同样包含完整选项

---

## 问题2: 投诉子类分布 → 按三大类分开三张饼图

### 现状
- 后端 `complaint_subs` 数据已包含 `source_type` 字段（内部投诉/商户号投诉/外部投诉）
- 前端 `complaintSubPieOption` (Today.vue:1437-1486) 已按 source_type 分组着色，但所有子类混在一个饼图中

### 修改
**文件: `web/src/views/dashboards/Today.vue`**

1. 模板中（第227-252行），将单个饼图替换为三列并排：
   - 内部投诉子类（商品/服务）→ 蓝色系
   - 外部投诉-商户号（微信投诉）→ 紫色系
   - 外部投诉-平台（12315/工信部/消协等）→ 红色系

2. 将原 `complaintSubPieOption` 替换为三个 computed：
   - `complaintSubInternalPieOption` — 过滤 `source_type === '内部投诉'`
   - `complaintSubMerchantPieOption` — 过滤 `source_type === '商户号投诉'`
   - `complaintSubExternalPieOption` — 过滤 `source_type === '外部投诉'`

3. 提取公共饼图构建函数 `buildSubPieOption(rows, colorPalette, sourceLabel)` 避免重复

4. 投诉根因柱图移至投诉处理人排行上方独占一行

### 验证
- 运营看板选择有投诉数据的日期，确认显示三个独立饼图
- 内部投诉饼图：商品投诉、服务投诉
- 商户号投诉饼图：微信投诉
- 外部平台投诉饼图：全国12315平台、工信部投诉、公共服务平台、消费者协会

---

## 问题3: 投诉处理人排行 → 拆分为内部/外部两表

### 现状
- 后端 SQL（analytics.py:3249-3319）UNION ALL 三张表，返回 `source_type` 区分来源
- 前端显示单个表格，包含"按来源"列（内部/外部/卡商）
- 用户要求因数量级不同分开展示

### 修改

**后端: `backend/app/services/analytics.py`**

1. 在现有 handler 聚合逻辑（第3323-3366行）之后，新增按 source_type 拆分：
   - `complaint_internal_handlers` — 仅 `source_type == '内部投诉'`
   - `complaint_external_handlers` — `source_type == '外部投诉'` + `'卡商群投诉'`

2. 在返回字典中（第3654-3665行）新增两个字段：
   - `complaint_internal_handlers`
   - `complaint_external_handlers`
   - 保留原 `complaint_handlers` 向后兼容

**前端: `web/src/views/dashboards/Today.vue`**

3. complaintData 初始化（第2121行）新增字段：
   - `complaint_internal_handlers`
   - `complaint_external_handlers`

4. 模板中（第265-332行）替换为两个并列表格：
   - 左：内部投诉处理人排行（Top 15）
   - 右：外部投诉处理人排行（Top 15）

### 验证
- 运营看板确认显示两个独立处理人排行表
- 内部表只含 service_tickets 内部投诉处理人
- 外部表含 external_complaints + card_complaints 处理人

---

## 问题4: 未闭环投诉列表为空

### 现状
- 用户看到"未闭环 200条"标签但列表无数据
- 后端 `complaint_pending_total` 和 `complaint_pending_list` 来自同一变量 `complaint_pending`，理论上应一致
- `_INCLUDE_COMPLAINT_FILES = False`（analytics.py:17）影响 complaint_sub/roots 聚合，但不影响 complaint_pending 的三表直接查询

### 修改

**后端: `backend/app/services/analytics.py`**

1. 在 complaint_pending 构建完成后（第3644行），增加数据一致性校验日志：
```python
import logging
_log = logging.getLogger(__name__)
if complaint_pending_total > 0 and len(complaint_pending) == 0:
    _log.warning(f"complaint_pending 数据不一致: total={complaint_pending_total}, list空")
    complaint_pending_total = 0
```

2. 在 complaint_pending 查询的 external_complaints/card_complaints 部分（第3495行前）增加注释，说明这些查询不受 `_INCLUDE_COMPLAINT_FILES` 影响

**前端: `web/src/views/dashboards/Today.vue`**

3. complaintData 赋值（第2129-2130行）增加防御性校验：
```javascript
complaint_pending_total: rdata.complaint_pending_total ?? null,
complaint_pending_list: (() => {
  const list = rdata.complaint_pending_list || []
  const total = rdata.complaint_pending_total
  if (total > 0 && list.length === 0) {
    console.warn('[complaint_pending] 数据不一致: total=' + total + ', list为空')
  }
  return list
})(),
```

4. 模板中（第369-376行之后）增加边界情况提示：当 `complaint_pending_total > 0` 但 `complaint_pending_list` 为空时，显示警告提示并提供刷新按钮

### 验证
- 清除浏览器缓存后重新加载运营看板
- 选择今日视图，确认"未闭环 N 条"标签与下方列表数据一致
- 若不一致，应看到新增的警告提示（而非空白表格）
- 检查后端日志，确认无 `complaint_pending 数据不一致` 警告

---

## 变更文件汇总

| 文件 | 问题 | 变更类型 |
|------|------|----------|
| `web/src/views/system/Persons.vue` | 问题1 | 修改第470-472行、第533-534行，硬编码→动态选项 |
| `web/src/views/dashboards/Today.vue` | 问题2 | 重构第227-252行模板，新增3个computed替代原pie |
| `web/src/views/dashboards/Today.vue` | 问题3 | 拆分第265-332行处理人表，新增complaintData字段 |
| `web/src/views/dashboards/Today.vue` | 问题4 | 第2129-2130行防御性校验，新增边界提示模板 |
| `backend/app/services/analytics.py` | 问题3 | 新增按source_type拆分的handler聚合+返回字段 |
| `backend/app/services/analytics.py` | 问题4 | 数据一致性校验日志+注释 |

## 实施顺序

1. **问题1** — 最简单，仅改前端两处模板，无后端依赖
2. **问题4** — 防御性修改，后端+前端增加校验，不影响现有功能
3. **问题3** — 后端拆分handler聚合 + 前端新增表格，前后端协调
4. **问题2** — 前端重构饼图区域，UI布局调整较大，在问题3基础上叠加
