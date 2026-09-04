# 采集数据健康告警与事故自动登记 设计（A-20260902-004 的 M5 扩展）

**状态**：LOCKED（v1.0）
**锁定日期**：20260904
**锁定脚本**：scripts/lock-arch-docs.sh
**关联工单**：`tanzk/csctalon#5`（采集数据健康告警 + 事故自动登记闭环）
**架构依据**：`A-20260902-004-metric-alerting-notification-design.md`（LOCKED v1.0，本设计为其 **M5 扩展落地**）
**事故来源**：`#1`（09-02 cs_records 缺口）/ `#2`（ai-acc 治理）
**审查结论**：人工终审通过（2026-09-04）

---

## 〇、范围与边界

本设计只做「**感知 + 闭环**」两层，不做「重试/熔断/缺口自愈」（那些属 #2，另走设计）：

| 层 | 内容 | 归属 |
|---|---|---|
| 感知 | cs_record 等采集源「入表量骤降/断流」能被指标引擎**自动发现并告警** | 本设计（#5） |
| 通知 | 告警 → 站内信管理员/主管 | 复用 A-20260902-004 M3（已实施） |
| 闭环 | 采集健康告警首次触发 → 自动登记 Incident（含修复指引） | 本设计（#5） |
| 自愈 | 采集失败自动重试 / 按日降级 / 缺口队列 / 熔断半开 | #2（另设计） |

---

## 一、背景与问题

A-20260902-004（LOCKED）已建成指标告警引擎：`metric_registry`（5 个业务指标）+ `metric_thresholds`（四级阈值）+ `scan_alerts`（每小时扫描）+ `notifier`（站内信）+ 恢复自愈 + `data_ok` 数据健康保护。

但 09-02 事故暴露：**采集链路本身（cs_record 入表量）不在指标监测范围内**。事故 09-02 发生，09-03 才人工发现，补跑靠人工。运维手册 §2.2「看每日入表量」目前是纯人工巡检。

### 根本缺口

1. 指标注册表只有**业务指标**（退款挽回率/退款笔数/投诉积压…），缺**采集健康维度**（各源每日入表量）。
2. 现有 `data_ok=False → 跳过告警` 的防假告警设计，**不适用于采集健康**：
   - 业务指标：SCRM 同步失败导致指标=0 → 是"数据缺失"，应跳过告警（防把业务误报为恶化）✅ 现有语义正确
   - 采集健康：**采集断流导致入表量=0/骤降 → 恰恰是我们要告警的故障信号**，却被 data_ok 吞掉 ❌
3. 事故发现后无**自动 Incident 登记** → 事件闭环依赖人工补录（#1 教训：补跑了但工单/审计没留痕）。

---

## 二、总体设计

### 2.1 新增采集健康指标 `ingest_volume`（核心）

**不新造引擎**，完全复用 A-20260902-004 的指标注册表 + 阈值 + 扫描 + 通知 + 自愈框架，只新增 1 个指标定义 + 1 个求值函数。

#### 指标定义

```
code          = ingest_volume_<data_type>      # 每个采集源一个(首期只 cs_record)
name_cn       = <源>每日入表量                  # 如 "智能助手记录每日入表量"
period        = day                            # 每日求值(与看板同源同口径)
direction     = higher_better                  # 入表量越多越健康(阈值层做"骤降"语义)
unit          = 条
data_sources  = [cs_record] + 采集运行日志(bms_sync_logs)
```

#### 求值函数（核心：不是简单 count，而是"采集健康度"）

```
def _eval_ingest_volume(db, ts, te) -> dict:
    today_cnt   = count(cs_record where operation_time in 北京当日)
    baseline    = 近7日(不含当日)同源 operation_time 日均入表量
    hour_now    = 当前北京小时数
    expected    = baseline × 当日已过计划采集小时的归一系数   # 避免凌晨误报
    ratio       = today_cnt / expected(当 expected>0)

    采集运行健康检查(结合 bms_sync_logs):
      sync_failed = 当日该源存在 status='failed' 记录(trigger_type in scheduled/manual)
      sync_done   = 当日已成功执行的计划小时数

    data_ok = (今日确有采集尝试) or (ratio 未跌破阈值)
    # 语义: 采集健康指标下, data_ok=False(断流/全失败) 时返回特殊标记,
    #       由扫描器识别为"采集异常告警"而非"业务数据缺失跳过"(见 2.2)

    return {
      value:    ratio×100 或今日入表量(展示用)
      data_ok:  True(正常) / "no_sync"(今日无任何采集) / "sync_failed"(今日采集失败)
      detail:   今日量 vs 基线 + 失败次数 + 已跑小时
    }
```

#### 分级（阈值参数化，写入 metric_thresholds，可配置）

| 档 | 判定（相对近7日同时段基线） | 含义 |
|---|---|---|
| 正常 | ratio ≥ 70% | 采集健康 |
| 预警 warning | ratio < 70% 且 ≥ 40% | 可能漏采，观察 |
| 告警 critical | ratio < 40% 或 data_ok ∈ {no_sync, sync_failed} | 断流/上游故障/熔断，需立即处理 |

> 阈值 70%/40% 为初始建议值，存入 `metric_thresholds` 表，运营可在预警中心调整，不硬编码。
> 用"相对近 7 日均值"而非绝对量，避免业务自然波动（周末/活动日）误报。

### 2.2 引擎 `data_ok` 语义扩展（关键修正点）

A-20260902-004 的 `_scan_metric_rule` 对 `data_ok=False` 一律跳过。本设计引入**采集健康指标的第三种 data_ok 状态**：

| data_ok | 语义 | 现有处理 | 新处理 |
|---|---|---|---|
| `True` | 数据完整 | 正常分级 | 不变 |
| `False` | 业务数据缺失（如 SCRM 没同步） | 跳过告警（防假） | 不变（业务指标仍如此） |
| `"sync_failed"` / `"no_sync"` | **采集本身失败/未跑**（采集健康指标专用） | —（新） | **按 critical 告警**（采集断流=故障信号） |

实现：`_scan_metric_rule` 增加分支——当 `meta["kind"] == "ingest_health"` 且 `data_ok` 是字符串标记时，视为采集异常 → 生成 critical 告警，不走"跳过"。

> 兼容性：不影响现有 5 个业务指标（其 data_ok 仍为 bool/False → 跳过）。新增字段 `meta["kind"]` 默认 "business"，仅采集健康指标标 "ingest_health"。

### 2.3 告警 → 自动 Incident 登记（闭环）

采集健康指标首次触发 critical 时，在 `_scan_metric_rule` 中追加钩子：

```
if 采集健康 critical 且该 period 未登记过 Incident:
    incidents.create(
       title      = "数据采集异常: <源> <date> 入表量骤降(<今日>/基线<均值>)"
       source     = "数据采集"
       status     = REGISTERED
       severity   = 高/中(按 ratio)
       detail     = 同步日志摘要(failed次数/熔断?) + 运维手册补跑指引
       source_type= "系统自动(采集健康告警)"
    )
```

幂等：同一 (data_type, period_start) 已登记则不重复建（Incident 用 JSONB related_tickets 或独立索引去重）。

**闭环价值**：#1 的教训是"补跑了但没留痕"。自动登记后，每次采集事故**自动进入 incidents 台账**，有状态(登记→处理→闭环)、可复盘、可追踪——即使运维当时只做了补跑，事件也在系统里有记录，供事后复盘/周报。

### 2.4 通知与处置指引

- 告警 → 站内信管理员/主管（复用 `notify_all_admins_and_supervisors`）
- **告警 detail 内嵌运维手册单日补跑命令**（已有 SOP，从 docs/ops/数据采集-运维手册.md §4.1 引用），运维收到即可照做

### 2.5 运维手册更新

§2.2「看每日入表量」从"人工每天看"升级为：
- 系统每小时自动扫描入表量指标
- 骤降 → 站内信告警 + 自动登记 Incident
- 运维动作 = 确认告警 → 查 Incident → 按 §4.1 单日补跑 → 在 Incident/工单标记闭环

---

## 三、涉及文件（预估）

| 文件 | 改动 |
|---|---|
| `backend/app/services/metric_registry_service.py` | 新增 `_eval_ingest_volume` + `METRIC_DEFS` 注册 `ingest_volume_cs_record`（kind=ingest_health） |
| `backend/app/api/v1/alerts.py` | `_scan_metric_rule` 增加 data_ok 字符串标记 → critical 分支 + Incident 登记钩子 |
| `backend/app/models/incident.py` | 无需改表（字段已够）；如需来源标识可能加枚举值 |
| `backend/app/api/v1/metrics.py` | 指标详情展示支持 kind 标注（次要） |
| `alembic/versions/` | 新增 migration：`metric_thresholds` 种子数据(ingest_volume_cs_record 阈值) + `metric_registry` 元数据行 |
| `docs/ops/数据采集-运维手册.md` | §2.2 升级 + §5 待建能力移除该项 |

---

## 四、验收标准

1. 指标注册表出现 `ingest_volume_cs_record`，`scan_alerts` 能求值并写快照
2. 配置阈值后：模拟"当日入表量 < 40% 基线"→ 触发 critical 告警 + 站内信
3. 模拟"当日无任何采集(no_sync)"→ 触发 critical（不被 data_ok 吞掉）
4. 首次 critical → incidents 自动登记一条（幂等：同 period 不重复）
5. 次日恢复 → 告警 recovered 自愈通知
6. 现有 5 个业务指标行为不变（回归）
7. pytest 新增用例全绿 + ruff 通过 + CI 通过 + 审查无 CRITICAL

---

## 五、灰度与上线

1. 本地开发验证（模拟断流/骤降/恢复三场景）
2. 生产先只注册 cs_record 一个源，观察 3-5 天告警准确性（防误报调阈值）
3. 确认稳定后，可选扩展到 refund/ticket/wallet 等其它源

---

## 六、风险与限制

- **误报风险**：业务真波动（如大促后骤降）可能触发告警 → 靠"相对近 7 日均值"缓解，且阈值可配置、告警可确认/忽略
- **data_ok 扩展影响面**：仅影响 kind=ingest_health 的指标，业务指标零影响（回归测试覆盖）
- **基线依赖历史数据**：新部署初期近 7 日基线不足 → 需"冷启动期"(如首 3 天只记录不告警) 或基线不足时降级为绝对值阈值
- **Incident 自动登记频率**：按 (data_type, period) 幂等，避免同一事故刷多条

---

## 七、关联

- 架构：`docs/arch/A-20260902-004-metric-alerting-notification-design.md`（LOCKED）
- 事故：`#1`（09-02 缺口闭环）/ `#2`（ai-acc 治理：本设计不覆盖的重试/熔断/缺口队列）
- 工单：`#5`（本设计对应实现）
- 运维：`docs/ops/数据采集-运维手册.md`
