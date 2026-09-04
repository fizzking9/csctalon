# 采集数据健康告警与事故自动登记 设计（A-20260902-004 的 M5 扩展）

**状态**：LOCKED（v1.1）
**锁定日期**：20260904
**v1.1 修订日期**：20260904（人工评审通过）
**锁定脚本**：scripts/lock-arch-docs.sh
**关联工单**：`tanzk/csctalon#5`（采集数据健康告警 + 事故自动登记闭环）
**架构依据**：`A-20260902-004-metric-alerting-notification-design.md`（LOCKED v1.0，本设计为其 **M5 扩展落地**）
**事故来源**：`#1`（09-02 cs_records 缺口）/ `#2`（ai-acc 治理）
**审查结论**：人工终审通过（2026-09-04）

---

## 修订记录

| 版本 | 日期 | 修订内容 | 原因 |
|---|---|---|---|
| v1.0 | 2026-09-04 | 初版 | — |
| **v1.1** | 2026-09-04 | **§2.1 判定从「入表量相对基线比例」改为「数据到达心跳」**；§2.5/四/六 同步更新 | 生产验证发现接待量日/日内波动大(日间 3334~5874 近 2 倍, 8点 408 vs 9点 1134)，用业务量比例判断采集健康会产生误报。用户确认应改为「正常采集执行且不报错即 OK；该采没采/报错/被动推送未到达才告警」。 |

---

## 〇、范围与边界

本设计只做「**感知 + 闭环**」两层，不做「重试/熔断/缺口自愈」（那些属 #2，另走设计）：

| 层 | 内容 | 归属 |
|---|---|---|
| 感知 | 各数据域「**数据是否按预期到达**」（任一渠道：主动抓取 / 被动推送 / 手动导入）能被自动发现并告警 | 本设计（#5） |
| 通知 | 告警 → 站内信管理员/主管 | 复用 A-20260902-004 M3（已实施） |
| 闭环 | 采集健康告警首次触发 → 自动登记 Incident（含修复指引） | 本设计（#5） |
| 自愈 | 采集失败自动重试 / 按日降级 / 缺口队列 / 熔断半开 | #2（另设计） |

---

## 一、背景与问题

A-20260902-004（LOCKED）已建成指标告警引擎。09-02 事故暴露：**采集链路本身不在监测范围内**，事故次日才人工发现。

v1.0 曾设计为「cs_record 每日入表量相对近 7 日均值比例」作为采集健康判据。**生产实测发现该判据不可靠**：

| 维度 | 实测数据 | 问题 |
|---|---|---|
| 日间总量波动 | 8/27=3334、8/30=5874（近 2 倍） | 接待量是结果性业务指标，受客诉量/活动影响大 |
| 日内小时分布 | 8点=408、9点=1134（2.8 倍跳增） | 非线性流入，按时段线性折算期望失真 |

**结论：业务数据量(接待量)不适合作为"采集健康"判据**——它波动大，会产生误报。

### 根本缺口（v1.1 修订后口径）

「采集健康」的本质是：**数据是否按预期到达系统**，而不是"业务量是否达标"。数据到达有 3 类渠道：

| 渠道 | 触发方 | 日志/记录表 | 覆盖数据域 |
|---|---|---|---|
| ① 主动抓取 | 本系统调度器 | `bms_sync_logs`(+`scrm_sync_logs`) | refund/ticket/wallet/cs_record 等 |
| ② 被动推送 | workbuddy 外部推 / 腾讯文档轮询 | `complaint_sync_logs`、`manual_refund_sync_logs`、`doc_sync_configs.last_sync_at` | 315投诉/卡商投诉/手动退款 |
| ③ 手动导入 | 人工上传 | `upload_records`(source=manual, status=imported) | refund/ticket/wallet/cs_record/投诉等 |

> 关键：手动导入(③)是**合法补数路径**——若自动抓取 failed 但运维已手动补导，数据其实到了，不应误报断流。

---

## 二、总体设计（v1.1）

### 2.1 新增「数据到达健康」指标（核心）

**不新造引擎**，复用 A-20260902-004 的指标注册表 + 扫描 + 通知 + 自愈框架。按**数据域**注册指标，每个指标判"该域数据今天是否按预期到达"。

#### 指标定义（按域，每个一个）

```
code          = data_arrival_<domain>        # 每数据域一个: refund/ticket/cs_record/wallet/complaint/manual_refund
name_cn       = <域>数据到达健康
period        = day
direction     = higher_better                # 到达即健康; 阈值语义: 用"距今未到达小时数"
unit          = -                            # 该指标值仅用于展示, 告警靠 data_ok 状态
data_sources  = 该域涉及的渠道日志表
```

#### 求值函数（核心：看"到达心跳"，不看业务量）

对每个数据域，判断"当日是否有任一渠道的成功到达记录"，并给出状态：

```
def _eval_data_arrival(db, domain, ts, te) -> dict:
    # ① 主动抓取到达: bms_sync_logs 该域当日存在 status='success'
    #    (cs_record/refund/ticket/wallet → data_type 过滤; ticket 含 ticket_finish 别名)
    # ② 被动推送到达: complaint_sync_logs / manual_refund_sync_logs 当日存在行
    #    (success/partial 都算"推了", partial=有失败行但主体到达)
    #    + 投诉域可参考 doc_sync_configs.last_sync_at(系统侧腾讯文档轮询)
    # ③ 手动导入到达: upload_records(source='manual' 或 'bms', status='imported') 当日存在
    #    —— 手动补导 = 数据已到, 视为该域今日已到达

    reached = (①有success) or (②有推送) or (③有imported)
    last_arrival = 最近一次到达时间(跨①/②/③取 max)

    # 各渠道异常信号(不看业务量):
    #   sync_failed = ①当日存在 status='failed'(主动抓取明确报错)
    #   push_failed = ②当日存在 status='failed'(被动推送明确报错)
    #   no_arrival   = 已过预期窗口仍 reached=False(该采没采 + 没推 + 没手动导)

    return { value: 距上次到达小时数(展示) 或 0,
             data_ok: True / "sync_failed" / "push_failed" / "no_arrival",
             detail: 各渠道最近到达/失败时间 + 失败次数 }
```

#### 判定阈值（按域预期节奏，参数化可配置）

| 数据域 | 预期到达节奏 | 判定 |
|---|---|---|
| refund/ticket/wallet/cs_record(主动抓取) | 白天每小时调度 | 当日无任何 success 且**已过 9 点首采保护** → `no_arrival`；有 failed → `sync_failed` |
| 315投诉/卡商投诉(被动推送) | 实测约每 1-2h 一次(09-23点) | 白天(09:00-23:00)**连续 ≥4h 无推送** 且无手动导入 → `no_arrival`；推送 failed → `push_failed` |
| 手动退款(被动推送) | 实测约每小时一次(09-24点) | 同上(≥4h 无推送 → `no_arrival`) |

> 首采保护(9点)与冷启动保护沿用 v1.0 设计(见 §六)。
> **完全不比较业务数据量** → 彻底消除 v1.0 的误报根源。

### 2.2 引擎 `data_ok` 语义扩展

沿用 v1.0（已实现）：`meta["kind"]="ingest_health"` + data_ok 字符串标记：
- `"sync_failed"`/`"push_failed"`/`"no_arrival"` → 采集/推送异常，**按 critical 告警**
- `"cold_start"`（基线/样本不足）→ 只记录不告警
- 业务指标 data_ok=False 仍跳过（行为不变）

### 2.3 告警 → 自动 Incident 登记（闭环）

沿用 v1.0（已实现）：采集健康首次 critical → 自动登记 Incident（幂等），恢复 → 自动平息。告警内嵌修复指引。

### 2.4 通知与处置指引

沿用 v1.0：站内信管理员/主管 + detail 内嵌运维手册补跑/排查命令。

### 2.5 运维手册更新

§2.2「看每日入表量」升级为「看各域数据到达」：系统每小时扫描各域到达心跳；任一域异常 → 站内信告警 + 自动 Incident 登记。

---

## 三、涉及文件（v1.1）

| 文件 | 改动 |
|---|---|
| `backend/app/services/metric_registry_service.py` | **替换** `ingest_volume_cs_record` 为按域 `data_arrival_<domain>` 求值函数（看到达日志, 不看业务量） |
| `backend/app/api/v1/alerts.py` | `_scan_metric_rule` 已支持 data_ok 字符串 → critical + Incident 钩子（v1.0 已实现, 复用） |
| `alembic/versions/` | 新 migration：停用旧 `ingest_volume_cs_record` 指标/规则；注册 `data_arrival_*` 域指标 + 规则 |
| `docs/ops/数据采集-运维手册.md` | §2.2 改"看数据到达" |

---

## 四、验收标准（v1.1）

1. 指标注册表出现 `data_arrival_<domain>`（refund/ticket/cs_record/wallet/complaint/manual_refund），scan_alerts 能求值
2. 旧 `ingest_volume_cs_record` 指标/规则已停用（不再误报）
3. 模拟"某域当日抓取 failed 且无手动导入"→ `sync_failed` → critical + Incident
4. 模拟"被动推送域白天 ≥4h 无推送"→ `no_arrival` → critical
5. 模拟"自动抓取 failed 但有手动导入(imported)"→ **不告警**(数据已到达) —— 关键回归
6. 首次 critical → Incident 自动登记(幂等)；恢复 → 自动平息
7. 现有业务指标行为不变；pytest/ruff/CI 全绿；审查无 CRITICAL

---

## 五、灰度与上线

1. 本地开发验证（抓取失败/推送停滞/手动导入兜底/恢复 4 场景）
2. 生产先启用 complaint/manual_refund(被动推送) 与 cs_record(主动)，观察 3-5 天
3. 确认稳定后扩展到 refund/ticket/wallet

---

## 六、风险与限制

- **误报**：靠"到达心跳"而非业务量，已消除 v1.0 主误报源；阈值(≥4h)可配置、告警可确认/忽略
- **被动推送无系统侧探测**：workbuddy 若整段不推，只能等超时判定(≥4h)才报——这是被动模式的固有限制，可后续用腾讯文档轮询兜底
- **冷启动**：新指标刚上无历史"到达节奏"样本 → 首 3 天只记录不告警(或按预期窗口直接判, 因窗口是业务约定非统计)
- **Incident 幂等**：按 (domain, period) 去重

---

## 七、关联

- 架构：`docs/arch/A-20260902-004-metric-alerting-notification-design.md`（LOCKED）
- 事故：`#1`（09-02 缺口闭环）/ `#2`（ai-acc 治理：重试/熔断/缺口队列另设计）
- 工单：`#5`（本设计对应实现）
- 运维：`docs/ops/数据采集-运维手册.md`
