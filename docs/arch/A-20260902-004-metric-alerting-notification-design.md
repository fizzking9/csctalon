# 运营指标阈值化与预警通知机制 设计方案

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


> 版本: v0.3 · 2026-08-21
> 状态: M1~M3 已实施(最小闭环), M4 通道架构已就绪(站内信已接, 群机器人/邮件可配置接入), M5 扩展待定
> 范围: 指标注册表 + 多级阈值(优秀/达标/预警/告警) + 预警引擎 + 通知通道抽象

## 0. 实施状态(2026-08-21)

| 里程碑 | 状态 | 说明 |
|---|---|---|
| M1 指标与阈值基础 | ✅ 已实施 | 5 个指标注册(recovery_rate/refund_count/ticket_backlog/external_complaint_7d/internal_complaint_daily) + 四级阈值(metric_thresholds 留痕) |
| M2 预警引擎 | ✅ 已实施 | scan_alerts 注册表驱动: 四级分级/区间幂等/升级/恢复自愈/数据健康检查; 定时每小时自动扫描 |
| M3 站内通知 | ✅ 已实施 | 告警触发/恢复 → 站内信(notify) + 投递记录(alert_deliveries) |
| M4 推送通道 | 🟡 架构已就绪 | notification_channels 表 + Channel 抽象(站内信已接; webhook 群机器人/邮件通道类已实现, 配置即用); 前端通道管理+测试按钮 |
| M5 扩展联动 | ⏳ 待定 | 指标扩充/聚合/与 Incident/Measure 联动 |

**实现与设计差异(§8.1)**: 告警记录复用现有 `alerts` 表(扩展 metric_code/metric_value/threshold_value/period_start/period_end/data_ok/recovered_at 字段), 未新建 alert_events 表。

**新增后端文件**: `app/models/metric.py` / `app/models/notification_channel.py` / `app/services/metric_registry_service.py` / `app/services/notification_channel.py` / `app/api/v1/metrics.py` / `app/api/v1/channels.py`; 迁移 `n9a8b7c6d5e4f_add_metric_alerting.py`(含指标/阈值/站内信种子数据)。

**前端**: `web/src/views/management/Alerts.vue` 升级为四 Tab(指标水位/预警规则/预警记录/通知通道); 新增 `web/src/api/metric.js`、`web/src/api/channel.js`。

---

## 1. 背景与目标

当前系统的看板(运营看板/今日看板/投诉分析)已实时计算大量运营指标(退款挽回率、三源投诉、积压工单、首响时长等),但**指标的"好坏"完全靠人肉看数字判断**——没有"低于多少要盯、低于多少要报警"的系统化机制。

目标:

1. **指标化监测**: 运营指标有明确的"正常/预警/告警"水位线,系统按周期自动评估
2. **阈值参数化**: 优秀/达标/预警/告警四级阈值全部由系统参数配置(可改、可留痕),不硬编码
3. **预警自动生成**: 指标越线自动生成预警记录,走完整生命周期(待确认→已确认→已恢复)
4. **通知多渠道**: 预警触发后站内信通知(已有),并预留企业微信/钉钉/飞书群机器人、邮件等推送通道(可扩充)

核心原则:**预警监测的指标口径必须与看板同源同口径**,先有指标注册表,再有阈值与规则,避免"看板一个数、预警另一个数"。

---

## 2. 现状盘点

### 2.1 已有基础(可复用)

| 模块 | 位置 | 现状 |
|---|---|---|
| 预警规则/记录 | `app/models/alert.py` AlertRule/Alert | 已有: name/type/condition(JSON)/threshold/enabled; 记录含 level/status(pending/confirmed/ignored) |
| 预警扫描 | `app/api/v1/alerts.py` scan_alerts | 4 种硬编码规则(退款激增/工单积压/事件激增/投诉激增), 单一 threshold, level 由 threshold×2 推断, **当日幂等** |
| 定时调度 | `app/services/bms_scheduler.py` | 已有每小时预警扫描钩子(当日幂等), 可复用 |
| 站内信 | `app/services/notifier.py` notify() | 统一入口, 支持按用户/角色, Notification 表, 前端有角标 |
| 前端预警中心 | `web/src/views/management/Alerts.vue` | 规则 CRUD + 预警记录 + 手动扫描 + 确认/忽略 |
| 指标计算 | `app/services/analytics.py` | 各看板指标已实时计算(如 refund recovery), 口径已沉淀 |

### 2.2 缺口

1. **阈值单一数值** → 无法表达"达标(绿)/预警(黄)/告警(红)"三级水位
2. **规则类型硬编码** → 加一个新指标预警必须改 scan_alerts 代码, 不可配置
3. **只支持"≥阈值"的激增类判断** → 无法表达"挽回率 < 60%"这类"越低越差"指标
4. **指标无注册表** → 口径散落, 规则无法引用统一指标定义
5. **预警只入库不通知** → 不调 notify(), 无任何推送通道
6. **无告警生命周期闭环** → 无"恢复/解除"概念, 无效告警会堆积
7. **无数据健康保护** → SCRM 同步失败导致指标=0 会误报"恶化告警"

---

## 3. 总体架构

```
┌──────────────────────────────────────────────────────┐
│  指标注册表 (metric_registry)                         │
│  每个指标: code / 口径函数 / 周期 / 方向 / 依赖数据源  │
└───────────────┬──────────────────────────────────────┘
                │ 引用
┌───────────────▼──────────────────────────────────────┐
│  阈值参数 (metric_thresholds)                        │
│  metric_code + 优秀线/达标线/预警线/告警线 + 生效区间   │
└───────────────┬──────────────────────────────────────┘
                │ 计算
┌───────────────▼──────────────────────────────────────┐
│  预警引擎 (alert_engine)                             │
│  周期扫描 → 数据健康检查 → 指标求值 → 与阈值比较       │
│  → 幂等/升级/静默 → 生成告警记录                      │
└───────────────┬──────────────────────────────────────┘
                │ 触发
┌───────────────▼──────────────────────────────────────┐
│  通知分发 (notification_channel)                     │
│  站内信(先) → 企业微信/钉钉/飞书群机器人 → 邮件(后)     │
└──────────────────────────────────────────────────────┘
```

分层职责:

- **指标注册表**: 定义"算什么"(口径), 是唯一事实源, 看板与预警共用
- **阈值参数**: 定义"多好算好"(水位线), 可配置可留痕
- **预警引擎**: 定义"何时喊"(触发), 与指标/阈值解耦, 新增指标无需改引擎
- **通知通道**: 定义"喊给谁、怎么喊", 通道可插拔

---

## 4. 指标注册表(核心)

### 4.1 设计要点

- 每个指标注册一条元数据:**计算函数** + **周期** + **方向** + **依赖数据源**
- 指标值由统一求值器调用注册的计算函数得到, **与看板共用同一函数**(同源同口径)
- 注册方式: 代码注册(函数式) + 数据库元数据(阈值引用), 避免重复实现

### 4.2 首批指标清单(试点范围)

| code | 指标名 | 方向 | 周期 | 依赖数据源 | 口径来源 |
|---|---|---|---|---|---|
| recovery_rate | 退款挽回率 | 越高越好 | 日 | SCRM cs_record | analytics 退款挽回计算 |
| refund_count | 当日退款笔数 | 越低越好 | 日 | refund | 看板 refund kpi |
| ticket_backlog | 待处理投诉工单积压 | 越低越好 | 日 | service_tickets | 投诉 backlog.total |
| external_complaint_7d | 近7天外部投诉量 | 越低越好 | 日 | external_complaints | 投诉 supplementary |
| internal_complaint_daily | 当日内部投诉量 | 越低越好 | 日 | service_tickets | 投诉 internal.total |
| first_response_sec | 首次响应时长(均值) | 越低越好 | 日 | scrm_session | SCRM 绩效 |
| transfer_rate | 会话转接率 | 双向 | 日 | scrm_session_transfer | SCRM 绩效 |
| sync_health | 数据同步成功率 | 越高越好 | 日 | scrm_sync_log/bms_sync_log | 同步日志 |

> 指标清单按需扩充: 值班在线率、工作效率值、四路退款占比、套餐充值量等。
> 重要:**先有指标, 后有阈值**; 未注册的指标不参与预警。

---

## 5. 三级阈值与规则模型

### 5.1 阈值语义(四级: 优秀/达标/预警/告警)

| 级别 | 含义 | 运营动作 | 颜色 |
|---|---|---|---|
| 优秀(excellent) | 指标超出预期, 明显优于正常水位 | 通报表扬, 提炼可复制的经验 | 蓝 |
| 达标(green) | 指标处于正常区间 | 无需处理 | 绿 |
| 预警(yellow) | 指标偏离正常,需关注 | 盯一盯,排查原因 | 黄 |
| 告警(red) | 指标严重偏离,需干预 | 立即处理,升级 | 红 |

- **越高越好型**(如挽回率): 阈值 = 下限线。`value ≥ 优秀线` → 优秀; `value < 预警线` → 预警; `value < 告警线` → 告警; 其余 → 达标
- **越低越好型**(如退款笔数/积压): 阈值 = 上限线。`value ≤ 优秀线` → 优秀; `value > 预警线` → 预警; `value > 告警线` → 告警; 其余 → 达标
- **优秀线可选**: 不配置优秀线则退化为"达标/预警/告警"三级, 不产生"优秀"状态
- **仅 预警/告警 触发告警记录与通知**; 优秀/达标仅展示水位状态(优秀可另行配置"通报表扬"通知, 默认不推)
- 方向由指标注册表声明, 阈值表只存数值, 引擎按方向解释

### 5.2 规则 = 指标 × 阈值 × 调度

规则配置项:

```
metric_code     引用指标注册表
warning_threshold   预警线
critical_threshold  告警线
period          日/周/月
enabled         是否启用
channels        通知通道列表(默认站内信)
recipient_roles 通知角色(admin/supervisor)或指定用户
silence_window  静默时段(如 00:00-08:00 不推送,可选)
```

> 兼容现有 AlertRule: 规则仍存 alert_rules 表, 字段扩展(见 §8), 现有 4 类硬编码规则迁移为注册表指标+阈值。

---

## 6. 预警引擎

### 6.1 扫描流程

```
1. 数据健康检查(防假告警)
   - 指标依赖的数据源今日同步是否成功(sync_health)
   - 数据量是否合理(如 SCRM 会话量低于前3日均值 20% → 视为数据异常)
   - 数据异常 → 跳过该指标, 生成"数据异常"提示(不产生告警)
2. 指标求值(调用注册表计算函数, 与看板同口径)
3. 与多级阈值比较 → 确定级别(优秀/达标/预警/告警; 无越线时若之前有告警则生成"已恢复"记录)
4. 幂等去重: 同规则同周期同级别最多一条 pending
5. 升级: 连续 N 个周期处于预警且未处理 → 升级为告警
6. 生成 Alert 记录 + 通知分发(§7)
```

### 6.2 关键规则

| 规则 | 说明 |
|---|---|
| 当日幂等 | 同规则同周期同级别不重复生成(复用现有机制) |
| 连续恶化升级 | 预警持续 N 天未处理且指标继续恶化 → 自动升级告警 |
| 恢复自愈 | 指标回到达标线 → 自动生成/更新"已恢复"状态并通知 |
| 静默窗口 | 配置的时段内不推送(仅入库), 避免夜间打扰 |
| 数据保护 | 数据不完整/异常时不告警, 单独标记(防假告警, 见 6.3) |
| 批量聚合 | 同批多条同类预警合并为一条摘要(可选, 后期) |

### 6.3 数据健康保护(重要)

- 预警求值前先跑 `sync_health` 检查: 今日 SCRM/BMS 是否同步成功
- 指标值出现"断崖式"变化(如为 0 或骤降 80%)时, 先判定为数据异常而非业务恶化
- 数据异常与业务告警分开展示, 避免"假告警"干扰决策

---

## 7. 通知通道抽象

### 7.1 通道接口

```python
class NotificationChannel(Protocol):
    def send(self, ctx: AlertContext, recipient: list) -> ChannelResult: ...
```

- `站内信(InAppChannel)` — 复用现有 `notifier.notify()`, **首发实现**
- `企业微信群机器人 / 钉钉 / 飞书群机器人(WebhookChannel)` — 二期
- `邮件(EmailChannel)` — 三期

### 7.2 配置模型

- 通道注册表: `notification_channels` 表, 每个通道有 type + config(webhook URL/密钥, 加密存储)
- 规则级配置: 每条规则选择通知通道 + 接收对象(角色/指定用户)
- 测试能力: 每个通道提供"发送测试"按钮(前端), 验证配置可用
- 发送失败降级: 群机器人失败 → 自动降级为站内信, 并记发送日志

### 7.3 通知去重

- 同一告警不重复推送: 以 Alert.id 为幂等键, 推送状态记录在 alert_deliveries 表
- 恢复通知与触发通知各推送一次

---

## 8. 数据模型(表结构草案)

### 8.1 新建

```sql
-- 指标注册表(元数据; 计算函数在代码层注册)
CREATE TABLE metric_registry (
  id            BIGSERIAL PRIMARY KEY,
  code          VARCHAR(64) UNIQUE NOT NULL,     -- 指标code, 与代码注册对应
  name_cn       VARCHAR(128) NOT NULL,
  direction     VARCHAR(8)  NOT NULL,            -- higher_better / lower_better
  period        VARCHAR(8)  NOT NULL DEFAULT 'day',  -- day/week/month
  data_sources  JSONB DEFAULT '[]',              -- 依赖数据源, 供健康检查
  description   TEXT,
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- 阈值参数(四级水位: 优秀/达标/预警/告警, 可留痕)
CREATE TABLE metric_thresholds (
  id             BIGSERIAL PRIMARY KEY,
  metric_code    VARCHAR(64) REFERENCES metric_registry(code),
  excellent_value DOUBLE PRECISION,              -- 优秀线(可选, 不配则无优秀状态)
  warning_value  DOUBLE PRECISION NOT NULL,      -- 预警线
  critical_value DOUBLE PRECISION NOT NULL,      -- 告警线
  unit           VARCHAR(16),                    -- % / 笔 / 秒 ...
  valid_from     TIMESTAMPTZ,                    -- 生效区间(支持历史调参留痕)
  valid_to       TIMESTAMPTZ,
  updated_by     INTEGER REFERENCES users(id),
  updated_at     TIMESTAMPTZ DEFAULT now()
);

-- 告警记录(扩展现有 alert_alerts 或新建 alert_events)
CREATE TABLE alert_events (
  id            BIGSERIAL PRIMARY KEY,
  metric_code   VARCHAR(64),
  rule_id       BIGINT,                          -- 关联 alert_rules
  level         VARCHAR(16),                     -- warning / critical / recovered
  metric_value  DOUBLE PRECISION,
  threshold_value DOUBLE PRECISION,
  period_start  TIMESTAMPTZ,
  period_end    TIMESTAMPTZ,
  status        VARCHAR(16) DEFAULT 'pending',   -- pending/confirmed/ignored/recovered
  data_ok       BOOLEAN DEFAULT TRUE,            -- 数据健康标记(防假告警)
  detail        TEXT,
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- 通知通道配置
CREATE TABLE notification_channels (
  id          BIGSERIAL PRIMARY KEY,
  code        VARCHAR(32) UNIQUE NOT NULL,       -- inapp / wecom_robot / dingtalk_robot / feishu_robot / email
  name_cn     VARCHAR(64),
  channel_type VARCHAR(16),
  config      JSONB,                             -- webhook等, 敏感字段加密
  enabled     BOOLEAN DEFAULT TRUE,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- 通知投递记录(幂等)
CREATE TABLE alert_deliveries (
  id         BIGSERIAL PRIMARY KEY,
  alert_id   BIGINT,
  channel    VARCHAR(32),
  status     VARCHAR(16),                        -- success / failed / degraded
  detail     TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 指标历史值(回溯"为什么触发/持续多久")
CREATE TABLE metric_daily_snapshots (
  id          BIGSERIAL PRIMARY KEY,
  metric_code VARCHAR(64),
  value       DOUBLE PRECISION,
  period_date DATE,
  level       VARCHAR(16),                       -- excellent/green/warning/critical
  UNIQUE (metric_code, period_date)
);
```

### 8.2 复用与兼容

- `alert_rules` 扩展字段: metric_code / warning_value / critical_value / channels / recipient_roles / silence_window(JSONB), 现有 4 类规则迁移
- `Alert` 记录增加 metric_code/level 对齐(或迁移到 alert_events)
- 前端 Alerts.vue 扩展: 规则管理升级为"指标+阈值"配置, 预警记录增加级别/恢复状态

---

## 9. API 与前端规划

### 9.1 API(后端)

```
GET  /api/v1/metrics                      指标注册表列表
GET  /api/v1/metrics/{code}/current      指标当前值+当前级别(前端展示水位)
PUT  /api/v1/metrics/{code}/thresholds    配置/修改三级阈值(留痕)
POST /api/v1/alerts/scan                  手动触发扫描(已有, 扩展)
POST /api/v1/alerts/{id}/recover          手动标记恢复
GET  /api/v1/alerts/events                告警事件列表(含数据异常)
GET  /api/v1/channels                     通道列表
POST /api/v1/channels/{code}/test         发送测试通知
```

### 9.2 前端(Alerts.vue 升级)

1. **指标水位视图**: 指标列表 + 当前值 + 优秀/达标/预警/告警状态徽标 + 趋势
2. **阈值配置**: 多级阈值可视化编辑, 修改留痕
3. **告警事件流**: 级别/状态/恢复时间/数据异常标记
4. **通道管理**: 启停 + 测试发送
5. **通知中心**: 已有站内信角标复用; 预警类通知可点击跳转预警详情

---

## 10. 与现有模块联动

| 联动 | 说明 |
|---|---|
| 影响事件(Incident) | 高严重度告警(告警级)可一键/自动生成影响事件, 今日看板横幅已支持 |
| 管理措施(Measure) | 告警可转"管理措施"或"待办", 形成"发现→措施→回看"闭环 |
| 数据同步(sync) | 预警依赖数据同步; 同步失败本身也是监测指标(sync_health) |
| 工作台 | 待处理告警进入工作台待办, 管理端可见 |

---

## 11. 实施里程碑

| 阶段 | 内容 | 交付物 |
|---|---|---|
| M1 指标与阈值基础 | metric_registry + metric_thresholds 表; 指标求值器(复用 analytics 函数); 首批 3~5 个指标注册(含退款挽回率) | 指标列表 API + 当前值/级别 |
| M2 预警引擎 | 扩展 scan_alerts 为注册表驱动; 三级阈值比较; 当日幂等 + 恢复自愈; 数据健康检查; 定时扫描接入 | 告警事件 + 恢复通知 |
| M3 站内通知 | 告警生成/恢复 → notify() 站内信; 前端 Alerts.vue 升级(水位视图+阈值配置+事件流) | 站内信闭环 |
| M4 推送通道 | notification_channels 表 + 通道抽象 + 企业微信/钉钉/飞书群机器人 + 邮件; 测试发送; 失败降级 | 多通道推送 |
| M5 扩展 | 指标清单扩充; 连续升级/聚合; 与 Incident/Measure 联动 | 运营指标全覆盖 |

> M1~M3 是"最小闭环"(用户先只做站内信, 推送通道架构先抽象、后续接入)。

---

## 12. 风险与权衡

| 风险 | 应对 |
|---|---|
| 指标口径与看板不一致 | 指标注册表强制复用看板计算函数, 不另写 |
| 假告警(数据未同步) | 数据健康检查前置, 数据异常与业务告警分离 |
| 告警风暴/打扰 | 当日幂等 + 静默窗口 + 恢复自愈 + (后期)聚合 |
| 硬编码规则迁移成本 | 现有 4 类规则映射到注册表指标, 阈值表迁移, 保留 condition 字段兼容 |
| 阈值配置失误 | 阈值修改留痕(valid_from/valid_to + updated_by), 可回看可回滚 |
| 推送通道失败 | 发送失败降级站内信, 投递记录可查 |
| 性能 | 指标求值缓存(复用 Redis), 日终批量计算 + 快照表 |

---

## 待评审问题

1. 首批指标清单(§4.2)是否合理? 是否有遗漏的高优先级指标?
2. ~~三级阈值是否够用~~ → **已确认改为四级(优秀/达标/预警/告警)**; 是否再引入"红线"级(合规/安全等硬性底线)待定, 运营指标暂不引入
3. 告警是否需要在运营看板/今日看板顶部展示(类似影响事件横幅)?
4. M1~M3 最小闭环是否按此顺序推进?
