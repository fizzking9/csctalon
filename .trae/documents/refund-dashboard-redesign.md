# 退款看板重设计 Spec

> 版本:v1.0 · 2026-08-12
> 依据:运营看板已验证的 6 专项分类 / T+7 判定 / staff_type 三分类 / reason 归一化 7 大类口径
> 目标:把退款看板从"汇总监控"升级为"人员×设备×类型 深度分析 + 下钻审计"

---

## 一、背景与目标

### 1.1 现状问题

当前退款看板(RefundDashboard.vue)只有两个 Tab:
- **今日视图**:11 张 KPI 卡 + 小时/日趋势 + 失败原因 7 类 + 专员接待排名
- **综合看板**:催单风险 5 视图 + 挽回专项 6 视图 + 现金流 7 视图

**核心缺失**:
1. 看到汇总数后无法下钻到具体设备/人员
2. 没有人员成功率趋势(只有全员趋势)
3. 没有类型维度的趋势对比
4. 设备维度完全缺失(反向视角)
5. AuditView 审计组件是孤岛,未与退款分析打通
6. staff_detail 已有 rich 字段(department/position/staff_type/refund_op_amount)但前端只展示了 6 列

### 1.2 设计目标

把退款看板打造成"退款深度分析中枢":
- **人员维度**:查看每个人员周期内接待的所有设备 + 挽回成功率 + 具体挽回/流失的设备清单
- **设备维度**:反向视角,识别高风险设备(复退款/跨人员/高金额)
- **类型维度**:退款原因 7 大类 + problem_type 细分的接待量/成功率/趋势
- **下钻审计**:从汇总数 → 设备清单 → AuditView 时序,形成完整审计链路

### 1.3 设计原则

1. **复用已验证口径**:所有分母/分子/失败判定/T+7 窗口/staff_type 分类的逻辑,与 `refund_recovery_analysis` 保持完全一致,不重新发明
2. **汇总→下钻模式**:退款看板做"批量对比+定位",AuditView 做"单点深挖",两者通过跳转打通
3. **数据已有优先**:优先使用 refund_wide / cs_records / refunds / service_tickets 现有数据;SCRM 质量数据本期不接入
4. **不过度设计**:每个图表都要能回答一个具体业务问题

---

## 二、整体架构

### 2.1 Tab 结构(5 Tab)

```
退款看板 (RefundDashboard.vue)
├── Tab1: 概览        ← 原今日视图强化,小幅优化
├── Tab2: 人员分析    ← 新增(P0 核心)
├── Tab3: 设备分析    ← 新增(P0 核心)
├── Tab4: 类型×趋势   ← 新增(P0 核心)
└── Tab5: 综合看板    ← 原 overview,保留微调
```

### 2.2 数据流

```
后端                                      前端
─────────────────────────────────────────────────────
refund_recovery_analysis (已有,复用)  ──→ Tab1 概览
refund_personnel_analysis (新增)      ──→ Tab2 人员分析
refund_device_analysis (新增)         ──→ Tab3 设备分析
refund_type_trend (新增)              ──→ Tab4 类型×趋势
executeDashboard (已有,复用)          ──→ Tab5 综合看板
audit_timeline (已有,AuditView)       ←── Tab2/Tab3 下钻跳转
```

### 2.3 路由与权限

- 路由不变:`/dashboards/refund`(code=preset_dashboard_refund)
- 权限不变:仅 isManager=true 可见(决策看板下)
- Tab 切换更新 URL query:`?tab=overview|today|personnel|device|type`

---

## 三、Tab1:概览(今日视图强化)

### 3.1 保留内容(不变)

- 11 张 KPI 卡(total_sn/success_sn/failed_sn/success_rate/abandon/fail/success/recharge/target_total/actual_refund/fee_ratio)
- 今日按小时挽回趋势(双 Y 轴:柱=SN 数,线=成功率)
- 近 7 天按日挽回趋势(双 Y 轴)
- 失败原因 7 大类分布(柱=单量,线=占比)
- 退款专员接待排名表(现有 8 列)

### 3.2 新增内容

#### 3.2.1 Top5 高风险设备快照(当日失败金额倒序)

**位置**:KPI 卡下方,左侧半屏(12 col)

**数据来源**:新增后端字段 `top_risk_devices`(在 refund_recovery_analysis 返回中追加)

**展示**:
```
┌─ 今日高风险设备 Top5 (失败金额倒序) ──────────┐
│ SN            | 失败原因   | 退款金额 | 接待人 │
│ ABC123        | 不想用了   | ¥1,280   | 叶珍凤 │
│ DEF456        | 设备故障   | ¥980     | 朱海洋 │
│ ...                                              │
│ [查看全部] → 跳转 Tab3 设备分析               │
└──────────────────────────────────────────────────┘
```

**交互**:点击 SN 行 → 跳转 Tab3 并自动选中该 SN → 展开设备详情抽屉

#### 3.2.2 金额分桶饼图

**位置**:KPI 卡下方,右侧半屏(12 col)

**数据来源**:新增后端字段 `amount_buckets`

**展示**:失败 SN 按退款金额分桶
- 小额(<100 元)
- 中额(100-500 元)
- 大额(500-2000 元)
- 超大额(>2000 元)

每桶显示:SN 数 + 占比% + 金额合计

### 3.3 后端改动

在 `refund_recovery_analysis` 返回中追加 2 个字段:

```python
# analytics.py refund_recovery_analysis 返回追加:
{
    ...原有字段...,
    "top_risk_devices": [
        {
            "device_sn": "ABC123",
            "reason": "不想用了",          # 归一化原因
            "amount": 1280.00,
            "operator": "企微-叶珍凤",      # 接待人(首次接待)
            "operator_name": "叶珍凤",
            "refund_time": "2026-08-12T14:30:00",
            "status": "已退款"
        },
        ...  # Top5
    ],
    "amount_buckets": [
        {"bucket": "小额(<100)", "sn_cnt": 12, "amount": 960.00, "ratio": 40.0},
        {"bucket": "中额(100-500)", "sn_cnt": 10, "amount": 2800.00, "ratio": 33.3},
        {"bucket": "大额(500-2000)", "sn_cnt": 6, "amount": 5400.00, "ratio": 20.0},
        {"bucket": "超大额(>2000)", "sn_cnt": 2, "amount": 5600.00, "ratio": 6.7},
    ]
}
```

---

## 四、Tab2:人员分析(P0 核心)

### 4.1 布局

```
┌─ 筛选栏 ────────────────────────────────────────────────────┐
│ [部门:全部▼] [分组:全部▼] [专项:全部▼] [人员类型:全部▼]    │
│ [时间范围:近30天▼] [自定义日期] [刷新]                      │
└──────────────────────────────────────────────────────────────┘

┌─ ① 人员对比表(强化版) ────────────────────────────────────┐
│ 见 4.2 字段清单                                              │
└──────────────────────────────────────────────────────────────┘

┌─ ② 人员成功率趋势(多线) ──────┐ ┌─ ③ 人员接待量趋势(堆叠)─┐
│ 12 col                          │ │ 12 col                      │
└─────────────────────────────────┘ └─────────────────────────────┘

┌─ ④ 人员×原因 热力图矩阵 ────────────────────────────────────┐
│ 24 col                                                        │
└──────────────────────────────────────────────────────────────┘

┌─ ⑤ 人员详情抽屉(点击某行触发) ────────────────────────────┐
│ 见 4.3                                                        │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 ① 人员对比表(强化版 staff_detail)

**数据来源**:新增后端接口 `GET /api/v1/analytics/refund/personnel-analysis`

**字段清单**(在现有 staff_detail 基础上扩展):

| 列名 | 字段 | 类型 | 说明 |
|---|---|---|---|
| 姓名 | name | string | Person.display_name |
| 部门 | department | string | 企微/店铺/研发 |
| 分组 | group | string | 5 组之一 |
| 人员类型 | staff_type | tag | 专职挽回(绿)/退款操作(蓝)/其他人员挽回(灰) |
| 接待SN | total_sn | int | CS 接待去重 SN 数 |
| 成功 | success_sn | int | 挽留成功 SN 数(绿色) |
| 失败 | failed_sn | int | 挽留失败 SN 数(红色) |
| 成功率 | success_rate | tag% | ≥38%绿/30-38%黄/<30%红 |
| 退款操作SN | refund_op_cnt | int | 作为 refunds.operator 处理的 SN 数 |
| 退款操作金额 | refund_op_amount | money | 退款操作金额合计 |
| 挽回估算金额 | estimated_save_amount | money | success_sn × 同类失败平均金额(新增) |
| 首次接待 | first_reception | datetime | 周期内首次接待时间(新增) |
| 末次接待 | last_reception | datetime | 周期内末次接待时间(新增) |
| 操作 | - | button | [详情] [时序] |

**交互**:
- 表头支持排序(接待SN/成功率/退款操作金额/挽回估算金额)
- 点击 [详情] → 展开右侧抽屉(见 4.3)
- 点击 [时序] → 新窗口打开 AuditView,person_id 已带入
- 行可多选(checkbox),选中后联动 ②③ 趋势图

### 4.3 ⑤ 人员详情抽屉(核心下钻)

**触发**:点击人员对比表的 [详情] 按钮

**布局**:

```
┌─ 抽屉(右侧滑出,宽度 60vw) ────────────────────────────────┐
│                                                              │
│  ┌─ 人员概要 ──────────────────────────────────────────────┐│
│  │ 叶珍凤 | 企微 | 朱海洋组 | 专职挽回                       ││
│  │ 接待SN: 45个 | 成功: 28个 | 失败: 17个 | 成功率: 62.2%   ││
│  │ 退款操作: 12个 SN | 退款金额: ¥8,400 | 挽回估算: ¥6,720  ││
│  │ [查看完整时序] → 跳转 AuditView                          ││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌─ 接待设备清单 ──────────────────────────────────────────┐│
│  │ [成功 Tab(28)] [失败 Tab(17)] [全部 Tab(45)]            ││
│  │                                                          ││
│  │ 筛选: [失败原因▼] [搜索SN]                               ││
│  │                                                          ││
│  │ ┌─ 表格 ─────────────────────────────────────────────┐ ││
│  │ │ SN | 首次接待 | 末次接待 | 接待次数 | 失败原因     │ ││
│  │ │    | 退款金额 | 退款时间 | 最终状态 | [时序]       │ ││
│  │ └────────────────────────────────────────────────────┘ ││
│  │                                                          ││
│  │ 每行点击 [时序] → AuditView(person+device_sn)          ││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌─ 该人员的成功率趋势(单线,日粒度) ────────────────────┐│
│  │ 双 Y 轴:柱=接待SN,线=成功率                           ││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌─ 该人员×原因 分布(饼图) ──────────────────────────────┐│
│  │ 该人员接待的 SN 按失败原因 7 大类分布                  ││
│  └──────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

**设备清单字段**:

| 列名 | 字段 | 说明 |
|---|---|---|
| 设备SN | device_sn | |
| 首次接待 | first_reception_time | 该人员首次接待此 SN 的时间 |
| 末次接待 | last_reception_time | 该人员末次接待此 SN 的时间 |
| 接待次数 | reception_count | 该人员接待此 SN 的 cs_record 条数 |
| 结果 | result | 成功(绿)/失败(红) |
| 失败原因 | fail_reason | 归一化 7 大类(成功则为空) |
| 退款金额 | refund_amount | 失败时显示,成功为 0 |
| 退款时间 | refund_time | 失败时显示 |
| 最终状态 | final_status | 已退款/申请中/无需退款等 |
| 操作 | - | [时序] 跳转 AuditView |

### 4.4 ② 人员成功率趋势(多线对比)

**图表**:ECharts 折线图

**数据来源**:新增后端接口 `GET /api/v1/analytics/refund/personnel-trend`

**交互**:
- 默认展示成功率 Top3 + Bottom3 人员(共 6 条线)
- 可通过表格行多选自定义对比人员
- X 轴:日期(按日/周粒度切换)
- Y 轴:成功率%
- 悬浮显示:姓名 + 日期 + 成功率 + 接待SN数

### 4.5 ③ 人员接待量趋势(堆叠柱)

**图表**:ECharts 堆叠柱状图

**数据来源**:同 ② 接口

**展示**:
- X 轴:日期
- Y 轴:SN 数
- 堆叠:每个选中人员一层(颜色区分)
- 与 ② 联动:选中人员一致

### 4.6 ④ 人员×原因 热力图矩阵

**图表**:ECharts heatmap

**数据来源**:新增后端接口 `GET /api/v1/analytics/refund/person-reason-matrix`

**展示**:
- 行:人员(按接待量倒序,默认 Top15)
- 列:7 大原因(不想用了/充值错误/设备故障/网卡卡无网络/套餐问题/卡商问题/其他)
- 值:该人员在该原因下的**成功率**(颜色:绿=高/红=低)
- 单元格悬浮:姓名 + 原因 + 接待N个 + 成功M个 + 成功率X%

**交互**:
- 点击单元格 → 展开抽屉,自动筛选该人员+该原因的设备清单
- 可切换"成功率/接待量/失败数"三种色阶

### 4.7 后端接口

#### 接口1:人员分析汇总

```
GET /api/v1/analytics/refund/personnel-analysis
参数:
  start?: string (YYYY-MM-DD)
  end?: string (YYYY-MM-DD)
  department?: string (企微/店铺/研发)
  group?: string (分组名)
  specialty?: string (专项名)
  staff_type?: string (专职挽回/退款操作/其他人员挽回)

返回:
{
  "staff_detail": [
    {
      "operator": "企微-叶珍凤",
      "name": "叶珍凤",
      "department": "企微",
      "group": "朱海洋组",
      "position": "组员",
      "staff_type": "专职挽回",
      "total_sn": 45,
      "success_sn": 28,
      "failed_sn": 17,
      "success_rate": 62.22,
      "refund_op_cnt": 12,
      "refund_op_amount": 8400.00,
      "estimated_save_amount": 6720.00,  // success_sn × 同类失败平均金额
      "first_reception": "2026-08-01T09:00:00",
      "last_reception": "2026-08-12T15:30:00",
    },
    ...
  ],
  "summary": {
    "total_persons": 15,
    "total_sn": 320,
    "avg_success_rate": 45.6,
    "total_refund_op_amount": 128000.00,
  }
}
```

**实现要点**:
- 复用 `refund_recovery_analysis` 的人员分母/失败 SN 逻辑
- 新增 `estimated_save_amount` 计算:对每个成功 SN,用同原因类别的失败 SN 平均金额估算
- 新增 `first_reception / last_reception` 从 cs_records 聚合
- 支持 department/group/specialty/staff_type 筛选

#### 接口2:人员接待设备清单

```
GET /api/v1/analytics/refund/person-devices
参数:
  operator: string (必填,人员别名如 "企微-叶珍凤")
  start?: string
  end?: string
  result?: string (success/failed/all,默认 all)

返回:
{
  "operator": "企微-叶珍凤",
  "name": "叶珍凤",
  "devices": [
    {
      "device_sn": "ABC123",
      "first_reception_time": "2026-08-05T10:00:00",
      "last_reception_time": "2026-08-08T14:00:00",
      "reception_count": 3,
      "result": "failed",           // success/failed
      "fail_reason": "不想用了",     // 归一化原因,成功则为 null
      "refund_amount": 280.00,       // 失败时显示
      "refund_time": "2026-08-09T12:00:00",
      "final_status": "已退款",
      "refund_no": "RF20260809123",  // 退款单号(失败时)
    },
    ...
  ],
  "summary": {
    "total": 45,
    "success": 28,
    "failed": 17,
    "total_refund_amount": 8400.00,
  }
}
```

**实现要点**:
- 从 cs_records 查该 operator 接待的 problem_category='退款' 的 SN 集合
- 与 global_fail_sns 交集判断 success/failed
- 失败 SN 关联 refunds 表取金额/时间/单号
- 按 first_reception_time 倒序

#### 接口3:人员趋势

```
GET /api/v1/analytics/refund/personnel-trend
参数:
  start?: string
  end?: string
  operators?: string (逗号分隔,如 "企微-叶珍凤,企微-朱海洋")
  grain?: string (day/week,默认 day)

返回:
{
  "dates": ["2026-08-01", "2026-08-02", ...],
  "persons": [
    {
      "operator": "企微-叶珍凤",
      "name": "叶珍凤",
      "trend": [
        {"date": "2026-08-01", "total_sn": 5, "success_sn": 3, "failed_sn": 2, "success_rate": 60.0},
        ...
      ]
    },
    ...
  ]
}
```

**实现要点**:
- 复用 refund_recovery_analysis 的 daily_trend 逻辑,但按 operator 拆分
- 默认返回 Top10 人员的趋势(按接待量),支持 operators 参数自定义

#### 接口4:人员×原因矩阵

```
GET /api/v1/analytics/refund/person-reason-matrix
参数:
  start?: string
  end?: string
  top?: int (默认 15,按接待量倒序取 Top N 人员)

返回:
{
  "reasons": ["不想用了", "充值错误", "设备故障", "网卡卡无网络", "套餐问题", "卡商问题", "其他"],
  "persons": [
    {
      "operator": "企微-叶珍凤",
      "name": "叶珍凤",
      "cells": [
        {"reason": "不想用了", "total_sn": 12, "success_sn": 8, "failed_sn": 4, "success_rate": 66.7},
        {"reason": "充值错误", "total_sn": 5, "success_sn": 2, "failed_sn": 3, "success_rate": 40.0},
        ...
      ]
    },
    ...
  ]
}
```

**实现要点**:
- 对每个人员的接待 SN,按失败原因分组(成功 SN 的原因从 cs_record.problem_type 归一化)
- 成功 SN 也需要归一化原因(从 cs_record 取 problem_type → 归一化)

---

## 五、Tab3:设备分析(P0 核心,反向视角)

### 5.1 布局

```
┌─ 筛选栏 ────────────────────────────────────────────────────┐
│ [失败原因:全部▼] [金额区间:全部▼] [接待次数:全部▼]        │
│ [时间范围:近30天▼] [自定义日期] [刷新]                      │
└──────────────────────────────────────────────────────────────┘

┌─ ① 设备风险分布 KPI ────────────────────────────────────────┐
│ 4 张卡片:复退款设备数 | 跨人员接待设备数 | 高金额设备数      │
│         | 总失败设备数                                        │
└──────────────────────────────────────────────────────────────┘

┌─ ② Top20 高风险设备表(失败金额倒序) ──────────────────────┐
│ 见 5.2                                                        │
└──────────────────────────────────────────────────────────────┘

┌─ ③ 接待→流失时差分布 ─────┐ ┌─ ④ 设备接待次数分布 ────────┐
│ 12 col                      │ │ 12 col                        │
└─────────────────────────────┘ └────────────────────────────────┘

┌─ ⑤ 设备详情抽屉(点击某行触发) ────────────────────────────┐
│ 见 5.3                                                        │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 ② Top20 高风险设备表

**数据来源**:新增后端接口 `GET /api/v1/analytics/refund/device-analysis`

**字段清单**:

| 列名 | 字段 | 说明 |
|---|---|---|
| 设备SN | device_sn | |
| 接待次数 | reception_count | 被 cs_record 接待的总次数 |
| 接待人员数 | receptionist_count | 被几个不同人员接待过(跨人员) |
| 接待人员 | receptionists | 逗号分隔的姓名列表 |
| 失败原因 | fail_reason | 归一化 7 大类 |
| 退款金额 | refund_amount | |
| 退款时间 | refund_time | |
| 最终状态 | final_status | 已退款/申请中/无需退款 |
| 退款单号 | refund_no | |
| 操作 | - | [详情] [时序] |

**交互**:
- 默认按退款金额倒序
- 点击 [详情] → 展开设备详情抽屉(见 5.3)
- 点击 [时序] → 新窗口打开 AuditView,device_sn 已带入

### 5.3 ⑤ 设备详情抽屉

**触发**:点击设备表的 [详情] 按钮

**布局**:

```
┌─ 抽屉(右侧滑出,宽度 60vw) ────────────────────────────────┐
│                                                              │
│  ┌─ 设备画像 ──────────────────────────────────────────────┐│
│  │ SN: ABC123 | 设备型号: xxx                              ││
│  │ 首次接待: 2026-08-01 09:00 | 末次接待: 2026-08-08 14:00 ││
│  │ 接待次数: 3 | 接待人员: 2人(叶珍凤,朱海洋)            ││
│  │ 最终结果: 失败(已退款) | 退款金额: ¥1,280             ││
│  │ 退款时间: 2026-08-09 12:00 | 退款单号: RF20260809123   ││
│  │ [查看完整时序] → 跳转 AuditView                          ││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌─ 接待记录时间线 ────────────────────────────────────────┐│
│  │ 时间 | 接待人 | 问题描述 | 处理结果 | 接待时长           ││
│  │ 2026-08-01 09:00 | 叶珍凤 | 不想用了 | 沟通挽回中 | 15分 ││
│  │ 2026-08-05 14:00 | 叶珍凤 | 仍想退款   | 继续沟通   | 20分 ││
│  │ 2026-08-08 14:00 | 朱海洋 | 坚持退款   | 转退款流程 | 10分 ││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌─ 退款记录 ──────────────────────────────────────────────┐│
│  │ 退款单号 | 退款金额 | 退款方式 | 状态 | 操作人 | 审核人 ││
│  │ RF20260809123 | ¥1,280 | 线上 | 已退款 | sys | 张蕾    ││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌─ 关联工单(若有) ──────────────────────────────────────┐│
│  │ 工单号 | 服务项 | 服务类别 | 登记时间 | 完成时间 | 处理人││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌─ 钱包提现记录(若有) ──────────────────────────────────┐│
│  │ 提现时间 | 提现金额 | 操作类型                           ││
│  └──────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

### 5.4 ③ 接待→流失时差分布

**图表**:柱状图

**数据来源**:新增后端字段 `loss_time_dist`

**展示**:
- X 轴:时差分桶(当天/1-3天/3-7天/7-15天/15天+)
- Y 轴:失败 SN 数
- 颜色:按金额深浅区分(金额越大颜色越深)

**业务意义**:识别"快流失"vs"慢流失"设备,指导挽回节奏

### 5.5 ④ 设备接待次数分布

**图表**:柱状图 + 折线(双 Y 轴)

**展示**:
- X 轴:接待次数(1次/2次/3次/4次/5次+)
- 柱:设备数
- 线:该桶失败率%
- 业务意义:接待次数越多失败率是否越高(沟通成本 vs 成功率)

### 5.6 ① 设备风险分布 KPI(4 卡)

| 卡片 | 含义 | 颜色 |
|---|---|---|
| 复退款设备数 | 同一 SN 在周期内出现≥2次退款诉求 | 红色 |
| 跨人员接待设备数 | 被≥2个不同人员接待过的 SN | 橙色 |
| 高金额设备数 | 退款金额>1000 元的 SN | 紫色 |
| 总失败设备数 | 周期内失败 SN 总数 | 灰色 |

### 5.7 后端接口

#### 接口5:设备分析

```
GET /api/v1/analytics/refund/device-analysis
参数:
  start?: string
  end?: string
  reason?: string (失败原因筛选)
  amount_min?: number
  amount_max?: number
  reception_count_min?: int
  limit?: int (默认 20,Top N)

返回:
{
  "risk_summary": {
    "repeat_refund_devices": 8,      // 复退款设备数
    "cross_person_devices": 12,      // 跨人员接待设备数
    "high_amount_devices": 5,        // 金额>1000 的设备数
    "total_failed_devices": 45,
  },
  "top_devices": [
    {
      "device_sn": "ABC123",
      "reception_count": 3,
      "receptionist_count": 2,
      "receptionists": ["叶珍凤", "朱海洋"],
      "fail_reason": "不想用了",
      "refund_amount": 1280.00,
      "refund_time": "2026-08-09T12:00:00",
      "final_status": "已退款",
      "refund_no": "RF20260809123",
    },
    ...  // Top20
  ],
  "loss_time_dist": [
    {"bucket": "当天", "sn_cnt": 5, "amount": 1200.00},
    {"bucket": "1-3天", "sn_cnt": 12, "amount": 4800.00},
    {"bucket": "3-7天", "sn_cnt": 15, "amount": 7200.00},
    {"bucket": "7-15天", "sn_cnt": 8, "amount": 3600.00},
    {"bucket": "15天+", "sn_cnt": 5, "amount": 2400.00},
  ],
  "reception_count_dist": [
    {"bucket": "1次", "device_cnt": 20, "fail_rate": 35.0},
    {"bucket": "2次", "device_cnt": 15, "fail_rate": 45.0},
    {"bucket": "3次", "device_cnt": 7, "fail_rate": 60.0},
    {"bucket": "4次", "device_cnt": 2, "fail_rate": 75.0},
    {"bucket": "5次+", "device_cnt": 1, "fail_rate": 100.0},
  ]
}
```

#### 接口6:设备详情

```
GET /api/v1/analytics/refund/device-detail?device_sn=ABC123
参数:
  device_sn: string (必填)
  start?: string
  end?: string

返回:
{
  "device_sn": "ABC123",
  "device_type": "xxx",           // 来自 devices 表(若有)
  "first_reception": "2026-08-01T09:00:00",
  "last_reception": "2026-08-08T14:00:00",
  "reception_count": 3,
  "receptionists": ["叶珍凤", "朱海洋"],
  "result": "failed",
  "fail_reason": "不想用了",
  "refund_amount": 1280.00,
  "refund_time": "2026-08-09T12:00:00",
  "refund_no": "RF20260809123",
  "final_status": "已退款",
  "reception_timeline": [
    {
      "time": "2026-08-01T09:00:00",
      "operator": "企微-叶珍凤",
      "operator_name": "叶珍凤",
      "problem_type": "不想用了",
      "description": "...",        // cs_record 描述
      "execution_result": "...",
      "consult_time": 15,          // 接待时长(分钟)
    },
    ...
  ],
  "refund_records": [
    {
      "refund_no": "RF20260809123",
      "refund_amount": 1280.00,
      "refund_method": "线上",
      "status": "已退款",
      "operator": "sys",
      "auditor": "张蕾",
      "created_at_raw": "2026-08-09T12:00:00",
    }
  ],
  "tickets": [
    {
      "ticket_no": "GD20260808001",
      "service_item": "不想用了",
      "service_category": "退款",
      "register_time": "2026-08-08T16:00:00",
      "finish_time": "2026-08-09T12:00:00",
      "handler": "企微-朱海洋",
    }
  ],
  "wallet_records": [
    {
      "wallet_id": "W001",
      "op_time": "2026-08-09T13:00:00",
      "op_amount": 1280.00,
      "op_type": "用户提现",
    }
  ]
}
```

**实现要点**:
- 设备画像从 cs_records + refunds 聚合
- 接待时间线从 cs_records 按时间正序
- 退款/工单/钱包记录从 refund_wide 关联(已物化)
- 若 refund_wide 无该 SN,实时查 refunds/service_tickets/wallet_withdrawals

---

## 六、Tab4:类型×趋势(P0 核心)

### 6.1 布局

```
┌─ 筛选栏 ────────────────────────────────────────────────────┐
│ [时间范围:近30天▼] [粒度:日/周/月▼] [刷新]                  │
└──────────────────────────────────────────────────────────────┘

┌─ ① 各原因类型 接待量&成功率(双Y轴) ───────────────────────┐
│ 24 col                                                        │
└──────────────────────────────────────────────────────────────┘

┌─ ② 各人员类型 接待量&成功率 ────────────────────────────────┐
│ 24 col                                                        │
└──────────────────────────────────────────────────────────────┘

┌─ ③ 日期×原因 矩阵热力图 ────────────────────────────────────┐
│ 24 col                                                        │
└──────────────────────────────────────────────────────────────┘

┌─ ④ problem_type 细分 Top10 ──────┐ ┌─ ⑤ 组间对比 ──────────┐
│ 12 col                             │ │ 12 col                  │
└────────────────────────────────────┘ └──────────────────────────┘
```

### 6.2 ① 各原因类型 接待量&成功率

**图表**:双 Y 轴(堆叠柱 + 多线)

**展示**:
- X 轴:日期(按粒度切换)
- 左 Y 轴:SN 数(堆叠柱,7 大原因各一层)
- 右 Y 轴:成功率%(7 条线,每原因一条)
- 悬浮:日期 + 原因 + 接待N个 + 成功M个 + 成功率X%

**业务意义**:看各原因类型的接待量变化 + 成功率波动

### 6.3 ② 各人员类型 接待量&成功率

**图表**:双 Y 轴(分组柱 + 多线)

**展示**:
- X 轴:日期
- 左 Y 轴:SN 数(分组柱:专职挽回/其他人员挽回/退款操作)
- 右 Y 轴:成功率%(3 条线)

**业务意义**:对比专职挽回 vs 其他人员挽回的能力差异

### 6.4 ③ 日期×原因 矩阵热力图

**图表**:ECharts heatmap

**展示**:
- 行:日期(近 30 天)
- 列:7 大原因
- 值:失败 SN 数(颜色深浅)
- 可切换"失败数/接待数/成功率"三种色阶

### 6.5 ④ problem_type 细分 Top10

**图表**:横向柱状图

**展示**:
- 取周期内接待量 Top10 的 problem_type 原始值
- 每条柱:接待 SN 数(按成功/失败堆叠)
- 旁注:成功率%

**业务意义**:7 大类太粗,看细分哪些 problem_type 流失率高

### 6.6 ⑤ 组间对比

**图表**:横向分组柱 + 表格

**展示**:
- 5 个组(董梦玲组/吴晓龙组/叶珍凤组/朱海洋组/张蕾组)
- 每组:接待SN | 成功率 | 退款金额 | 人均挽回估算 | 失败数

### 6.7 后端接口

#### 接口7:类型趋势

```
GET /api/v1/analytics/refund/type-trend
参数:
  start?: string
  end?: string
  grain?: string (day/week/month,默认 day)
  dimension?: string (reason/staff_type,默认 reason)

返回(dimension=reason):
{
  "dates": ["2026-08-01", "2026-08-02", ...],
  "reasons": ["不想用了", "充值错误", "设备故障", "网卡卡无网络", "套餐问题", "卡商问题", "其他"],
  "series": [
    {
      "reason": "不想用了",
      "trend": [
        {"date": "2026-08-01", "total_sn": 5, "success_sn": 3, "failed_sn": 2, "success_rate": 60.0},
        ...
      ]
    },
    ...
  ]
}

返回(dimension=staff_type):
{
  "dates": [...],
  "staff_types": ["专职挽回", "其他人员挽回", "退款操作"],
  "series": [
    {
      "staff_type": "专职挽回",
      "trend": [...]
    },
    ...
  ]
}
```

**实现要点**:
- 按 reason:对每日每个 SN 归一化原因(从 cs_record.problem_type 或 refund.refund_reason / service_ticket.service_item 归一化)
- 按 staff_type:对每日每个 SN 取接待人的 staff_type
- 成功率复用 refund_recovery 的失败判定逻辑

#### 接口8:problem_type 细分

```
GET /api/v1/analytics/refund/problem-type-detail
参数:
  start?: string
  end?: string
  top?: int (默认 10)

返回:
{
  "items": [
    {
      "problem_type": "不想用了",   // cs_record.problem_type 原始值
      "normalized_reason": "不想用了",  // 归一化后的 7 大类
      "total_sn": 45,
      "success_sn": 28,
      "failed_sn": 17,
      "success_rate": 62.2,
      "total_amount": 8400.00,       // 失败 SN 金额合计
    },
    ...
  ]
}
```

#### 接口9:组间对比

```
GET /api/v1/analytics/refund/group-comparison
参数:
  start?: string
  end?: string

返回:
{
  "groups": [
    {
      "group": "朱海洋组",
      "person_cnt": 3,
      "total_sn": 120,
      "success_sn": 72,
      "failed_sn": 48,
      "success_rate": 60.0,
      "total_refund_amount": 24000.00,
      "estimated_save_amount": 18000.00,
      "avg_success_rate": 55.5,    // 人均成功率
    },
    ...
  ]
}
```

---

## 七、Tab5:综合看板(保留微调)

### 7.1 保留内容

原有三段结构不变:
- 区块1:催单风险(5 视图)
- 区块2:退款挽回专项(6 视图)
- 区块3:现金流与退款基础(7 视图)

### 7.2 微调

- 挽回专项区块右上角增加"组间对比"入口按钮 → 点击跳转 Tab4 的 ⑤
- 现金流区块增加"金额分桶"小图(复用 Tab1 的 amount_buckets)

---

## 八、与 AuditView 的打通

### 8.1 跳转规则

| 来源 | 目标 | 参数 |
|---|---|---|
| Tab2 人员表 [时序] | AuditView(person) | person_id + 时间范围 |
| Tab2 设备清单 [时序] | AuditView(person+device) | person_id + device_sn + 时间范围 |
| Tab3 设备表 [时序] | AuditView(device) | device_sn + 时间范围 |
| Tab1 Top5设备 [查看全部] | Tab3 设备分析 | 切换 Tab |
| Tab2 热力图单元格 | Tab2 抽屉(带筛选) | person + reason |

### 8.2 AuditView 改造

现有 AuditView 已支持 person/device 两种模式,需补充:
- 支持 URL query 同时带 person_id + device_sn(双维度过滤)
- 时间范围自动从 URL query 继承
- 新窗口打开(不离开退款看板)

### 8.3 路由

AuditView 路由已存在(在运营看板下),退款看板跳转时用新窗口:
```js
const url = `/workbench/audit?person_id=${pid}&device_sn=${sn}&start=${start}&end=${end}`
window.open(url, '_blank')
```

---

## 九、前端组件清单

### 9.1 新增组件

| 组件 | 位置 | 说明 |
|---|---|---|
| PersonnelAnalysis.vue | views/dashboards/refund/ | Tab2 人员分析主体 |
| DeviceAnalysis.vue | views/dashboards/refund/ | Tab3 设备分析主体 |
| TypeTrend.vue | views/dashboards/refund/ | Tab4 类型×趋势主体 |
| PersonDetailDrawer.vue | views/dashboards/refund/ | Tab2 人员详情抽屉 |
| DeviceDetailDrawer.vue | views/dashboards/refund/ | Tab3 设备详情抽屉 |
| HeatMapChart.vue | components/ | 通用热力图(人员×原因、日期×原因) |

### 9.2 修改组件

| 组件 | 改动 |
|---|---|
| RefundDashboard.vue | 新增 3 个 Tab,调整 Tab 切换逻辑,URL query 同步 |
| AuditView.vue | 支持双维度(person+device)URL query |

### 9.3 复用组件

- BaseChart.vue(已有,ECharts 封装)
- ViewItemCard.vue(已有,综合看板视图卡片)
- el-table / el-drawer / el-tag(Element Plus)

---

## 十、后端实现要点

### 10.1 新增接口清单

| 接口 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 1 | GET | /api/v1/analytics/refund/personnel-analysis | 人员分析汇总 |
| 2 | GET | /api/v1/analytics/refund/person-devices | 人员接待设备清单 |
| 3 | GET | /api/v1/analytics/refund/personnel-trend | 人员趋势 |
| 4 | GET | /api/v1/analytics/refund/person-reason-matrix | 人员×原因矩阵 |
| 5 | GET | /api/v1/analytics/refund/device-analysis | 设备分析 |
| 6 | GET | /api/v1/analytics/refund/device-detail | 设备详情 |
| 7 | GET | /api/v1/analytics/refund/type-trend | 类型趋势 |
| 8 | GET | /api/v1/analytics/refund/problem-type-detail | problem_type 细分 |
| 9 | GET | /api/v1/analytics/refund/group-comparison | 组间对比 |

### 10.2 复用逻辑

所有接口复用 `refund_recovery_analysis` 的核心逻辑:
- 分母:cs_records.problem_category='退款' 的去重 SN
- 失败判定:T+7 窗口 + refunds + service_tickets + cs_record 关键词
- 原因归一化:`_normalize_refund_reason` 7 大类
- 人员匹配:PersonAlias → Person
- staff_type 分类:专职挽回/退款操作/其他人员挽回
- 系统过滤:SYS_OP_BLACKLIST_RR + SYS_KW 描述过滤

### 10.3 性能考虑

- 人员分析、设备分析接口可复用 refund_recovery_analysis 的中间结果(若同请求已计算)
- type-trend 接口按日期×原因拆分,数据量较大,建议加缓存(5 分钟)
- device-detail 接口单设备查询,响应快
- 所有接口支持 start/end 参数,默认近 30 天

### 10.4 文件改动

| 文件 | 改动 |
|---|---|
| backend/app/services/analytics.py | 新增 9 个函数(接口1-9),复用 refund_recovery_analysis 的辅助函数 |
| backend/app/api/v1/analytics.py | 新增 9 个路由 |
| 无需新增 model | 全部用现有表(cs_records/refunds/service_tickets/refund_wide/persons/person_alias/devices) |

---

## 十一、实施计划

### Phase 1:P0 核心(3 个 Tab)

1. **后端**:实现接口 1-4(人员分析相关)
2. **前端**:Tab2 人员分析 + 人员详情抽屉
3. **后端**:实现接口 5-6(设备分析相关)
4. **前端**:Tab3 设备分析 + 设备详情抽屉
5. **后端**:实现接口 7-9(类型趋势相关)
6. **前端**:Tab4 类型×趋势
7. **联调**:AuditView 跳转打通

### Phase 2:Tab1 强化

1. **后端**:refund_recovery_analysis 追加 top_risk_devices + amount_buckets
2. **前端**:Tab1 新增 Top5 设备快照 + 金额分桶饼图

### Phase 3:Tab5 微调

1. **前端**:综合看板增加组间对比入口 + 金额分桶小图

---

## 十二、验收标准

### 12.1 功能验收

- [ ] Tab2 人员对比表展示所有字段(含估算金额/首末接待时间)
- [ ] Tab2 点击 [详情] 弹出抽屉,展示该人员接待的设备清单(成功/失败可切)
- [ ] Tab2 人员趋势图支持多选对比,日/周粒度切换
- [ ] Tab2 人员×原因热力图可点击单元格下钻
- [ ] Tab3 设备表展示 Top20,支持金额/原因/次数筛选
- [ ] Tab3 点击 [详情] 弹出抽屉,展示接待时间线 + 退款记录 + 工单 + 钱包
- [ ] Tab4 各原因类型趋势图(堆叠柱+多线)正常
- [ ] Tab4 人员类型趋势图正常
- [ ] Tab4 日期×原因热力图正常
- [ ] Tab4 problem_type 细分 Top10 正常
- [ ] Tab4 组间对比正常
- [ ] AuditView 跳转携带 person_id/device_sn/时间范围参数

### 12.2 数据一致性

- [ ] Tab2 人员接待SN总数 = Tab1 KPI 的 total_sn
- [ ] Tab3 失败设备总数 = Tab1 KPI 的 failed_sn
- [ ] Tab4 各原因成功率与 Tab1 reason_dist 一致
- [ ] 所有口径与运营看板 refund-recovery 接口一致

### 12.3 性能

- [ ] 各接口响应时间 < 3 秒(近 30 天数据)
- [ ] 热力图渲染 < 1 秒
- [ ] 抽屉打开 < 1 秒

---

## 附录 A:7 大原因归一化规则

(来自 analytics.py `_REASON_NORMALIZE_RULES`)

| 归一化桶 | 关键词 |
|---|---|
| 不想用了 | 不想用/不想要/没用了/不需要/退订/取消 |
| 充值错误 | 充错/充值错/充多/误充/错充 |
| 设备故障 | 设备/坏了/故障/损坏/不能用/用不了/死机/卡机 |
| 网卡卡无网络 | 网卡/卡慢/网速/网络慢/网慢/没网/无网/没信号/信号差/掉线 |
| 套餐问题(流量虚涨价升级) | 套餐/流量/升级/涨价/资费/太贵 |
| 卡商问题 | 卡商/渠道/代理/上游 |
| 其他(填写备注) | 兜底 |

## 附录 B:staff_type 分类规则

(来自 analytics.py)

| staff_type | 判定条件 |
|---|---|
| 专职挽回 | Person.specialty='退款挽回'(通过 _hr_map 映射) |
| 退款操作 | 无 CS 接待,仅作为 refunds.operator/service_tickets.handler |
| 其他人员挽回 | 非专职人员(组长/主管/咨询/套餐/投诉等)接待退款类 SN |

## 附录 C:失败判定三路合并(T+7 窗口)

| 来源 | 表 | 条件 |
|---|---|---|
| 退款单 | refunds | status ∉ (无需退款,驳回),created_at_raw 在 [首次接待, 首次接待+7天] |
| 退款工单 | service_tickets | service_category='退款',status≠终止,register_time 在 T+7 窗口 |
| CS描述关键词 | cs_records | description/execution_result/solution 含(退款说明/退款后重新充值/要求退回微信/非挽回用户) |
