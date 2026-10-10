# 采集链路可靠性治理 设计（ai-acc 重试/熔断/缺口补全）

**状态**：LOCKED（v1.0）
**日期**：2026-09-04
**关联工单**：`tanzk/csctalon#2`（ai-acc 大范围导出失败的重试、熔断与缺口补全机制治理）
**审计依据**：`docs/debug/debug-cs-record-gap-2026-09-02.md`（09-02 事故）
**架构关联**：`A-20260902-004`（指标告警引擎，LOCKED）/ `A-20260904-001 v1.2`（数据到达健康，LOCKED）
**审查结论**：人工终审通过（2026-09-04）

---

## 〇、范围与边界

本设计是 #2 的实现方案。按调研，#2 的 4 块需求中 **3.4 告警大部分已由 #5/#6 完成**（data_arrival 到达心跳 + Incident 闭环），本设计聚焦剩余三块：

| #2 需求 | 状态 | 本设计覆盖 |
|---|---|---|
| 3.1 错误分级与重试 | ❌ 基本全新建 | ✅ 核心 |
| 3.2 熔断优化（滑动窗口+半开探测） | 🔶 部分（有专项熔断，缺滑动窗口/半开） | ✅ 核心 |
| 3.3 缺口待补队列 | ❌ 全新（无表） | ✅ 核心 |
| 3.4 告警监控 | 🟡 大部分完成（data_arrival）；缺"熔断事件告警" | 🔶 补充熔断事件→Alert/Incident |

**不覆盖**：业务看板/报表（另工单）；BMS/SCRM 之外的采集源接入（后续自动纳入，见 §6）。

---

## 一、背景与问题（来自审计）

09-02 `cs_records` 缺口事故暴露 4 个采集链路可靠性缺陷：

1. **上游瞬时 5xx 被当"确定失败"**：`ai_acc_fetcher` 无错误分类，JSON500/5xx/超时全落 `except Exception` → 当"确定失败"，不做区分重试。
2. **熔断后当天不再探活**：专项熔断（单轮失败即熔断一整天）后，剩余小时跳过该源；上游恢复也不自动续采（无半开探测），只能人工 reset_halt 或等次日/8:00 兜底。
3. **缺口无自愈**：增量同步只接续"上次成功点"，不覆盖历史断层；无"缺口清单"记账 → 错过窗口即永久丢失（9/2 缺口需人工补跑才发现）。
4. **大范围导出一次性失败**：7 天兜底整窗一次性失败即整批丢弃，无"按日/按片降级"。

---

## 二、总体架构

```
┌─────────────────────────────────────────────────────────────┐
│ 抓取层 (ai_acc_fetcher / bms_fetcher)                        │
│  · 错误分类器: transient(5xx/JSON500/超时/连接) vs permanent │
│  · 分片级重试: 失败定位到片 → 按日降级重试                    │
│  · 退避器: 指数/分段退避 (参数化)                             │
└──────────────────────────┬──────────────────────────────────┘
                           │ FetchResult(带 error_class)
┌──────────────────────────▼──────────────────────────────────┐
│ 调度层 (bms_scheduler)                                       │
│  · 滑动窗口熔断: 连续失败 N 次才熔断 (Redis 计数)             │
│  · 半开探测: 熔断后定时放单片试探, 成功即摘除                 │
│  · 熔断事件 → Alert + Incident 自动登记                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ 结果落 bms_sync_logs
┌──────────────────────────▼──────────────────────────────────┐
│ 缺口层 (新增)                                                │
│  · gap_records 表: biz_date + data_type + status 记账         │
│  · 每次同步后更新缺口状态(补齐/部分/缺口)                     │
│  · 次日 5:00 补跑前清点遗漏 → 强制补跑指定日期                │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ 告警 (复用 A-20260902-004 引擎, 已 LOCKED)                    │
│  · data_arrival_* 到达心跳 (#6 已建)                          │
│  · 熔断事件 → critical Alert + Incident (新增)                │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、分块设计

### 3.1 错误分级与重试

#### 错误分类枚举（新）

```
class FetchErrorClass(str, Enum):
    TRANSIENT = "transient"   # 可重试: 5xx / JSON500 / 超时 / 连接重置 / 限流(429/retry-after)
    PERMANENT  = "permanent"  # 不可重试: 401/403(凭证失效) / 400 / 参数错误 / 文件解析错误
```

接入点：
- `ai_acc_fetcher._export_chunk`：HTTP>=500 → TRANSIENT；JSON 500 → TRANSIENT；401/403 → PERMANENT；timeout/connect → TRANSIENT
- `bms_fetcher`：现有"限流/JSON 提前抛错"归 TRANSIENT；其余下载失败归 TRANSIENT
- `FetchResult` 增加 `error_class` 字段（默认 permanent，兼容旧逻辑）

#### 分片级重试（核心，替代"整窗一次失败即丢"）

```
现有: _chunk_time_range(start,end) → 多片 → 任一失败整窗失败
改为: 逐片抓取, 每片失败:
     1) 若 TRANSIENT → 该片指数退避重试(1s,2s,4s... 上限 max_retry 次)
     2) 重试仍失败 → 记录该片失败, 继续抓其它片(不整窗丢弃)
     3) 汇总: 成功片合并入库; 失败片范围写入 gap_records(见 3.3)
```

- **按日降级**：若单次整窗(跨多天)失败，先按"天"粒度再分片重试（每片≤1天），而非直接放弃整窗。天然复用 `_chunk_time_range`（把 max_days 从 7 调到 1 即按日）。
- 成功片仍入库（部分成功 = partial），失败片进缺口队列由补跑机制兜底。

#### 退避器（新，参数化）

```
backoff = min(base * 2**attempt, cap)   # 指数: 1s→2s→4s...
或分段: [1s, 5s, 30s, 60s] (上游限流常用)
尊重 Retry-After 头(若上游返回)
参数: max_retry / base / cap / jitter(±20%)  —— 存入配置(settings), 不硬编码
```

### 3.2 熔断优化

#### 滑动窗口熔断（改）

```
现状: 单轮"重试后仍失败"即熔断一整天(_halted_types)
改为: Redis 计数(或内存+落库):
     key = f"circuit:{biz_date}:{data_type}"
     每轮该源失败 → INCR + 设 TTL(如 6h 滑动窗口)
     连续失败 ≥ N 次(如 3) → 才熔断该源当天
     单次失败但 < N → 只记, 不熔断(下次仍尝试)
参数: FAIL_THRESHOLD=3, WINDOW=6h (可配置)
```

好处：偶发单次失败(如 9/2 15:00 那一次)不会立即熔断，给重试/自愈机会；真持续故障才熔断。

#### 半开探测（新，核心解决"熔断后不探活"）

```
熔断后当天不再傻等:
  熔断的源在剩余白天小时中, 每 X 小时放一次"单片探测":
    取当天最近一次成功片范围后的第一个缺口小时 或 当前小时窗口 做单次抓取
    成功 → 摘除熔断(_halted_types 移除), 恢复该源正常调度
    失败 → 保持熔断, 下一探测窗口再试
探测间隔: HALF_OPEN_INTERVAL=2h (可配置), 复用 _retry_failed_types_once 骨架
```

- 效果：上游 09-03 恢复后，当日最迟 2h 内自动恢复采集，不再"熔断后永久停到人工"。
- 8:00 兜底/5:00 补跑本就忽略熔断，保留（作为深度自愈）。

#### 熔断状态落库 + 告警

```
新表 circuit_events(或复用 alerts):
  熔断触发 → 写 Alert(metric_code=circuit_<type>, level=critical) + Incident 自动登记
  半开探测成功恢复 → Alert recovered + Incident 平息
  (复用 #5/#6 的 _auto_register_ingest_incident/_close_ingest_incident)
```

### 3.3 缺口待补队列

#### 缺口记账表（新表 gap_records）

```
gap_records:
  id, biz_date(业务日期, 北京), data_type, status(open/partial/filled/ignored),
  first_gap_at(首次发现), last_try_at(末次补跑), try_count, error,
  source_range_start/end(缺口时间范围), remark
  UNIQUE(biz_date, data_type)  —— 一源一日一行
```

#### 记账时机（写入口）

| 时机 | 动作 |
|---|---|
| 某源当日同步失败/部分成功(有片失败) | upsert gap_records(biz_date, type) status=open/partial |
| 某源成功同步覆盖该日(全天 success) | gap_records status=filled |
| 手动补导(upload_records imported)覆盖 | 视为 filled |
| data_arrival 到达健康判定 | 该日到达 → 提示可标记 filled |

#### 次日清点 + 强制补跑

```
次日 5:00 补跑前:
  清点 gap_records WHERE biz_date=昨天 AND status IN (open, partial)
  → 对每个缺口 (biz_date, type) 调 sync_all(time_ranges={type: (缺口范围)})
  → 成功 → filled; 失败 → 保留 open, try_count+1 (留待 8:00 兜底/次日再试)
  → 补跑结果记录到 bms_sync_logs(trigger_type=catch_up)
```

- 这替代当前"5:00 固定补昨天整天"——只补真缺口，不重复无缺口源。
- 8:00 近 7 天兜底保留（作为跨日深兜底），与缺口队列协同。

---

## 四、涉及文件（预估）

| 文件 | 改动 |
|---|---|
| `backend/app/services/ai_acc_fetcher.py` | 错误分类、分片级重试、退避、返回 error_class |
| `backend/app/services/bms_fetcher.py` | 错误分类接入、退避参数化(替换固定2s/60s) |
| `backend/app/services/bms_scheduler.py` | 滑动窗口熔断、半开探测、熔断事件→Alert/Incident |
| `backend/app/services/bms_sync.py` | sync_all 支持分片级部分成功、gap 记账钩子 |
| `backend/app/models/gap_record.py` + 迁移 | 新表 gap_records |
| `backend/app/services/gap_queue_service.py` | 缺口记账/清点/强制补跑编排(新) |
| `backend/app/services/metric_registry_service.py` | (可选) circuit_<type> 指标注册(熔断事件) |
| `backend/app/api/v1/alerts.py` | 熔断事件告警接入(复用现有) |

---

## 五、验收标准

1. 5xx/JSON500/超时识别为 TRANSIENT，按分片重试（成功片入库，失败片进缺口）
2. 大范围导出失败：整窗→按日降级重试，单日成功率提升（对比 9/2 事故场景）
3. 熔断：连续失败 ≥N 次才熔断（非单次）；偶发单次失败不熔断
4. 半开探测：熔断后 ≤2h 自动放单片试探，上游恢复自动续采（模拟验证）
5. 熔断事件 → Alert + Incident 自动登记（幂等），恢复 → 平息
6. 缺口队列：gap_records 记账(缺口日自动入库)，5:00 清点强制补跑，成功转 filled
7. 手动补导覆盖缺口 → 自动标记 filled（不重复补跑）
8. 现有同步/告警回归：pytest/ruff/CI 全绿，审查无 CRITICAL

---

## 六、扩展性与风险

### 新源自动纳入
- 缺口记账/补跑按 `data_type` 通用（不硬编码源），新源接入自动获得：错误分类重试 → 熔断半开 → 缺口队列 → data_arrival 告警（#6 注册表驱动）。
- 只需新源遵守 `FetchResult.error_class` + 落 `bms_sync_logs` 即可。

### 风险与缓解
| 风险 | 缓解 |
|---|---|
| 重试过量拉取(上游限流) | 指数退避 + Retry-After 尊重 + 分片而非整窗 + max_retry 参数化 |
| 熔断阈值误调致频繁熔断/漏熔断 | 阈值参数化 + 半开探测兜底(熔断也自动试探恢复) |
| 缺口表膨胀 | 只记 open/partial, filled 后清理或保留最近 N 天 |
| 对现有调度影响 | 熔断逻辑向后兼容(单次失败不再立即熔断=更宽松), 有回归测试 |
| 幂等 | 补跑走 sync_all(upsert 幂等), gap 记账 UNIQUE 去重 |

---

## 七、灰度与上线

1. 本地开发验证（模拟 5xx/熔断/恢复/缺口场景）
2. 生产先启用错误分级重试（改动 ai_acc_fetcher，风险低），观察 3-5 天
3. 再启用滑动窗口熔断 + 半开探测（改调度器，重点观察）
4. 最后启用缺口队列（新表+补跑编排，需对齐 5:00 现有逻辑）
5. 逐步灰度，每步可回滚

---

## 八、关联

- 工单：`#2`（本设计对应实现）
- 事故审计：`docs/debug/debug-cs-record-gap-2026-09-02.md`
- 架构：`A-20260902-004`（指标告警引擎）/ `A-20260904-001 v1.2`（数据到达健康，告警部分）
- 运维：`docs/ops/数据采集-运维手册.md`
