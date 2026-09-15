# AI 助手数据底座重构：双层结构 + 日颗粒度 + DB 化 Prompt Seed

## Context（为什么改）

工单 #98 性能优化已上线（参数调优 + 段选取 + 规则层），但用户实测"昨日整体经营分析"报告暴露**数据源层 + Prompt 配置化**问题：

1. **专项分布脏数据**：报告显示"内部投诉/外部投诉 暂无（lead 为空）0 会话/0 设备"，根因是 [external.py:624-709](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py#L624) `_reception_specialty` 只过滤 `is_active=True`，但 specialties 表里这两条历史专项（已合并到"投诉专项"）的 `is_active` 仍为 True。
2. **数据不同源**：`ai_feed._build_feed` ([ai_feed.py:46-88](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py#L46)) 走 `external._build_brief`，自拼 `_reception_specialty`/`_complaint_enhanced`，与今日值班看板前端用的 `A.today_kpi`/`A.today_reception_breakdown`/`A.today_staff_efficiency` 不同源 → "看板一个数、AI 报告另一个数"。
3. **缓存 TTL bug**：[external.py:419](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py#L419) `cached_call(..., 60, ...)` 固定 60s，历史日 brief 也 60s 过期重算。
4. **AI 主观推断**："处理量 238 > 登记量 190 → 在消化存量"是无证据归因。现有 `focus-complaint` seed 里甚至写了"处理滞后于登记说明积压恶化"——这条规则本身就是错的，需要 DB 层修正。
5. **硬编码**：`_BUCKET` 映射 (external.py:633-640) 把专项名硬编码到 today_staff_efficiency 字段名；`LEGACY_COMPLAINT_SPECIALTIES` 常量靠名字硬编码过滤。
6. **业务判定基准缺失**：AI 只列数字不下好坏判断；缺工作时间说明（08:00~23:59，非工作时间 AI 协助）；这些本应是运营可改的 prompt seed，但当前写死在代码里就改不了。

用户确认方案：① 日报双层结构（顶层=今日看板原始数据，底层=AI 增量分析）；② 周/月/区间按日颗粒度（汇总+日走势）；③ 历史日懒加载快照（首次访问重算+24h 缓存，后续命中）；④ **业务判定基准/工作时间/防主观推断铁律/AI 增量分析段全部 DB 化（运营可改可排序可启停），不写死代码**；⑤ **数据层去硬编码**（specialty is_active 治理 + efficiency_field 字段）；⑥ 一次性完整改造 P0+P1。

**分层原则**（对齐既有设计文档 `docs/design/20260907-ai-assistant-focus-configurable-0.md`）：
- **代码层（不可改）**：`_SYSTEM_PROMPT` 防幻觉底线 + `ai_glossary.GLOSSARY` 技术口径定义（三源分类/四路/登记处理口径/时区/金额单位/专项 vs 行政组长）
- **DB 层（可改）**：`ai_focus_configs` 表的 Focus seed（运营关注点/业务判定/工作时间/防主观推断指导/AI 增量分析段）

## 关键文件

- [backend/app/services/ai_feed.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py) — `_build_feed` 改造 + `_select_feed_sections` 重写 + `_SYSTEM_PROMPT` 兜底铁律 + 周/月 `_range_feed` 新增
- [backend/app/services/ai_glossary.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_glossary.py) — 不改动（已是技术口径代码常量，按设计分层保留）
- [backend/app/api/v1/external.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py) — `_reception_specialty` 移除 `_BUCKET` 硬编码改为读 `s.efficiency_field` + `_build_brief` TTL bug (line 419)
- [backend/app/services/cache_warmer.py](file:///Users/tanzk/dev/csctalon/backend/app/services/cache_warmer.py) — 新增 AI 路径 `today:staff:...:200:...` 预热条目
- [backend/app/models/specialty.py](file:///Users/tanzk/dev/csctalon/backend/app/models/specialty.py) — Specialty 模型加 `efficiency_field` 字段
- [backend/app/api/v1/ai.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/ai.py#L173) — `/ai/analyze` 入口（不改动，仅对齐参数）
- [backend/app/services/ai_focus_service.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_focus_service.py) — 不改动（现有 `_load_all`/`focus_for_question`/CRUD 已支持 DB 化 prompt seed 注入，新增 seed 自动生效）

复用既有函数：`A.today_kpi` (analytics.py:3480) / `A.today_reception_breakdown` (analytics.py:4305) / `A.today_refund_breakdown` (analytics.py:5457) / `A.today_staff_efficiency` (analytics.py:5825) / `complaints.complaint_summary` / `complaints.complaint_backlog` / `cache_ttl_for` (redis_client.py) / `focus_for_question` (ai_focus_service.py:83)

---

## A. 数据层去硬编码（P0 必做）

### A.1 DB 治理：specialty is_active 标记

通过 alembic migration 把 specialties 表"内部投诉"/"外部投诉"两条记录的 `is_active` 置 False（DB 治理），代码层 `_reception_specialty` 只过滤 `is_active=True` 即可，不再依赖 `LEGACY_COMPLAINT_SPECIALTIES` 常量名硬编码。

**保留 `LEGACY_COMPLAINT_SPECIALTIES` / `COMPLAINT_SPECIALTIES_ALL` 常量**（[analytics.py:1457-1461](file:///Users/tanzk/dev/csctalon/backend/app/services/analytics.py#L1457)）：用于 SCD 历史归因查询（analytics.py:3087 / 3110），识别"曾经属于投诉类"的专项（包括已合并的），这是合理的口径定义不是硬编码。

### A.2 specialties 表加 `efficiency_field` 字段

[external.py:633-640](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py#L633) `_BUCKET` dict 把专项名硬编码到 today_staff_efficiency 的 sp_*/pp_* 字段名。改为 DB 字段：

- Specialty 模型加 `efficiency_field: Mapped[str | None]` (String(64), nullable, comment="对应 today_staff_efficiency 的字段名, 格式 'sp_xxx,pp_xxx'")
- Alembic migration 给 6 个正式专项填值：
  - 咨询接待 → `sp_zixun,pp_zixun`
  - 套餐专项 → `sp_taocan,pp_taocan`
  - 退款挽回 → `sp_recovery,pp_recovery`
  - 退款处理 → `sp_processing,pp_processing`
  - 投诉专项 → `sp_internal,pp_internal`（投诉类合并到此桶）
  - 内部投诉/外部投诉 → NULL（is_active=False 不参与）
- `_reception_specialty` 改为从 `s.efficiency_field` 读字段名，移除 `_BUCKET` dict
- 未配置 `efficiency_field` 的专项兜底跳过 sessions/devices 统计（不再硬兜底到"咨询接待"桶，避免错误归并）

### A.3 修复 `_reception_specialty` 兜底逻辑

原 [external.py:645-646](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py#L645) 注释里说"临时专项兜底到咨询接待桶"——这是错误归并。改为：`efficiency_field` 为空的专项（如临时专项、未配置字段）sessions/devices 显示 0 并在 `_spec` 注释说明"未配置 efficiency_field, 不计入会话/设备统计"，不再硬兜底。

---

## B. 数据源切换（P0 必做：日报双层结构）

### B.1 `ai_feed._build_feed` 改造

废弃走 `_build_brief` 路径，直接调今日看板服务函数，与前端今日看板页逐字段对齐。

```python
# ai_feed.py 新 _build_feed(db, period, date, start, end) -> dict
win = _window(period, date, start=start, end=end)  # 复用 external._window
s, e = win["start"], win["end"]
today_data = {
    "period": win["period"], "date": win.get("date"),
    "date_start": win["date_start"], "date_end": win["date_end"],
    "generated_at": _now_cn().isoformat(),
    "kpi":       cached_call(f"today:kpi:None:{s}:{e}", cache_ttl_for(s,e,60), A.today_kpi, db, None, s, e),
    "reception": cached_call(f"today:reception:None:{s}:{e}", cache_ttl_for(s,e,60), A.today_reception_breakdown, db, None, s, e),
    "refund":    cached_call(f"today:refund:None:{s}:{e}", cache_ttl_for(s,e,60), A.today_refund_breakdown, db, None, s, e),
    "complaint": _complaint_block(db, s, e),  # 调 complaints.summary + backlog 路由本体
    "staff":     _compact_specialty_staff(db, s, e),  # today_staff_efficiency + _compact_specialty 压缩
    "_spec":     _spec_brief(),
}
```

关键点：
- **同源同缓存**：复用今日看板路由层 key（`today:kpi:None:{s}:{e}` 等），与前端共享 Redis，"与看板完全一致"。
- **TTL 自动感知**：`cache_ttl_for(s, e, 60)` 已实现历史日 24h、今天 60s。
- **`_complaint_block`**：调 `complaints.complaint_summary` + `complaints.complaint_backlog`（已 60s 缓存），与今日看板投诉接口同源。
- **`_compact_specialty_staff`**：调 `A.today_staff_efficiency(topn=200, aggregate_by="person")`（前端默认 topn=30，AI 用 200 拿更多人），再用现有 `_compact_specialty` 压缩 by_specialty 段（去逐人名单）。
- **`today_queue` 不进 daily feed**：实时队列历史日为空，AI 不需要；在 `_spec.today_queue` 注释"实时队列, 历史日为空, AI 应跳过"。

### B.2 顶层 `today_data` 结构示意

```json
{
  "period": "daily", "date": "2026-09-14",
  "date_start": "2026-09-14T00:00:00+08:00", "date_end": "2026-09-14T23:59:59+08:00",
  "generated_at": "2026-09-15T10:00:00+08:00",
  "kpi": {
    "reception": {"value": 5850, "yoy_value": 6368, "yoy_pct": -8.1, "device_count": 2913, "breakdown": {...}, "ai_takeover": {...}},
    "refund_count": {"value": 256, "yoy_value": 230, "yoy_pct": 11.3},
    "refund_amount": {"value": 34016.20, "yoy_value": 27372.76, "yoy_pct": 24.3},
    "actual_cashflow": {"value": ..., "yoy_value": ...},
    "cash_routes_today": {"route_a": {...}, "route_b": {...}, "route_c": {...}, "route_d": {...}, "operational": ..., "diff": ...},
    "complaint": {"value": 190, "yoy_value": ...},
    "staff_on_duty": {"value": 8, "yoy_value": 9},
    "ticket_done": {"value": 200, "real_done_today": 50, "real_done_yoy": 45},
    "metric_levels": {"refund_amount": {"level": "warning", "target": ..., "reason": ...}}
  },
  "reception": {"by_specialty": [{"specialty": "投诉专项", "lead": "洪佳", "lead_role": "专项负责人", "member_count": 5, "sessions": 9, "devices": 5}]},
  "refund": {"total_amount": {...}, "routes": {"route_a": {...}, ...}, "a_route": {"litigation_ratio": {...}, "litigation_amount": ...}},
  "complaint": {"total": {"registered": 190, "handled": 238, "backlog": 77}, "by_source": [{...}, {...}, {...}]},
  "staff": {"by_specialty": [...]},
  "_spec": {"money_unit": "元", "complaint": "registered=窗口内新登记...", "today_queue": "历史日为空"}
}
```

### B.3 `_select_feed_sections` 重写

新顶层结构变为 `kpi`/`reception`/`refund`/`complaint`/`staff`。`kpi` 内已含所有指标汇总+环比，单独切 `kpi` 就够答大多数问句。

新映射：
- `scope="reception"` → 保留 `kpi`（只含 reception 相关子字段）+ `reception`
- `scope="refund"` → `kpi`（refund_count/refund_amount/cash_routes_today/actual_cashflow）+ `refund`
- `scope="complaint"` → `kpi`（complaint）+ `complaint`
- `scope=None` → 全量

实现：`_select_feed_sections` 内对 `kpi` 做子字段过滤（按 scope 选 key），其余顶层段按 scope 直接切片。`_CONSTANT_FEED_KEYS` 扩展加入 `kpi`（裁剪后）。

### B.4 `_build_brief` 不删

[external.py:312-325](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py#L312) `external_brief` 路由对外开放（鉴权+scope 校验+access log），依赖 `_build_brief`。**保留 `_build_brief` 但做最小修复**：修 TTL bug (line 419)；`_reception_specialty` 的修复已在 A 节完成（移除 `_BUCKET` + DB is_active 治理自动生效）。`ai_feed` 不再调它即可，二者解耦。

---

## C. 历史日懒加载快照

### C.1 复用 `today:*` 命名空间

不新建 `ai:snapshot:{date}`。理由：
1. `today:*` 已被今日看板前端、`cache_warmer` 预热，历史日首次访问大概率已命中。
2. 设计目标"与看板完全一致"——共享 key 是强约束，隔离反而可能漂移。
3. `cache_ttl_for` 已实现历史日 24h、今天 60s 自动切换。

### C.2 流程

```
1. 用户问"昨天的情况" → ai.py /ai/analyze 解析出 date=昨天
2. _build_feed 调 cached_call("today:kpi:None:2026-09-14T00:00:00:2026-09-14T23:59:59", 24h, A.today_kpi, ...)
3. Redis 未命中 → A.today_kpi 重算（_resolve_windows 内部 date=历史日 → is_realtime=False, 整天窗口）
4. cached_call 写回 Redis TTL=24h
5. 后续访问直接命中缓存, 毫秒级返回
```

无需额外代码，复用 `cache_ttl_for` 即可。

### C.3 `today_queue` 等实时接口

历史日天然返回空（实时队列只看当前未结）。在 `_spec.today_queue` 注释明示"历史日为空，AI 应跳过该段不陈述"。`today_staff_efficiency` 历史日有值（按人聚合窗口内数据），正常用。

---

## D. 周/月/区间按日颗粒度

### D.1 双层结构

```python
range_data = {
    "period": "weekly|monthly|range",
    "date_start": win.start, "date_end": win.end,
    "summary": _range_summary(db, s, e),     # 窗口总量(复用 today_kpi 多日模式)
    "daily_trend": _daily_trend(db, s, e),    # 按日时序数组
    "_spec": _spec_brief(),
}
```

### D.2 汇总：复用 `today_kpi` 多日模式

`today_kpi(db, start=s, end=e)` 已支持多日窗口（[analytics.py:3491](file:///Users/tanzk/dev/csctalon/backend/app/services/analytics.py#L3491) 调 `_resolve_windows` 走 start+end 分支），返回的 `value` 即窗口总量、`yoy_value` 即上一等长周期总量。**直接复用，不重新做 sum 各日**（避免双计/口径漂移）。

```python
def _range_summary(db, s, e):
    return cached_call(f"today:kpi:None:{s}:{e}", cache_ttl_for(s,e,60), A.today_kpi, db, None, s, e)
```

### D.3 日走势数组

```python
def _daily_trend(db, s, e):
    days = _days_range(s, e)  # 北京时区日历日列表
    out = []
    for d in days:
        ds, de = f"{d}T00:00:00", f"{d}T23:59:59"
        k = cached_call(f"today:kpi:None:{ds}:{de}", cache_ttl_for(ds,de,60), A.today_kpi, db, None, ds, de)
        c = _complaint_block(db, ds, de)  # 复用 complaint 段
        out.append({
            "date": d,
            "reception": k["reception"]["value"],
            "devices": k["reception"]["device_count"],
            "ai_takeover_pct": k["reception"]["ai_takeover"]["session_pct"],
            "refund_count": k["refund_count"]["value"],
            "refund_amount": k["refund_amount"]["value"],
            "actual_cashflow": k["actual_cashflow"]["value"],
            "complaint_registered": c["total"]["registered"],
            "complaint_handled":   c["total"]["handled"],
            "complaint_backlog":    c["total"]["backlog"],
        })
    return out
```

JSON 样例：

```json
"daily_trend": [
  {"date":"2026-09-08","reception":1200,"devices":800,"ai_takeover_pct":15.0,"refund_count":40,"refund_amount":9500.00,"actual_cashflow":9000.00,"complaint_registered":12,"complaint_handled":15,"complaint_backlog":28},
  {"date":"2026-09-09","reception":1180,"devices":790,"ai_takeover_pct":16.2,"refund_count":38,"refund_amount":8800.00,"actual_cashflow":8600.00,"complaint_registered":10,"complaint_handled":14,"complaint_backlog":24}
]
```

### D.4 月报 30 天 token 控制

30 天 × 9 字段 ≈ 270 数字 + JSON 结构字符 ≈ 2-3k token，可控。**但前提是不带成员名单/专项明细**——日走势只放关键指标，明细走 `summary` 段（一份）。

降级策略（超过 `AI_ANALYZE_MAX_CHARS`）：
- 30 天 → 前 14 天逐日 + 后 16 天按周聚合（4 周），结构不变，date 标签换 `W37(09-08~09-14)`。
- 仍超 → 只保留 6 个核心指标（reception / refund_amount / complaint_registered / complaint_handled / complaint_backlog / ai_takeover_pct），砍掉 devices / refund_count / actual_cashflow。

### D.5 与 `today_kpi` weekly/monthly 关系

复用 `today_kpi` 自身的多日模式拿窗口总量（start+end 一把调）；日走势是额外的按日循环，不冲突。`today_kpi` 不需要改。

---

## E. Prompt 层 DB 化（核心改动：去硬编码 → ai_focus_configs seed）

### E.1 分层原则（对齐既有设计文档）

- **代码层（不可改）**：`_SYSTEM_PROMPT` 防幻觉底线 + `ai_glossary.GLOSSARY` 技术口径定义
- **DB 层（可改）**：`ai_focus_configs` 表的 Focus seed（运营可改可排序可启停）

### E.2 新增 4 条 ai_focus_configs seed（通过 alembic migration 插入）

**Seed 1: business-benchmark**（业务判定基准）
```python
{
    "code": "business-benchmark", "scope_key": "global", "sort_order": 2,
    "title": "业务判定基准",
    "content": (
        "业务判定基准(方向性判据, 用于判定'好/坏'与'风险/机会'): "
        "退款越少越好(金额/笔数); 均单价越低越好; 退款单数越少越好; "
        "接待量越多越好; 接待会话数越多越好; "
        "投诉越少越好; 投诉严重性 外部投诉 > 商户号投诉 > 内部投诉(即外部投诉 1 件 > 商户号 10 件); "
        "投诉处理越及时越好(handle - register 间隔越短越好); "
        "退款挽回率越高越好(挽回成功/分母); "
        "积压投诉越低越好(窗口内登记未闭环). "
        "陈述'环比涨跌'后必须按此基准标注'利好/利空', 不得只报数字不评判."
    ),
}
```

**Seed 2: work-time-ai-assist**（工作时间与 AI 协助）
```python
{
    "code": "work-time-ai-assist", "scope_key": "global", "sort_order": 3,
    "title": "工作时间与 AI 协助",
    "content": (
        "客服工作时间 08:00~23:59; 非工作时间(00:00~07:59)由 AI 协助处理. "
        "解读'接待量为 0 / 完工单为 0'时, 必须先核对窗口是否落在非工作时间, "
        "不得直接判定'无人值班'或'系统故障'. "
        "AI 接管率(ai_takeover)是可控指标前置: 接待量可升可控, AI 接管率上升=利好."
    ),
}
```

**Seed 3: inference-discipline**（防主观推断指导）
```python
{
    "code": "inference-discipline", "scope_key": "global", "sort_order": 4,
    "title": "防主观推断指导",
    "content": (
        "防主观推断指导: "
        "① 不同口径的数字(如 registered=窗口内新登记 vs handled=窗口内完成含历史单)禁止直接相减或比较大小; "
        "必须显式标注口径差异后再下结论. "
        "② 禁止做'处理量 > 登记量 → 消化存量'这类无证据因果推断; "
        "如需描述关系, 只能说'本期 handled 高于 registered, 差额源于窗口外登记、窗口内办结的历史单'等可验证陈述. "
        "③ 数据未提供的归因(如'退款上升因 XX 活动')禁止编造, 一律写'原因需结合运营侧确认'. "
        "④ yoy_value/yoy_pct 是上一等长周期汇总, 不是另一天的独立明细, 不得当独立日数据展开陈述."
    ),
}
```

**Seed 4: ai-incremental-analysis**（AI 增量分析任务段，开放题触发）
```python
{
    "code": "ai-incremental-analysis", "scope_key": "global", "sort_order": 5,
    "title": "AI 增量分析任务",
    "content": (
        "当用户问'分析/总览/怎么看/风险/异常'类开放题时(纯取数问句跳过本段), 增量分析任务: "
        "- 口径差异提醒: 当数据里出现 registered 与 handled 这类不同口径数字(见口径卡), 必须显式标注口径, 禁止直接相减或推断因果; "
        "- 环比异常预警: yoy_pct 超 ±30% 的指标, 必须点名并给出可能原因方向(数据/运营/外部, 不下结论); "
        "- 风险与机会: 按 business_benchmark 卡的方向判定(如'退款金额上升=风险'), 每类最多 1 条; "
        "- 下钻建议: 给出'看哪个看板卡片/下钻到哪一层'的可执行建议(不重复数据)."
    ),
}
```

### E.3 更新现有 seed（修正错误口径）

**修正 focus-complaint seed**（现有内容里有"处理滞后于登记说明积压恶化"是错误推断，且专项列表过时）：
- 原文："②登记(窗口新增)与处理(真实办结)要分开看, 处理滞后于登记说明积压恶化"
- 改为："②登记(窗口新增)与处理(真实办结)是不同口径, 禁止直接相减或推断因果; 处理>登记属正常(含历史单办结), 不必然意味'消化存量'"

**修正 focus-reception seed**（专项列表过时，内部/外部投诉已合并）：
- 原文："②六个专项(咨询/套餐/退款处理/退款挽回/内部投诉/外部投诉)的会话与设备规模"
- 改为："②五个正式专项(咨询接待/套餐专项/退款处理/退款挽回/投诉专项)的会话与设备规模, 历史专项(内部投诉/外部投诉)已合并到投诉专项"

### E.4 `_SYSTEM_PROMPT` 兜底铁律（不可改，防幻觉底线）

在 [ai_feed.py:23-35](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py#L23) `_SYSTEM_PROMPT` 末尾追加（一句话兜底，详细在 Focus seed）：

```
8. 防主观推断底线: 不同口径数字(如投诉 registered/handled)禁止直接相减或做因果推断; 禁止"处理量高→消化存量"这类无证据归因.
9. 业务判定方向: 退款↓利好 / 接待↑利好 / 投诉↓利好 / 挽回↑利好 / 积压↓利好; 比较后必须标注利好/利空.
10. 工作时间 08:00~23:59; 非工作时间由 AI 协助; 解读空值前先核对窗口时段.
```

### E.5 不改动 `ai_glossary.GLOSSARY`

现有 8 个 key（complaint_sources/complaint_reg_handled/refund_routes/refund_litigation/reception_metric/timezone/money_unit/lead_systems）属于技术口径定义，按设计分层保留代码常量。`glossary_for_question` 的"基础卡强制注入"逻辑也保留（路由逻辑不是 seed 内容）。

### E.6 现有 `focus_for_question` 自动加载（无需改服务层）

`ai_focus_service._load_all(db)` ([ai_focus_service.py:43-60](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_focus_service.py#L43)) 已实现 60s 进程缓存 + 按 scope_key 路由 + global 兜底全带。新 seed 写进 DB 后自动被加载，无需改服务层代码。运营通过 `/ai/focus-configs` REST 路由（[ai.py:306-329](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/ai.py#L306)）增删改启停。

---

## F. 前序修复（P0 必做）

### F.1 修 `today_kpi` 缓存 TTL bug

[external.py:419](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py#L419)：

```python
# 改前
kpi = cached_call(f"today:kpi:None:{start_s}:{end_s}", 60, A.today_kpi, db, None, start_s, end_s)
# 改后
kpi = cached_call(f"today:kpi:None:{start_s}:{end_s}", cache_ttl_for(start_s, end_s, 60), A.today_kpi, db, None, start_s, end_s)
```

第 424 行 `refund_decision_overview` 已正确用了 `cache_ttl_for`，对照即可。修后历史日 brief 不再 60s 过期重算。

### F.2 数据源切换是否自动解决 LEGACY 问题

部分解决：`ai_feed` 改走 `A.today_reception_breakdown` / `A.today_staff_efficiency`（这俩内部按 specialty_id 聚合，不依赖 LEGACY 常量），AI 报告里的专项分布自动干净。但 `external /brief` 仍依赖 `_reception_specialty`，必须靠 A 节的 DB 治理（is_active=False）+ efficiency_field 字段移除 `_BUCKET` 硬编码。

---

## G. 风险与回归

### G.1 `_build_brief` 依赖方

- `external_brief` 路由（external.py:312-325）：对外开放，鉴权+access log，**不能删**。
- 修复策略：保留 `_build_brief` 函数体，只修 TTL bug (F.1)；`_reception_specialty` 的修复靠 A 节 DB 治理自动生效；`ai_feed` 不再调它即可，二者解耦。
- 回归：`curl -H "Authorization: ..." /api/v1/external/brief?period=daily` 验证专项分布不再出"内部投诉/外部投诉"。

### G.2 LLM token 上限

- 单日 feed：`today_kpi` 全量约 1-2k 字符 + reception/refund/complaint/staff 各几百字符，压缩后约 4-5k 字符（< `AI_ANALYZE_MAX_CHARS=6000` 默认），可控。
- 月报 30 天日走势：约 2-3k token，加 summary 段共约 5-6k token，仍可控。
- 风险点：`today_staff_efficiency` 的 `data` 数组（200 人 × 多字段）会爆。**日走势里不带 staff.data**，只带 `summary.staff_on_duty`；staff 明细只在 daily 模式且 scope=reception 时带（且经 `_compact_specialty` 压缩）。

### G.3 缓存 key 冲突

`ai_feed` 改造后**共享**今日看板的 `today:*` key（设计目标"与看板完全一致"），不冲突。但要注意：
- `today:staff:None:{s}:{e}:200:0:person:None:None:None` 这个 key 与前端今日看板 staff 接口默认参数（topn=30）对齐不一致——AI 用 topn=200 会生成不同 key，**这是设计上的隔离**（AI 要更多人），需在 `cache_warmer.py` 加预热条目（否则 AI 路径冷加载）。
- 不需要在 `ai_feed` 里再做"_compact_specialty 自愈删坏缓存"逻辑（[ai_feed.py:62-82](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py#L62)）——`today_kpi` 直接调，无 `_build_brief` 的 routes 拼装环节，坏缓存场景消失。但保留自愈无害，可留作安全网。

### G.4 DB 治理的回滚风险

- 把"内部投诉"/"外部投诉"两条记录的 `is_active=False` 是**数据修复**，对应 alembic migration 的 downgrade 应该把 is_active 恢复为 True。
- specialty 表加 `efficiency_field` 字段是 nullable，未配置的专项不影响现有逻辑（_reception_specialty 兜底跳过 sessions/devices 统计）。
- 现有 `COMPLAINT_SPECIALTIES_ALL` 常量仍包含 LEGACY 名字，用于 SCD 历史归因查询——保持不变，不影响。

### G.5 Focus seed 注入顺序

新增 4 条 seed 的 sort_order：focus-global=1 / business-benchmark=2 / work-time-ai-assist=3 / inference-discipline=4 / ai-incremental-analysis=5。`focus_for_question` 按 sort_order 排序注入，确保基准/工作时间/防推断/增量段顺序合理。

---

## H. 实施步骤（一次性完整改造 P0+P1）

### 步骤 1: DB 治理 alembic migration（数据层去硬编码）

新建 alembic migration：
- upgrade：① specialties 表加 `efficiency_field` 字段（String(64), nullable）；② 给 6 个正式专项填 efficiency_field 值；③ 把"内部投诉"/"外部投诉"两条记录的 is_active 置 False；④ ai_focus_configs 表插入 4 条新 seed（business-benchmark / work-time-ai-assist / inference-discipline / ai-incremental-analysis）；⑤ UPDATE focus-complaint / focus-reception 两条现有 seed 修正口径。
- downgrade：反向操作。

验证：`docker exec csctalon-db psql -U postgres -d csctalon -c "SELECT name, is_active, efficiency_field FROM specialties ORDER BY display_order;"` — 应见 5 条 is_active=True + 2 条 is_active=False；`SELECT code, scope_key, sort_order, enabled FROM ai_focus_configs ORDER BY sort_order;` — 应见 8 条 seed。

### 步骤 2: Specialty 模型加 efficiency_field 字段

改 [backend/app/models/specialty.py](file:///Users/tanzk/dev/csctalon/backend/app/models/specialty.py) Specialty 模型，加 `efficiency_field: Mapped[str | None]` (String(64), nullable, comment="对应 today_staff_efficiency 的字段名, 格式 'sp_xxx,pp_xxx'")。

### 步骤 3: 修 `_reception_specialty` 移除 `_BUCKET` 硬编码

改 [external.py:624-709](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py#L624) `_reception_specialty`：
- 移除 `_BUCKET` dict (line 633-640)
- 改为从 `s.efficiency_field` 读字段名（如 `sp_zixun,pp_zixun` 分割得到 sp_field/pp_field）
- `efficiency_field` 为空的专项 sessions/devices 显示 0，不再硬兜底到"咨询接待"桶
- 由于 DB 已把"内部投诉"/"外部投诉" is_active=False，`select(Specialty).where(Specialty.is_active.is_(True))` 自动排除，不再需要 `~Specialty.name.in_(LEGACY_COMPLAINT_SPECIALTIES)` 过滤

### 步骤 4: 修 `today_kpi` 缓存 TTL bug

改 [external.py:419](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py#L419)：`60` → `cache_ttl_for(start_s, end_s, 60)`。

### 步骤 5: `ai_feed._build_feed` 改造为直接调今日看板服务

改 [ai_feed.py:46-88](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py#L46)：
- 替换 `_build_brief` 调用为直接调 `A.today_kpi` / `A.today_reception_breakdown` / `A.today_refund_breakdown` / `complaints` summary/backlog
- 新增 `_complaint_block(db, s, e)` helper（调 complaints 路由本体）
- 新增 `_compact_specialty_staff(db, s, e)` helper（调 today_staff_efficiency topn=200 + _compact_specialty）
- 保留 `_compact_specialty`（仍需压缩 staff by_specialty）
- 重写 `_select_feed_sections` 按 B.3 新映射

### 步骤 6: `_SYSTEM_PROMPT` 追加兜底铁律

改 [ai_feed.py:23-35](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py#L23) `_SYSTEM_PROMPT` 末尾追加铁律 8/9/10（E.4）。

### 步骤 7: 周/月/区间按日颗粒度

改 [ai_feed.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py) 新增：
- `_range_feed(db, period, start, end)` → 组装 summary + daily_trend + _spec
- `_daily_trend(db, s, e)` → 按日循环调 today_kpi + complaint_block
- `_days_range(s, e)` → 北京时区日历日列表 helper
- `build_messages` 按 period 分流：daily → `_build_feed`；weekly/monthly/range → `_range_feed`
- 月报 token 降级策略（D.4）

### 步骤 8: cache_warmer 预热 AI 路径的 staff topn=200 key

改 [cache_warmer.py](file:///Users/tanzk/dev/csctalon/backend/app/services/cache_warmer.py) `_warm_analytics` 加 `today:staff:None:{s}:{e}:200:0:person:None:None:None` 预热条目。

### 步骤 9: 部署 + 验证

```bash
# 部署到生产
ssh tanzk@192.168.10.21 "cd /opt/csctalon && sudo ./upgrade.sh -git"

# 验证 1: 专项分布修复
ssh tanzk@192.168.10.21 "sudo docker exec csctalon-backend python -c \"
from app.api.v1.external import _reception_specialty
from app.core.database import SessionLocal
db = SessionLocal()
r = _reception_specialty(db, '2026-09-14T00:00:00', '2026-09-14T23:59:59')
print([(s['specialty'], s['sessions']['value'], s['devices']['value']) for s in r])
\""
# 期望：5 条正式专项（咨询接待/套餐专项/退款处理/退款挽回/投诉专项），不再含'内部投诉'/'外部投诉'

# 验证 2: TTL 修复
ssh tanzk@192.168.10.21 "sudo docker exec csctalon-db redis-cli TTL 'today:kpi:None:2026-09-14T00:00:00:2026-09-14T23:59:59'"
# 期望：86400 附近

# 验证 3: Focus seed 注入
ssh tanzk@192.168.10.21 "sudo docker exec csctalon-db psql -U postgres -d csctalon -c \"SELECT code, scope_key, sort_order, enabled FROM ai_focus_configs ORDER BY sort_order;\""
# 期望：8 条 seed（4 条原有 + 4 条新增），全 enabled=true

# 验证 4: AI 报告不再有"消化存量"推断 + 带利好/利空标注
# 前端发"昨日整体经营分析"问句，检查 AI 输出：
# - 含"registered 与 handled 不同口径"显式说明
# - 含利好/利空标注（如"退款金额+24.3% 利空"）
# - 不再出现"处理量高→消化存量"推断
# - 含工作时间核对（如"08:00~23:59 工作时间"）

# 验证 5: AI 数据与今日看板一致
# 前端今日看板同窗口对比 AI 报告数字

# 验证 6: 周/月报按日颗粒度
# 前端发"本周接待量走势"，检查 AI 输出 7 天日走势 + 窗口总量
# 前端发"本月经营总览"，检查 AI 输出 30 天日走势或降级为前 14 天 + 后周聚合

# 验证 7: cache_warmer 预热 AI 路径
ssh tanzk@192.168.10.21 "sudo docker exec csctalon-db redis-cli KEYS 'today:staff:None:*:200:0:person:None:None:None'"
# 期望：返回 1 个 key（今日）+ 1 个 key（昨日，历史日 24h 缓存）
```

---

## 关键文件清单

### 必须修改
- /Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py — `_build_feed` 改造 + `_select_feed_sections` 重写 + `_SYSTEM_PROMPT` 兜底铁律 + 周/月 `_range_feed`/`_daily_trend`/`_days_range` 新增
- /Users/tanzk/dev/csctalon/backend/app/api/v1/external.py — `_reception_specialty` 移除 `_BUCKET` 改读 `s.efficiency_field` (line 624-709) + `_build_brief` TTL bug (line 419)
- /Users/tanzk/dev/csctalon/backend/app/models/specialty.py — Specialty 模型加 `efficiency_field` 字段
- /Users/tanzk/dev/csctalon/backend/app/services/cache_warmer.py — 新增 AI 路径 staff topn=200 预热条目

### 新建
- /Users/tanzk/dev/csctalon/backend/alembic/versions/xxx_ai_feed_refactor.py — DB 治理 migration（specialty 字段+数据修复 + ai_focus_configs 新 seed + 现有 seed 修正）

### 不修改（已对齐分层）
- /Users/tanzk/dev/csctalon/backend/app/services/ai_glossary.py — 技术口径代码常量（按设计分层保留）
- /Users/tanzk/dev/csctalon/backend/app/services/ai_focus_service.py — DB 化 Focus 服务（已支持，无需改）
- /Users/tanzk/dev/csctalon/backend/app/api/v1/ai.py — `/ai/analyze` 入口（参数已对齐）
