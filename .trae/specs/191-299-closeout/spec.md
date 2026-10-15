# Spec：`#191 收口` + `#299 收口`

**性质**：需求（REQ） · 一次性任务规格（非 META，不约束其他任务）
**日期**：2026-10-15
**关联工单**：`#191`（E 路恒 0 行治理，open）· `#299`（device_combo 历史回补，open）

---

## 一、已核实事实（2026-10-15，生产 VM400 / release `git-20261015081123`）

| 项 | 事实 | 取证方式 |
|---|---|---|
| #191 核验 | **未通过**（EXIT=1） | 容器内 `check_ai_issue_gates.py --issue 191 --allow-real-db` |
| E 路 scheduled 记录 | 近 **20 天零条** `trigger_type=scheduled`；7 条全部 `manual` | `SELECT ... FROM bms_sync_logs WHERE data_type='refund_record_report'` |
| E 路时效 | 最新业务日 `2026-10-10`，`lag=5`，`stale=True` | `check_freshness()` |
| 运行时配置 | 正确：低频源含 `refund_record_report`；`list_low_freq_schedules → {'refund_record_report': {'hour': 15, 'window_days': 3}}` | 容器内直调服务函数 |
| 容器启动日志 | 16:17 BJT 启动时「低频档回填：今日已执行的小时档 `['2026-10-15:6']`」→ **15 档未执行** | `docker logs csctalon-backend` |

## 二、根因（代码级，已定位到行）

`backend/app/services/bms_scheduler.py` 低频档分支（约 L836–842）：

```python
elif should_run_low_freq(
    now_cn.hour,
    low_freq_hour=now_cn.hour,
    done=False,
    busy=sync_task_manager.is_busy(),
):
```

**未传 `deadline_hour`** → 取默认 `LOW_FREQ_DEADLINE_HOUR = 7`；而 `should_run_low_freq` 第 185 行
`if now_hour >= deadline_hour: return False`，故 **任何 per-source hour ≥ 7 的低频档恒不触发**
（默认 06:00 档因 6 < 7 仍正常，因此长期未暴露）。

到达 `:59` 后落 `elif now_cn.minute >= 59` 分支 → 记 `abandoned` 入 gap 队列，**无同步尝试 → 不写
`bms_sync_logs`**，与「零 scheduled 记录」现象完全吻合。

**影响**：E 路（退款记录报表，对账基准）自 #191 于 09-24 引入「按源可配小时」后**从未自动采集**，
数据仅靠人工手动触发维持，当前落后 5 天。这与 #191 工单注释（L804）声明的语义
「窗口 = 该源配置小时起 1 小时内」相矛盾。

## 三、变更 A：`#191` 根因修复

- **唯一改动点**：`backend/app/services/bms_scheduler.py` L836–842，补 `deadline_hour=now_cn.hour + 1`。
- **不改**：`should_run_low_freq` 本体、`:59` abandon 分支、默认 06:00 档行为、fetch 窗口逻辑。
- **语义恢复**：窗口 = `[该源配置小时, 该源配置小时 + 1)`，与工单注释一致。
- **测试**：
  1. `backend/tests/test_source_freshness.py`（或 `test_bms_scheduler_t1.py`）新增用例：
     `should_run_low_freq(15, low_freq_hour=15, deadline_hour=16, done=False, busy=False) is True`
     与 `... deadline_hour=7 ...` 为 False（锁死根因）。
  2. **漂移守卫**：断言 `bms_scheduler` 源码低频分支调用 `should_run_low_freq` 必带
     `deadline_hour=now_cn.hour + 1`（仿既有「`expected_by_hour` 必须等于采集种子 `low_freq_hour`」守卫写法，防止两处口径再次分叉）。
  3. `pytest` + `ruff` 全绿。

## 四、变更 B：`#299` 顺带项 5（覆盖率异常波动告警）——**待用户拍板**

原文：「同步覆盖缺口监控——低频档跑完对比『devices 无套餐比例』异常波动告警（成功≠完整）」。

- **建议实施，且落在监控模块（非采集核心）**：`backend/app/services/source_freshness_service.py`
  新增只读巡检 `check_device_combo_coverage()`：
  - 指标：设备无有效套餐比例（口径须与 #298/#320/#321 一致，套餐以 `dim_device_combo` 为单一真实源）；
  - 触发：比例超阈值（初值 **5%**，当前基线 **2.62%**）→ `record_system_event(category="sync",
    action="device_combo_coverage", level="warning")`，按日节流（复用现有 `_emit_once`）。
  - 该模块即 #191 交付的「数据源时效与空结果告警」巡检，由调度器每小时调用、**只读、零业务写入**，
    不属于采集链路。
- **降级选项**：若不实施 → 在 #299 关闭凭证中显式写
  `结论: 以 <理由> 降级验收`，禁止静默关闭。

## 五、发布与验收

- **发布**：`git push origin main` → 节点标准入口 `curl ... upgrade.sh | sudo bash -s -- --git`（禁手拼命令）。发布后核 `VERSION` 与迁移 head。
- **#191 关闭条件**（工单原文）：
  1. 生产**连续 2 个自然日** `scheduled` 档 `total_rows > 0` **且** `check_freshness` 的 E 路 `lag_days ≤ 1`、`stale=false`（核验脚本 EXIT=0）；
  2. 0 行场景能触发 `system_events` 告警（已有 `#5385` 历史证据）；
  3. 巡检可复现（`check_freshness` 输出）；
  4. 单测 + `ruff` + `pytest` 全绿；
  5. 若验收窗口内因上游无法达成 → 允许降级并登记待观察项。
- **#299 关闭凭证**：A–F 逐条已有证据（探针结论、切片折半、全量回补 `min=2024-01-01`、残差 34,523 无流水 / 18,136 待 #321、台账 125 success + 64 split 无残留、17 单测）+ 顺带项 5 结论。

## 六、风险与约束

1. **变更 A 属采集调度链路** —— 触发 AGENTS.md「未经用户确认不得修改采集核心」铁律 → **须用户明示批准后再动手**。
2. E 路上游 T+1：修复后需观察 1 个自然日确认 15:00 档能取到数（09-24 实验结论复用，不重做探针）。
3. 变更 B 若被纳入调度触发路径则同属采集链路（亦须批准）；仅作独立每小时只读巡检则属监控范围。
4. 存在 `abandoned` 已入 gap 队列的存量记录：修复后 05:00 缺口清点会重试，属正常收口，不额外处理。
