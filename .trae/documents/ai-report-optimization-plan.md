# AI 报告系统优化方案

## Context

当前 AI 报告系统已完成多 provider 框架(#127-#131)：122b 专供预生成、27b 供实时问答。但存在三个核心问题：
1. **对比类报告含当天不完整数据**——周报/月报用"本周一到此刻"做窗口，当天才过了几小时就拿来和昨天全天比，口径不对等
2. **命中=返回全文，未命中=实时跑27b**——用户只想看退款部分却拿到整份报告，或未命中走27b实时分析（质量不如122b预生成草稿）
3. **缺少统一 AI 管理入口**——模板、模型、报告分散在不同页面

用户确认分三批实施，本方案覆盖全部三批。

---

## 批次 A：对比报告截止前一天 + trigger_at 调半夜

### A1. 新增窗口计算模块

**新建** `backend/app/services/ai_report_window.py`：
- `compute_pregen_window(period, ref_date=None) -> dict`：纯计算，无 IO
  - weekly: 本周周一 ~ 昨天；若今天是周一→退化为上周一~上周日
  - monthly: 本月1日 ~ 昨天；若今天是1号→退化为上月1日~上月末
  - daily: 返回 None（日报保持现行 date=None 实时数据）
  - 返回 `{start, end, snapshot_date, label}`
- `compute_compare_window(start, end) -> tuple[str, str]`：等长前对比窗口（紧贴 start 之前取同长度天数）

### A2. 改 generate_for_template

**改** [ai_report_worker.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_report_worker.py) L196-211：

weekly/monthly 模板不再走 `build_messages(period=template.period, date=None)`，改为：
- 调 `compute_pregen_window(template.period)` 算出 start/end
- 调 `build_messages(period="range", date=None, start=start, end=end)`
  - `_window` 在 start/end 模式下 end < today → `date_end = f"{e}T23:59:59+08:00"` 整天，`is_realtime=False`（[external.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py) L360-366）
- 落库 `save_report(start=start, end=end, snapshot_date=今日)`，snapshot_date 保持今日让 matcher 命中
- daily 模板保持现行 `date=None`（随同步刷新，当天实时数据不变）

### A3. trigger_at 调半夜 + warm_idle 时段放宽

**改** [ai_report_worker.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_report_worker.py)：
- `warm_idle()` L438：移除 `_DAYTIME_HOURS` 限制（当前8-23才扫描，凌晨不触发 trigger_at）
- `warm_on_startup()` L405：保持白天限制（避免重启时大量并发）
- **Alembic 迁移**：`UPDATE ai_report_templates SET trigger_at='1-02:00' WHERE code IN ('weekly_compare_last','monthly_overview')`
- daily_general_morning 保持 `09:00`（refresh_on_sync 覆盖白天实时刷新）

### A4. feed 增对比段（可选，A 批先不接入）

`_build_feed` 已支持 `start/end` 透传（[ai_feed.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py) L92），`_enrich_with_daily_trend` 在 start/end 模式下自动走日走势路径（L124-130）。等长对比窗口在批次 B 随草稿结构化一起接入。

---

## 批次 B：草稿结构化 + 摘取层 + 三级语义命中

### B1. 草稿结构化标记

**段落锚点（全树统一）**：
- `## 接待分析` / `## 退款分析` / `## 投诉分析` / `## 趋势对比` / `## 结论建议`

**改** [ai_feed.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py) `_SYSTEM_PROMPT` L35-58 第3条：增加"周报/月报必须按 H2 锚点结构化输出"约束。

**Alembic 迁移**：
- `UPDATE weekly_compare_last SET max_tokens=4000`（当前2000不够完整草稿）
- `UPDATE monthly_overview SET max_tokens=5000`
- `UPDATE daily_general_morning SET max_tokens=2500`

### B2. 新增摘取服务

**新建** `backend/app/services/ai_extract_service.py`：
- `split_sections(answer_md) -> dict[str, str]`：按 `## ` H2 锚点切分，兜底按段落分
- `extract_section(db, question, report, template) -> dict`：
  1. split_sections 切分草稿
  2. 关键词路由：`keywords_to_scopes(question)` → scope → 段落（复用 [ai_feed.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py) L497 `_select_feed_sections` 同款逻辑）
  3. 调 27b（`provider_code=None` 走 default 链，`timeout_s=30`，`max_tokens=1200`）：从命中段落摘取+按问句排版
  4. 输出 JSON `{result_md, confidence, need_clarify, clarify_options}`

### B3. 新增语义匹配服务

**新建** `backend/app/services/ai_semantic_matcher.py`：
- `semantic_match(db, question, candidates) -> dict`：27b 判定问句与候选报告的语义相关性，输出 `{hit_report, confidence}`

### B4. 改命中链路

**改** [ai.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/ai.py) L272-299：

```
L1 关键词匹配(现行 match_template_report 不变)
  → 命中 → 走摘取层(不返回全文)
  → 未命中 ↓
L2 27b 语义匹配(semantic_match, confidence≥0.6 命中)
  → 命中 → 走摘取层
  → 未命中 ↓
L3 兜底: 返回 clarify_options(当日报告 H2 标题列表)
  或走原实时 LLM 路径(保留兼容)
```

### B5. 前端改造

**改** [AssistantDrawer.vue](file:///Users/tanzk/dev/csctalon/web/src/components/AssistantDrawer.vue)：
- 新增 `status === "clarify"` 状态：渲染 `clarify_options` 为可点按钮，点击后重新发 /ai/analyze

---

## 批次 C：月报逐天小时粒度 + AI 管理页面

### C1. feed 逐天小时粒度

**改** [ai_feed.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py) `_enrich_with_daily_trend`：
- 在 `span > 14`（月报路径）下，对 daily_trend 每条额外调 `cached_call(f"today:hourly:None:{d}:{d}", ..., A.today_hourly_trend, ...)` 取当日 hourly
- 新增 `_compact_daily_hourly_summary(day_hourly) -> dict`：每天只取工作时段合计 + 峰谷小时（~6标量/天 × 30天 = 180字段，~6KB）

### C2. AI 管理页面

**新建** `web/src/views/system/AiAdmin.vue`（5 Tab）：
- Tab 1 报告列表：复用 `/admin/ai/reports` API（[admin_ai.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/admin_ai.py) L160）
- Tab 2 模板管理：内嵌 [AiTemplates.vue](file:///Users/tanzk/dev/csctalon/web/src/views/system/AiTemplates.vue)
- Tab 3 模型/算力节点：内嵌 LlmProviders 管理组件（[llm_providers.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/llm_providers.py) API）
- Tab 4 提示词管理：内嵌 AiFocusConfigs + 模板 question_template 编辑
- **设计说明三层落地**（AGENTS.md 铁律 7）：代码注释 + 页面 ⓘ + review-checklist

**改** `web/src/router/index.js`：新增 `system/ai-admin` 路由

### C3. 维度扩充

先与用户确认扩充清单再实施（AGENTS.md：不确定停下来提问）。

---

## 工单规划

| 工单 | 批次 | 标题 | 关键文件 |
|---|---|---|---|
| #132 | A | 周报/月报预生成窗口截止前一天 | ai_report_worker.py, ai_report_window.py(新) |
| #133 | A | trigger_at 调半夜 + warm_idle 凌晨可触发 | ai_report_worker.py, 迁移 f8g9h0i1j2k3 |
| #134 | B | 草稿结构化标记 + 模板 max_tokens 放宽 | ai_feed.py(_SYSTEM_PROMPT), 迁移 g3h4i5j6k7l8 |
| #135 | B | 摘取层 + 三级语义命中链路 | ai_extract_service.py(新), ai_semantic_matcher.py(新), ai.py, AssistantDrawer.vue |
| #136 | C | 月报逐天小时粒度 | ai_feed.py(_compact_daily_hourly_summary) |
| #137 | C | AI 管理页面(5 Tab) | AiAdmin.vue(新), router/index.js |
| #138 | C | 数据维度扩充(先确认清单) | ai_feed.py |

**依赖**：#132→#133(批次A) | #134→#135(批次B) | #136/#137/#138 独立(批次C)

## 验证方式

每批完成后：
1. **ruff + pytest**：`ruff check backend/ && pytest -k ai`
2. **本地 docker cp 到 csctalon-backend 容器验证**
3. **部署生产**：`ssh tanzk@192.168.10.21 "sudo /opt/csctalon/current/deploy/upgrade.sh --git"`
4. **端到端实测**：
   - A：手动触发周报预生成，查 `ai_reports.start/end` 截止昨天、trigger_at=02:00 凌晨自动触发
   - B：用户提问"退款情况"→命中预生成→只返回退款段落（非全文）；提问模糊→返回 clarify 选项
   - C：月报 feed 含 `hourly_summary`；AI 管理页面 5 Tab 可用
5. **凭证评论关闭工单**（逐条可复现核验方式）
