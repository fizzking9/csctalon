# A-20260901-001-complaint-trend-merge-regression-test

**状态**：LOCKED（v1.1）
**日期**：2026-09-01（v1.1 于 2026-09-24 增补）
**架构师阶段产出**：Architect 架构师 Agent
**审查结论**：<待人工终审>

## 背景

`backend/app/api/v1/complaints.py` 的 `complaint_summary` 接口曾在 2026-09-01 发生 500 崩溃（提交 `1953631` 修复）：
- **根因**：`external_trend_merged` 嵌套调用 `_merge_trend(_merge_trend(...), ...)`，内层返回 `[{day, count}]` 字典列表，外层 `for d, c in trend_rows` 解包字典 → `c='count'` → `int('count')` 抛 ValueError。
- **修复**：两个补充表 dict 列表直接相加后一次 `_merge_trend` 合并。
- **现状**：该修复仅人工/模拟验证过，**无自动化回归测试**。

## 一、模块边界图

```mermaid
graph LR
  A[complaint_summary API] --> B[_merge_trend]
  A --> C[_suppl_trend_rows]
  C --> D[ExternalComplaint 表]
  C --> E[CardComplaint 表]
  B --> F[趋势返回 [{day,count}]]
  G[回归测试 test_complaint_summary_trend] --> B
  G --> C
```

## 二、接口契约

- 路径：`/api/v1/complaints/summary`（既有，**本次不改接口**）
- 方法：GET
- 本次变更：**仅新增测试**，不动生产代码（回归测试覆盖已修复逻辑）
- 被测函数：
  - `_merge_trend(trend_rows: [(day, count)], suppl_rows: [{day,total,overlap}]) -> [{day,count}]`
  - `_suppl_trend_rows(db, model, src_expr_val, ts, te) -> [{day,total,overlap}]`

## 三、测试设计（数据库变更：无）

| 用例 | 输入 | 预期 | 类型 |
|---|---|---|---|
| T1 正向 | `trend=[('2026-09-01',8)]`, `suppl=[{day:09-01,total:10,overlap:3},{day:09-02,total:5,overlap:5}]` | `[{09-01:15},{09-02:0(省略)}]` 即 `[{09-01:15}]` | 正向 |
| T2 边界 | 两个补充表 dict 列表直接相加后一次合并（复现修复点，旧嵌套写法会崩溃） | 不抛异常，逐日累加==总数 | 回归/边界 |
| T3 边界 | 空输入：`trend=[]`, `suppl=[]` | 返回 `[]` | 边界 |
| T4 边界 | 补充表 `overlap==total`（全部重叠） | 该日净增 0，不出现 | 边界 |

## 四、风险清单

1. **回归测试依赖内部私有函数 `_merge_trend`**：若未来重构该函数签名，测试需同步——测试定位为"逻辑契约锁定"，签名变更时须走架构流程。
2. **`_suppl_trend_rows` 需真实 DB**：其 SQL 依赖 PG 时区函数 `timezone('Asia/Shanghai')`，无法纯单测 mock——测试基建需连接测试库（已有 conftest）。
3. **时区敏感**：`_day_trunc` 依赖 `Asia/Shanghai`，若误用 UTC 截断会导致日期偏移——测试断言必须用北京时间语义。

## 人工终审重点

- [ ] `_merge_trend` 类型契约是否如设计所述（元组列表 + 字典列表）？
- [ ] 回归测试 T2 是否能真正复现旧崩溃（改为旧写法应失败）？
- [ ] 测试是否连接测试库而非生产库？
