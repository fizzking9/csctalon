# 20261022 analytics 缓存分层与失效缺口 · 评估报告

**性质**：参考（REF）—— 一次性专项评估，结论已同步到工单，**以代码为准**
**关联**：工单 [#341](https://gitea.kimirock.top/tanzk/csctalon/issues/341)（已排期，P0+P1 一起落地）｜前置 #339 大表物理精简｜规范 `docs/standards/数据源接入与缓存加速规范.md`
**结论一句话**：当前 analytics 缓存命中率 70% 时 p50=35ms / p90=5.6s / p99=10.4s，**慢查询路径比缓存路径慢 2 个数量级**；瓶颈不在"要不要缓存"，而在 TTL 与查询成本脱钩 + 6 个前缀同步后不失效 + 3 个前缀无预热——P0(TTL 分层) 先消灭 5-13s 白屏，P1(补齐失效/预热) 降冷启动成本，预计 p90 从 5.6s 降至 <2s。

---

## 一、生产实测（10-22，Redis uptime 45.3h + nginx 957 条 analytics 请求）

### 1.1 Redis 侧

| 指标 | 值 | 解读 |
|---|---|---|
| 命中率 | **70.0%**（90,747 hit / 38,905 miss） | 低 QPS 场景下正常水平；没有"缓存完全没命中"的证据 |
| key 数 | 40 个 | 远未塞满 256MB（当前 3.13MB，占 1.2%） |
| 淘汰 / 过期 | 0 | 无容量压力 |
| maxmemory_policy | noeviction | 没配淘汰策略（安全，因为远远用不到） |

### 1.2 nginx 侧 analytics 接口延迟分布

| 指标 | 值 | 说明 |
|---|---|---|
| 样本 | 955 | 10-21 11:55 ~ 10-22 09:00，跨越首轮 rebuild_std_big |
| p50 | **35 ms** | 缓存命中路径 |
| p90 | **5.59 s** | 缓存未命中路径 |
| p99 | **10.39 s** | 未命中叠加排队 |
| max | **13.62 s** | — |
| avg | 1.31 s | — |

**缓存命中 vs 未命中 = 35ms vs 5.6s → 差 160 倍**。瓶颈明确不是"缓存没开"，而是「缓存配得太松，没真正扛住峰值」。

### 1.3 按接口聚合（慢清单）

| 接口 | avg | p90 | max | n | 缓存状态 |
|---|---|---|---|---|---|
| refund-withdrawal/overview | 6.07 | 13.56 | 13.56 | 4 | ✅ 有缓存，TTL=60s |
| refund-withdrawal/facets | 5.43 | 8.92 | 13.45 | 80 | ✅ 有缓存，TTL=120s |
| refund-withdrawal/detail | 5.07 | 8.57 | 13.62 | 89 | ✅ 有缓存，TTL=60s |
| refund-withdrawal/daily-split | 4.76 | 11.33 | 11.33 | 4 | ✅ 有缓存，TTL=60s |
| reception/per-capita | 2.70 | 5.75 | 5.75 | 3 | — |
| refund/problem-type-detail | 2.02 | 2.02 | 2.02 | 1 | — |
| reception/category-summary | 2.01 | 4.14 | 4.14 | 3 | — |
| refund/group-comparison | 1.80 | 1.80 | 1.80 | 1 | — |
| cash/wallet-vs-withdraw | 1.59 | 2.44 | 2.44 | 4 | — |
| refund/type-trend | 1.55 | 2.34 | 2.34 | 3 | — |
| today/refund | 1.34 | 3.22 | 3.22 | 4 | — |
| reception/overview | 1.32 | 2.45 | 2.45 | 3 | — |
| today/reception | 1.18 | 2.09 | 2.09 | 4 | — |
| refund/decision-overview | 1.07 | 2.94 | 6.59 | 84 | — |

**关键发现**：最慢四个接口**已经用了缓存**（[analytics.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/v1/analytics.py#L164-L213) 的 `cached_call(..., 60s/120s, ...)`），但 TTL 只有 60～120s。一个 5-13s 的查询配 60s TTL → **每分钟至少有一次用户要真实等 5-13s**。

## 二、瓶颈定位：三个叠加层

### 2.1 层一：TTL 与查询成本脱钩（主凶）

现状：**所有 analytics 接口 TTL 都是 60s**（个别 120s），不区分查询成本。

```python
# analytics.py 现状（节选）
return R(data=cached_call(key, cache_ttl_for(start, end, 60),  # ← 60s 硬编码
         A.refund_withdrawal_overview, db, start, end))
```

### 2.2 层二：提现全链路读 `_all` 视图（吃不到 #339 精简红利）

[refund.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/analytics/refund.py) 中 15 处 `FROM std_refund_online_all` / `std_wallet_flow_all`。`_all` 是零存储视图（`SELECT * FROM ods.*`），**必须读宽表全列**——#339 精简出的 `std_refund_online` / `std_wallet_flow` 物理表只被读 12 处（`std_refund_online` 8 处 + `std_wallet_flow` 4 处），大部分 JOIN 链路仍走宽视图。

### 2.3 层三：同步后不失效 + 无预热（冷启动成本）

依据 [数据源接入与缓存加速规范.md §覆盖矩阵](file:///Users/tanzk/dev-tmp/csctalon/docs/standards/数据源接入与缓存加速规范.md#L217-L237)：

| 缺口类型 | 涉及前缀 | 影响 |
|---|---|---|
| 同步后不失效 | `op:` / `cash:` / `cat:` / `operator:` / `summary` | 数据重采修正后最长 60s 内仍返回旧值（历史窗口 TTL 24h 时更久） |
| 无预热 | `reception:` / `refund:` / `rw:` | 进程重启或 TTL 过期后，第一个用户必然吃满 5-13s |

## 三、方案设计（P0 + P1 一起落地）

### 3.1 P0：TTL 按查询成本分层（消灭 5-13s 白屏）

#### 分层矩阵

| 查询成本 | TTL | 覆盖前缀/接口 | 依据 |
|---|---|---|---|
| **> 3s** | **600s** | `rw:` 系列：refund-withdrawal/overview, refund-withdrawal/facets, refund-withdrawal/detail, refund-withdrawal/daily-split, refund-withdrawal/time-distribution, refund-withdrawal/dimensions | 提现全链路是**T-1 对账看板**，天然允许 10 分钟延迟 |
| **1-3s** | **180s** | `cash:` (wallet-vs-withdraw), `refund:` 多数接口（type-trend, decision-overview, problem-type-detail, group-comparison, decision-tree, adoption-stats, device-trend）, `reception:` (per-capita, category-summary) | 日常运营看板，非实时 |
| **< 500ms** | **60s（不变）** | `today:*`, `op:*`, `cat:*`, `operator:*`, `summary*`, `cash:*` 非慢接口 | 实时运营看板，需短 TTL |

#### 冲突说明（必须坦诚）

**拉长 TTL → 数据更旧**，与"希望及时"是反向的。本单**只动提现全链路（rw 域）**，它天然是 T-1 对账语义，用户已接受 T-2（#339 关闭凭证中记录）。

**不碰 `today/*` / `op/*`**——这些是实时运营域，保持短 TTL + 采集后主动失效。

#### 实现方式

在 [redis_client.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/core/redis_client.py) 新增 `cache_ttl_by_cost()`（按查询成本查常量表返回 TTL），analytics.py 里替换硬编码 `60`。**不改路由逻辑、不改 SQL**——纯配置层变更。

### 3.2 P1a：补齐同步后失效（6 个前缀）

#### 需失效的前缀（逐字符对齐）

| 前缀 | 失效触发点 | 调用方式 |
|---|---|---|
| `op:` | `rebuild_cash_flow_wide` 末尾 + 采集 upsert 成功后 | `cache_invalidate_pattern("op:*")` |
| `cash:` | 同上 | `cache_invalidate_pattern("cash:*")` |
| `cat:` | 同上 | `cache_invalidate_pattern("cat:*")` |
| `operator:` | 同上 | `cache_invalidate_pattern("operator:*")` |
| `summary*` | 同上（注意：无前缀，直接 invalidate） | `cache_invalidate_pattern("*summary*")` |

> **铁律**（规范 §数据源接入与缓存加速规范.md）：Redis key 必须逐字符对齐——路由层写 `today:kpi:{start}:{end}`，失效也必须是 `cache_invalidate_pattern("today:*")`，不能漏冒号、不能改大小写。

#### 已有的失效链路（确认无误）

| 前缀 | 触发点 | 状态 |
|---|---|---|
| `refund:` | `rebuild_refund_wide` 末尾 + 退款五路采纳作业 | ✅ 已有 |
| `rw:` | `rebuild_refund_wide` 末尾 | ✅ 已有 |
| `today:*` | `cache_ttl_for` 返回短 TTL（60s）自然过期 | ✅ 已有 |

### 3.3 P1b：补齐预热（3 个前缀）

[cache_warmer.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/cache_warmer.py) 的 `_warm_analytics()` 当前只覆盖 7 个 `today:*` 接口。扩展覆盖：

| 前缀 | 需预热的典型路由 | key 构造 |
|---|---|---|
| `reception:` | reception/overview, reception/per-capita, reception/category-summary | 对齐 analytics.py 路由层构造 |
| `refund:` | refund/type-trend, refund/decision-overview, refund/decision-tree, refund/adoption-stats | 同上 |
| `rw:` | refund-withdrawal/overview, refund-withdrawal/facets, refund-withdrawal/detail (page 1), refund-withdrawal/daily-split | 同上 |

> **预热要选默认参数**：detail 只预热第一页（page=1, page_size=20），不要预热所有排序/筛选组合（空间浪费）。

## 四、方案外：为什么不做 P2（提现全链路物化）

提现全链路的根因是**多表 JOIN 叠加宽表**，#339 只精简了 cs_records/bms_orders 两张宽表，`refund_online_all` / `wallet_flow_all` 仍走 ods 全列。理论上可以仿 #339 建 `std_refund_withdrawal` 日粒度预 JOIN 表。

**不在本单的理由**：

1. **P0 + P1 成本远低于 P2**——TTL 分层是改几个常量，半天能搞定；P2 要新增 migration + 重建作业 + 消费切换，至少 2-3 天。
2. **P0 + P1 收益可量化**——rw 域 600s TTL 后，每分钟从"至少一次 5-13s"变成"10 分钟内零次 5-13s"。
3. **P2 没有"必须做"的触发条件**——如果 P0 + P1 后 p90 仍 > 3s，再评估 P2；否则属于提前优化。

## 五、验收目标

| 指标 | 当前 | 目标 |
|---|---|---|
| analytics p90（nginx rt，排除 today/*） | 5.59s | **< 2.0s** |
| analytics p99 | 10.39s | **< 3.0s** |
| `refund-withdrawal/*` 未命中路径（EXPLAIN） | 5-13s | 同（**P0 只加 TTL，不碰查询本身**——这点必须诚实） |
| 进程重启后首次访问延迟 | 5-13s（冷启动） | **< 200ms**（预热覆盖） |

> **必须诚实**：P0 拉长 TTL 并不会让"缓存未命中时"的查询变快。它只是把"用户遇到未命中"的概率降到接近零。查询本身的 5-13s 仍存在，但只在**极少数**触发（TTL 过期 + 无预热时才会遇到）。
>
> 如果 P0 后 p90 仍 > 2s，说明要么 TTL 还不够长，要么预热没覆盖到，再评估 P2。

## 六、非目标（明确排除）

1. **不做提现全链路物化**（P2，属后续专项）
2. **不引入预聚合 cube / 列存**（无实证必要）
3. **不动 `today/*` / `op/*` 实时运营域的 TTL**（保持短 TTL + 主动失效）
4. **不改 SQL 查询本身**（P0/P1 是配置层 + 失效/预热补齐，不碰 SQL）

## 七、交付清单（与 #341 工单同步）

| # | 交付物 | 性质 |
|---|---|---|
| REF 文档 | 本报告 | docs/reports/20261022-analytics-缓存分层与失效缺口-评估.md |
| 工单 | #341（已创建） | https://gitea.kimirock.top/tanzk/csctalon/issues/341 |
| migration | （P0/P1 无 DDL 变更，不新写） | — |
| 代码变更 | `redis_client.py` + `analytics.py` + `cache_warmer.py` + 5 个 rebuild/upsert 作业（各加 1 行 invalidation 调用） | 待实施 |

---

## 附：TTL 分层 vs "及时获取"的权衡（必须告知使用者）

你接受了 `cs_records` / `bms_orders` 的 T-2 滞后，但**不应该接受 analytics 看板的 10 分钟数据延迟**。本单的方案是**分域处理**：

| 域 | TTL | 数据延迟 | 语义 |
|---|---|---|---|
| `rw:` 提现全链路 | 600s | T-1 + ~10min | 对账看板，天然 T-1 |
| `cash:` / `refund:` 运营汇总 | 180s | T-1 + ~3min | 日常运营 |
| `today:*` 实时运营 | 60s + 主动失效 | 秒级 | 需及时 |

**如果 10 分钟不可接受** → 才需评估 P2（提现全链路物化），不是 P0 的 TTL 分层能解决的问题。
