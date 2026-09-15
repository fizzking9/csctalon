# AI 分析助手性能优化方案（规范流程版）

## Context（为什么改）

生产环境（192.168.10.21，DeepSeek `deepseek-v4-flash`）AI 助手用户反馈"分析耗时较长"，但实际诉求是"按既定逻辑 + 系统数据做简单判断"。根因是**架构代差**：全量喂模型 + 让模型自由组织中文报告，简单问句也要走完整 LLM 链路。

### 生产实测（2026-09-12 ~ 09-15，16 条 `ai task done` 日志）

| 指标 | 实测范围 | 平均 | 占比 |
|---|---|---|---|
| `build_ms`（数据底座组装） | 360-3042ms | ~700ms（热）/ 2s（冷） | ~15% |
| `llm_ms`（模型生成） | 1479-6893ms | ~4000ms | **~75%** |
| `total_ms` | 2188-9935ms | ~5000ms | 100% |

**关键发现**（推翻前置假设）：
1. `LLM_THINKING` 未显式配置 → 走默认 `disabled`（**非思考模式误配**，先前担心的 low/high 不存在）
2. **`llm_ms` 是绝对大头**（75%），`build_ms` 是次要矛盾
3. `out_chars` 与 `llm_ms` 强正相关（2308 字符→6484ms；171 字符→1479ms）→ **输出长度直接决定耗时**
4. `AI_SYNC_WAIT_S=3.0` + `total_ms` 平均 5s → 几乎全走轮询 → 前端体感更长

**优化优先级**（数据驱动）：层 1 降参数 > 层 3 规则层秒回 > 层 2 精确取数。

---

## 规范流程（按 AGENTS.md 工单/界面/文档纪律）

| 环节 | 动作 | 凭证 |
|---|---|---|
| ① 工单 | 在 GitHub `tanzk/csctalon` 建 issue，标题"AI 助手性能优化：参数调优+规则层+精确取数"，正文含本方案摘要 + 验收标准 | issue 号 `#XX`（实施时填） |
| ② 方案审核 | 本计划经人工终审（用户确认）后开工 | 计划文件 + 用户确认 |
| ③ 实现 | 每层独立 commit，提交信息带 `#XX` | commit sha |
| ④ 测试 | pytest + npm test + 生产灰度对比 | 测试输出 |
| ⑤ 关闭凭证 | 工单关闭前写 `## 关闭凭证核验 (YYYY-MM-DD)` 评论，逐条对应验收标准 + commit sha + 实测命令 | 评论 |

**文档性质**：本方案属 **REQ**（有功能边界、验收标准、归属工单），非 META。
**界面纪律**：层 3 规则层不改前端 UI（返回结构兼容），不触发界面设计说明三层落地；若后续加"规则层秒回"角标则需补设计说明。

---

## 层 1：参数调优（立竿见影，优先级最高）

生产数据证明 `llm_ms` 是大头，降输入/输出 token + 提同步等待时间见效最快。

| 配置项 | 当前默认 | 改为 | 依据 |
|---|---|---|---|
| `AI_MAX_TOKENS` | 1500 | **800** | out_chars 实测多在 1200-2000，800 token 足够；省一半生成时间 |
| `AI_ANALYZE_MAX_CHARS` | 12000 | **6000** | 喂 4000 输入 token 降首 token 延迟 |
| `AI_SYNC_WAIT_S` | 3.0 | **8.0** | total_ms 平均 5s，提到 8s 让多数请求一次返回不走轮询 |
| `LLM_TIMEOUT_S` | 120.0 | 保持 | 云端生成长尾兜底 |

**改文件**：
- [backend/app/core/config.py](file:///Users/tanzk/dev/csctalon/backend/app/core/config.py#L132-L180)（3 个默认值）
- [deploy/env.production.example](file:///Users/tanzk/dev/csctalon/deploy/env.production.example#L117-L127)（补 3 个示例值，避免生产 .env 与代码默认脱节——本次生产就是 .env 没显式配走默认值）

**同步改生产**：SSH 192.168.10.21 `/opt/csctalon/.env` 追加 3 行 + `docker compose restart backend`。

**测试影响**：`test_ai_assistant.py` L77-L120 只断言结构/字段不断言长度，**不回归**。

**验收**：改后同问句 `total_ms` 应从 ~5s 降到 ~2.5s，多数请求走 `success` 不走 `running`。

---

## 层 2：问题路由 + 精确取数（次优先，build_ms 边际收益小但降输入 token）

**复用 [ai_focus_service._KEYWORD_TO_SCOPE](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_focus_service.py#L31-L36)**（scope 值 `refund/complaint/reception` 与 feed 顶层段名**逐字符对齐**，见 [external.py:436-438](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/external.py#L436-L438)），**不复用 `GLOSSARY_ROUTE`**（粒度错位）。

**改 [backend/app/services/ai_feed.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py)**：
1. 新增 `_keyword_to_scope(question) -> str | None`：复用 `_KEYWORD_TO_SCOPE` 映射表（导出复用，勿复制防漂移）
2. 新增 `_select_feed_sections(feed, scope) -> dict`：恒量段（period/date/date_start/date_end/generated_at/_spec/range）始终保留 + 命中 scope 对应业务段；scope=None 回退全量（兼容）
3. `build_messages` 内：`_build_feed` → `_select_feed_sections` → `_trim_feed`

**缓存策略**：brief 仍走 `_build_brief`（命中 `today:kpi`/`refund:do`/`complaint:*` 既有 key，**铁律 #11**），段选取只在内存 dict 切片零成本。**不**按段单独调聚合函数（today_kpi 一次出 reception+refund，分调反多一次 miss）。

**测试**：`test_build_messages_structure`（L78）走全量兜底不断言段，不回归；新增 `test_build_messages_scope_refund_only` 断言问"今日退款"→ feed 不含 complaint 顶层段。

**风险**：问"今日退款"保留整段 refund（含 `a_route.litigation_*`）不漏涉诉率。

---

## 层 3：简单判断走规则层（最贴合诉求，纯取数问句秒回）

**入口**：[ai.py ai_analyze](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/ai.py#L174-L270) 内 `_require_enabled` 之后、`ai_task_manager.submit` 之前**短路**。命中规则 → 直接返回 `R(data={status:success, result_md, **window_info})`，**不入队** task_manager（毫秒级）。

**新增 `backend/app/services/ai_rule_engine.py`**，导出：
```python
def try_rule_answer(db: Session, question: str, eff: dict) -> str | None
```
逻辑：关键词正则匹配 → 调 `ai_feed._build_feed`（命中缓存秒取，含坏缓存自愈）→ 取字段 → 模板拼 Markdown。未命中/取数为空 → 返回 None 回退 LLM 路径。

**模板放代码常量**（非 `ai_focus_configs`——那是 admin 可改的"主观偏好"，规则模板是"客观取数"，混放会让运维误改取数口径，违反 System 铁律精神 [ai_feed.py:22-34](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py#L22-L34)）。

**首批规则**（关键词正则 → 模板）：
- `今日|昨日|本周|本月.*(退款|提现).*(多少笔|笔数|情况|金额)` → `refund.total_count/total_amount/a_route.litigation_ratio`
- `...投诉.*(积压|登记|处理|多少)` → `complaint.{source}.registered/handled/backlog`
- `...接待.*(量|多少|情况|会话)` → `reception.total/by_specialty`

**前端兼容**：返回结构与现有 `success` 一致，[AssistantDrawer.vue:328](file:///Users/tanzk/dev/csctalon/web/src/components/AssistantDrawer.vue#L312-L345) `if (d.status === 'success')` 天然命中，**零改**。

**测试**：
- `test_rule_engine_refund_count`（问"今日退款多少笔"→ 命中、返回含 total_count）
- `test_rule_engine_miss_falls_back_to_llm`（问"分析下经营策略"→ 返回 None）
- `test_rule_engine_respects_window`（问"昨日投诉积压"→ _AiWindow 解析昨日窗口，规则层取昨日数据）

**风险**：模板数字必须来自 feed（禁编造）；取数 None 时模板写"暂无"不写 0；规则层在 `_build_feed` 坏缓存自愈（[ai_feed.py:70-81](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py#L70-L81)）之后取数（同函数天然安全）。

---

## 实施顺序

1. **建工单** `#XX`（实施时填实际 issue 号）
2. **层 1**（1 commit）：改 config.py + env.production.example + 同步生产 .env 重启 → 看日志 llm_ms 是否降
3. **层 2**（1 commit）：改 ai_feed.py + 新增测试 → pytest
4. **层 3**（1 commit）：新增 ai_rule_engine.py + 改 ai.py 短路 + 新增测试 → pytest
5. **前端测试**：`npm test -- AssistantDrawer.spec.js` 不回归
6. **生产灰度**：层 1 上线后对比同问句 total_ms；层 3 上线后"今日退款多少笔"应 <500ms
7. **关闭凭证核验**：工单评论逐条对应验收标准 + commit sha + 实测命令

---

## 验证方案

**后端**：
```bash
cd backend && pytest tests/test_ai_assistant.py -v
# 期望：现有用例全过 + 新增 3 个规则层/段选取用例过
```

**前端**：
```bash
cd web && npm test -- AssistantDrawer.spec.js
# 期望：L105-L166 轮询/失败重试用例不回归
```

**生产灰度**（层 1 后）：
```bash
ssh tanzk@192.168.10.21 'cd /opt/csctalon && tail -f logs/backend/app.log | grep "ai task .* done"'
# 期望：llm_ms 从 ~4000 降到 ~2000，total_ms 从 ~5000 降到 ~2500
```

**端到端**（层 3 后）：
```bash
curl -X POST http://192.168.10.21:8000/api/v1/ai/analyze \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"今日退款多少笔","period":"daily"}'
# 期望：<500ms 返回 success，不走 LLM
```

---

## 风险汇总

| 风险 | 缓解 |
|---|---|
| 规则层模板数字编造 | 模板取数必须来自 feed，None→"暂无" |
| 精确取数漏段 | refund 整段保留，`a_route.litigation_*` 天然在内 |
| 缓存 key 漂移 | 全部复用 `today:kpi`/`refund:do`/`complaint:*`，勿新建（铁律 #11） |
| 800 token 截尾 | 层 2 输入变小后输出同步收缩；复杂问句仍走 LLM |
| 生产 .env 改动风险 | 改前备份，改后 restart backend，失败可回滚 |

## Critical Files

- [backend/app/api/v1/ai.py](file:///Users/tanzk/dev/csctalon/backend/app/api/v1/ai.py) — 层 3 规则层短路入口
- [backend/app/services/ai_feed.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_feed.py) — 层 2 段选取
- [backend/app/services/ai_focus_service.py](file:///Users/tanzk/dev/csctalon/backend/app/services/ai_focus_service.py) — 复用 `_KEYWORD_TO_SCOPE`（L31）
- [backend/app/core/config.py](file:///Users/tanzk/dev/csctalon/backend/app/core/config.py) — 层 1 参数默认值（L132-L180）
- [deploy/env.production.example](file:///Users/tanzk/dev/csctalon/deploy/env.production.example) — 层 1 生产示例
- `backend/app/services/ai_rule_engine.py` — 层 3 新增
- [backend/tests/test_ai_assistant.py](file:///Users/tanzk/dev/csctalon/backend/tests/test_ai_assistant.py) — 测试基线 + 新增用例
- `/opt/csctalon/.env`（生产）— 层 1 同步
