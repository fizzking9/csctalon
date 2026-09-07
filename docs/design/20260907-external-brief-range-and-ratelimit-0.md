# 外部只读平台 + AI 助手增强：日期范围回看 / 限频 0=不限 / AI 透传（Architect 增量设计草案）

**状态**：PENDING（待人工终审，非 LOCKED）
**日期**：2026-09-07
**关联架构**：`docs/arch/A-20260902-021-外部工具只读开放平台.md`（LOCKED v1.1）、`A-20260904-003-本地AI分析助手.md`（LOCKED v1.1，feed 复用同底层）
**性质**：既有 LOCKED 架构的**向后兼容增量**（不改动既有 period/date 语义、不改 scope、不新增表）
**架构师阶段产出**：Architect 阶段草案（人工终审通过后升版 A-20260902-021 → v1.2 并打 arch tag）

---

## 〇、需求还原

1. **brief 支持任意日期范围回看（含"标准周/自然周"）**：当前 `brief?period=weekly` 只能算"本周"（`_window` 以今天所在周为周一），`date` 参数在 weekly/monthly 分支被忽略，**无法回看历史某个 ISO 自然周**。用户希望日报查询（含 AI/外部简报）能拉取"某个历史标准周"乃至任意连续区间。
2. **限频支持"0 = 不限制"**：当前 `rate_limit_per_min` 语义 = `NULL→用全局默认(60)` / `≥1→个性化`，**无法表达"对该 token 完全不限频"**。schema 的 `ge=1` 也拒绝 0。用户希望：`0 = 不限`、`NULL = 全局默认`、`>0 = 个性化`。
3. **⚠️ 范围护栏（补充）**：开放 `start/end` 后需限制单次日期宽度——`end` 未来日期 → 400；跨度上限 → 400（默认 93 天 ≈1 季度，可配置）。防压库 / 防被当全量导出工具滥用。
4. **AI 助手同步（补充 ①a）**：AI 助手 feed 与 external brief **同一底层**（`_build_brief`），应同步支持任意区间/指定周问答（后端透传 start/end + weekly?date），前端本期不暴露手填日期（保持会话问答定位）。

**结论（用户已确认）**：四项可同批收口（A-20260902-021 v1.2 单一增量），改动均落在外部只读平台 + AI feed 复用层，风险面小、可一次验收。

---

## 一、改动 A：`brief` 暴露可选 `start`/`end`（任意区间 / 标准周回看）

### 1.1 设计要点

**不新增周接口、不扩大 period 语义**，而是给 `brief` 增加**可选 `start`/`end`**（YYYY-MM-DD，互斥于 `date`）：

```
GET /api/v1/external/brief?start=2026-08-03&end=2026-08-09   # 历史标准周(ISO 自然周, 周一~周日)
GET /api/v1/external/brief?start=2026-08-03&end=2026-08-03   # 指定整天(等价 period=daily&date=...)
GET /api/v1/external/brief?start=2026-08-01&end=2026-08-31   # 任意连续区间
GET /api/v1/external/brief?period=daily                      # 向后兼容: 原语义不变
```

**优先级（Architect 定，向后兼容铁律）**：
1. `start`/`end` 同时给出 → 走任意区间模式（`period` 仅作展示标签/忽略）
2. `start`/`end` 只给一个 → **400**（必须成对）
3. 否则 → 走既有 `period`(+`date`) 逻辑（完全不变）

### 1.2 窗口语义（复用 `_resolve_windows`，零新增窗口逻辑）

| 调用 | start/end 传给聚合 | 环比 | 实时性 |
|---|---|---|---|
| `start=S&end=E`，`E==今天` | `today_kpi(start=S, end=今天)` | 前移等长周期 | ✅ 实时截止 now（`_resolve_windows` 已处理） |
| `start=S&end=E`，`E` 为历史日 | 同上 | 前移等长周期 | 整天（00:00~23:59:59.999999） |
| `start=E&end=E` | `today_kpi(start=E,end=E)` | 昨天 | 历史整天 |

**参数精度与时间边界（2026-09-07 确认，用户拍板）**：
- `start`/`end` **只接受纯日期 `YYYY-MM-DD`**，不解析时分秒（传 `T`/空格带时间 → 400）。"当天精确到某时刻"这类诉求不在本接口范围（那是单日端点/内部看板的既有能力）。
- **跨天区间（span>1）**：时间边界默认整天——`start` 日 `00:00:00` 起、`end` 日 `23:59:59.999999` 止（北京时间）。用户确认"超过一天的日期范围默认整天更合理"。
- **`end==今天`（含单日=今天）**：特例实时截止 **now**（非 23:59:59），与 `period=daily` 当日口径一致；历史单日（`start=end` 且非今天）才是整天。

**关键收益**：`today_kpi / refund_decision_overview(db,start,end,grain) / _complaint_enhanced / _reception_specialty` **全部已支持任意 start/end**，`brief` 的接待专项/退款四路+涉诉/投诉三源增强对任意区间天然成立——**不需要为"周"写任何特殊聚合**。

**grain 映射**（决定 `refund_decision_overview` 聚合粒度）：
- 区间跨度 ≤ 1 天 → `grain="day"`
- 区间跨度 > 1 天 → `grain="week"`（与 monthly 现行为一致，涉诉率等按周聚合，与既有 weekly 行为对齐）

> ⚠️ 说明：`weekly`/`monthly` 现实现的 `grain="week"` 是既有行为，任意区间跨度>1天沿用 `week` 保持一致，避免改变既有消费方口径。

### 1.3 契约与 _spec

- 响应增加 `"range": {"start": "...", "end": "..."}` 仅当走 start/end 模式时返回；`period` 字段仍返回（取 `"range"` 标签），不破坏现有消费方。
- `_spec_brief().period` 文案补一句：`start/end 给定则优先(任意区间/标准周回看)`。

### 1.4 兼容与风险

1. **workbuddy 现有调用不受影响**（只用 `period=`）。
2. **跨 ISO 年周**：调用方需自算周一~周日；本项目"周"= ISO 自然周（`date_trunc('week')`/dayjs `isoWeek` 同语义，A-20260902-012）。历史周回看建议外部方传 `周一~周日`，服务端按 start/end 原样解释，**不做周号解析**（保持简单、口径清晰）。
3. 非法 `start/end`（非 YYYY-MM-DD、起>止）→ 400（与 `date` 非法 400 一致，避免静默换序掩盖调用方错误）。
4. Redis 缓存键已含 `{start}:{end}`，无需额外处理。
5. **⚠️ 范围护栏（2026-09-07 补充，Architect 定稿）**：
   - **未来日期拦截**：`end > 今天(北京)` → `400`（防时间穿越/未来空窗，不静默截断）。
   - **单次最大跨度**：`span_days > EXTERNAL_BRIEF_MAX_RANGE_DAYS` → `400`。取值 **93 天(≈1 季度, 用户拍板)**，可配置（`settings`），覆盖季度简报/回看，同时防"一次拉太宽压库/被当全量导出工具滥用"。
   - 与限频（控频次）**正交**：限频管"每分钟几次"，本护栏管"单次查多宽"，两层都要。
   - 对齐项目既有惯例：`analytics.py` 投诉待处理列表 `PENDING_LIMIT_DAYS=31`（超窗拒绝+提示）、ai-acc 采集 7 天分片。
   - 单日端点（reception/refund/complaint）仅收单日 `date`，不受此限制。

### 1.5 AI 助手同步（改动 C，①a，2026-09-07 用户确认）

**背景**：AI 助手 feed（`ai_feed._build_feed`）复用 `external._build_brief` 同底层。`brief` 支持 range/指定周后，AI 后端应同步透传，才能支持"历史某周/某区间经营如何"这类问答。

**改动（后端透传，前端本期不暴露手填日期）**：

| 文件 | 改动 |
|---|---|
| `backend/app/api/v1/ai.py` | `AiAnalyzeRequest` 增可选 `start/end`（纯日期，与 `_AiWindow` 校验成对）；`analyze` 透传给 `build_messages`/`submit` |
| `backend/app/services/ai_feed.py` | `build_messages`/`_build_feed` 增 `start/end` 透传；坏缓存自愈处 `_window` 同步传参；窗口说明标注 `period=range(start,end)` |
| `backend/app/services/ai_task_manager.py` | `AiTask`/`submit`/`to_public_dict` 记录 `start/end` |
| `backend/app/api/v1/external.py` | `_window` weekly 分支支持 `date` 指定 ISO 周（date 缺省=本周不变） |

**边界**：
- 前端抽屉（`AssistantDrawer.vue`）本期**不加** start/end 手填（保持会话问答定位）；后端能力为将来快捷周/UI 铺路。
- 护栏（93 天/未来日期/成对）由 `external._window` 统一执行，AI 复用同一校验，不重复实现。
- 兼容：`start/end` 缺省 → 完全走原 period/date 逻辑，AI 既有行为不变。

---

## 二、改动 B：限频 `0 = 不限制` 三分支

### 2.1 现状（已核实）

```python
# services/external_service.py
def _token_quota(row):
    if row.rate_limit_per_min and row.rate_limit_per_min > 0:
        return row.rate_limit_per_min
    return settings.EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN or 60   # ← NULL 也会被兜底成 60
# schema: rate_limit_per_min: int | None = Field(None, ge=1)   # ← 0 被拒
```

**问题**：`rate_limit_per_min = 0` 无法落库（schema 拒绝）；即便绕过，`_token_quota` 也会因 `>0` 判断失败掉回全局默认——**"0=不限"从未真正实现**。

### 2.2 设计（三值语义）

| `rate_limit_per_min` | 语义 | `check_rate_limit` 行为 |
|---|---|---|
| `0` | **不限制**（该 token 免除限频） | 直接 return（不做 INCR） |
| `>0` | 个性化限频 | 固定窗口计数 > 阈值 → 429 |
| `NULL` | 用全局默认 `EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN` | 同上（阈值=全局默认） |

### 2.3 改动点

1. **schema** `ExternalTokenCreate/Update.rate_limit_per_min`：`Field(None, ge=0)`（允许 0），description 注明 `0=不限制 / 空=全局默认 / >0=个性化`。
2. **service**：
   ```python
   def _is_unlimited(row) -> bool:
       return row.rate_limit_per_min == 0

   def _token_quota(row) -> int:
       if row.rate_limit_per_min is not None and row.rate_limit_per_min > 0:
           return row.rate_limit_per_min
       return settings.EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN or 60

   def check_rate_limit(row):
       if _is_unlimited(row):   # 0 = 不限, 跳过计数
           return
       ...原逻辑...
   ```
3. **model 注释**（`rate_limit_per_min`）：注明 `0=不限制 / NULL=全局默认 / >0=个性化`（ORM comment 更新，**无需 DDL migration**——comment 不参与 schema 校验；若要同步 DB comment 可另加 migration，本草案建议不改 DB comment，仅代码层语义，避免无谓 migration）。
4. **前端** `ExternalAccess.vue`：
   - 创建/编辑 `el-input-number` `:min="0"`，placeholder/提示改为 `0=不限制 / 留空=全局默认(60)`。
   - 提交判断 `if (form.rate_limit_per_min !== null && form.rate_limit_per_min !== undefined)`（现在 `if(...)` 会把 0 当假值丢掉）。
   - 列表展示 `0` 显示为 `不限制`、`NULL` 显示 `全局默认`。
   - 编辑回填：`row.rate_limit_per_min ?? null` 保持（0 是真值，能正确回填）。
5. **`/external/config`** 响应可加 `rate_limit_unlimited_value: 0` 便于前端文案（可选）。

### 2.4 风险

1. **放开 0=不限**可能被滥用 → 只对受信任调用方（workbuddy 等）配 0；管理界面有 `last_used_at` 可观测。属管理员授权行为，与 scope 授权同级。
2. `touch_last_used` 的 60s 写频与 0=不限无冲突（那是审计写频，不是限频）。
3. 既有 `rate_limit_per_min=5` 等 token 不受影响（>0 分支不变）；`NULL` 仍走全局默认。

---

## 三、涉及文件

| 文件 | 改动 |
|---|---|
| `backend/app/api/v1/external.py` | `external_brief` 增 start/end Query；`_window` 增 range 分支 + weekly 支持 date 指定周；`_spec_brief` 文案 |
| `backend/app/schemas/external_token.py` | `ge=0` + description |
| `backend/app/services/external_service.py` | `_token_quota`/`check_rate_limit` 支持 0=不限 |
| `backend/app/models/external_token.py` | `rate_limit_per_min` comment 更新（无 DDL） |
| `web/src/views/system/ExternalAccess.vue` | 表单 min=0 + 提示 + 提交/展示 0=不限制 |
| `web/src/api/externalAccess.js` | （无需改，字段名不变） |
| `backend/app/api/v1/ai.py` | `AiAnalyzeRequest` 增可选 start/end；`_AiWindow` 校验；analyze 透传 |
| `backend/app/services/ai_feed.py` | `build_messages`/`_build_feed` 透传 start/end；坏缓存自愈同步 |
| `backend/app/services/ai_task_manager.py` | `AiTask`/`submit`/`to_public_dict` 记录 start/end |
| `backend/app/core/config.py` | `EXTERNAL_BRIEF_MAX_RANGE_DAYS=93` 跨度上限配置 |
| `backend/tests/test_external_brief.py` | 补 start/end 范围回看 + weekly?date 指定周 + 护栏 |
| `backend/tests/test_external_access.py` | 补限频 0=不限 三分支（fake redis） |
| `backend/tests/test_ai_assistant.py` | 补 ai feed/build_messages 透传 start/end |
| `docs/standards/外部只读数据接入规范.md` | brief 增 start/end 示例；限频三值说明；时间边界 |
| `docs/API接口文档.md` | 同步 |
| 架构文档 `A-20260902-021` | 终审后升 v1.2（标修订 + 打 arch tag） |
| 架构文档 `A-20260904-003` | 终审后在 AI 契约标注 v1.1+（start/end 可选透传） |

---

## 四、测试要求

**改动 A（test_external_brief.py）**
- `_window`：start/end 历史周（周一~周日整天）、单日区间、跨 ISO 年周边界、`end==今天` 实时、起>止→400、只传一个→400
- HTTP：`brief?start=&end=` 200 且结构与 period 模式一致；与 `today_kpi(start,end)` 输出一致

**改动 B（test_external_access.py）**
- `_token_quota`：0→unlimited（check_rate_limit 直接 return）/ >0→个性化 / None→全局默认
- HTTP（fake redis）：`rate_limit_per_min=0` 反复调用不 429；`=1` 超限 429；None 用全局默认阈值

**改动 C / AI 同步（test_ai_assistant.py + test_external_brief.py）**
- `_build_feed`/`build_messages` 传 start/end → 走 range、窗口说明标注 `period=range(start,end)`
- `_window` weekly + date → 该 ISO 周周一~周日（历史周整天）
- 兼容：不传 start/end → 原 period/date 行为不变（既有 AI 用例回归）

---

## 五、风险清单

1. **brief start/end 误用**：起>止/单个参数 → 400 拦截（不静默换序）。
2. **限频 0=不限 滥用**：管理员授权行为；有 last_used_at 可观测；文档标注"仅限受信任方"。
3. **契约漂移**：period 模式行为完全不变；新增参数向后兼容；`_spec` 透出说明。
4. **无 DDL**：改动 B 不新增 migration（仅代码语义 + 可选 comment 同步），风险低。
5. **AI 契约扩展**：`analyze` 加可选 start/end 不破坏既有调用（缺省走原逻辑）；但属 LOCKED 契约扩展，需并入 v1.2 架构评审。前端本期不暴露，避免范围蔓延。

---

*（本草案 PENDING，待人工终审后：升版 A-20260902-021 → v1.2 → 打 arch tag → 交 Developer 实现。）*
