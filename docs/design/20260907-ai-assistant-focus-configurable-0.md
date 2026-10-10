# AI 助手"关注点/分析视角"可配置化（Architect 增量设计草案）

**状态**：PENDING（待人工终审，非 LOCKED）
**日期**：2026-09-07
**关联架构**：`docs/arch/A-20260904-003-本地AI分析助手.md`（LOCKED v1.1；本期接口契约"一期自由问答、模板/口径存代码常量、二期可 DB 化"）
**性质**：A-20260904-003 明确规划的**二期增强**（架构 §七/终审记录 4："模板/口径卡存代码常量，二期可 DB 化"）——本文档把"二期 DB 化"收敛为最小可独立验收切片：**只 DB 化"关注点/分析视角"(Focus)，口径卡(Business Glossary)仍代码常量**。
**架构师阶段产出**：Architect 阶段草案（人工终审通过后升版 A-20260904-003 → v1.2 并打 arch tag）

---

## 〇、需求还原（用户 2026-09-07）

用户希望 AI 经营助手"可以配置相关的提示词做限定"——例如：
- **对接口返回数据指标的理解**（这部分=口径卡，已存在）
- **关注点**（"退款应关注涉诉率、投诉应关注积压、接待应关注专项"等运营关注点）
- **分析视角/输出风格**（结论怎么组织、先看什么后看什么）

用户理解："**提示词 + 数据 一起给大模型 → 输出结论**"，若提示词可修订扩充，AI 助手使用效率更高。该理解与现架构三层 Prompt 完全一致。

**范围决策（用户已拍板）**：
- 本期只配 **"关注点/分析视角"（Focus）**，可追加/修订/排序/启用；
- **口径卡（Business Glossary）与 System 铁律保持代码常量**——口径是客观业务事实，改错风险高，不进本期配置；
- 实现方式：**DB 表 + 管理 UI**（管理员可改，改完即时生效）。

---

## 一、设计

### 1.1 概念界定（避免与既有混淆）

| 层 | 内容 | 来源 | 本期是否配置化 |
|---|---|---|---|
| **System 铁律** | 防幻觉/防越权 5 条 | `ai_feed._SYSTEM_PROMPT` 代码常量 | ❌ 保持（放开=可被改成"编造数据"，高危） |
| **口径卡 Business Glossary** | 指标怎么算（客观事实） | `ai_glossary.GLOSSARY/ROUTE` 代码常量 | ❌ 保持（口径=业务事实，改错误导模型；一期已 git 版本化） |
| **关注点/分析视角 Focus**（本期新增） | 运营希望 AI 重点看什么、怎么表述（主观偏好） | **DB 表 `ai_focus_configs`** | ✅ 本期 DB 化 |

**为什么只配 Focus**：关注点是"运营偏好"，天然多变、因人/因阶段而异，最值得运营自助调整且改错不伤数据正确性（最多是分析侧重不同）。口径/铁律是"正确性底线"，保持代码受控。

### 1.2 Focus 注入位置（Prompt 结构）

现三层 Prompt（`ai_feed.build_messages`）在 `[user]` 的 `business_ctx`（口径卡 + 窗口）与 `data_ctx`（feed JSON）之间**插入一段 Focus 指导**：

```
[system]   System 铁律(不变)
[user]
  【业务口径(必须遵守)】        ← 口径卡(不变)
  【数据窗口说明】              ← 窗口(不变)
  【本期关注点 / 分析视角(请结合你的经营分析职责参考)】  ← 新增: DB Focus
  【给定数据(JSON, 金额单位=元)】← feed(不变)
  请回答以下问题：{question}
```

- Focus 为**建议性指导**，措辞避免与"铁律必须遵守"冲突；在 user 段末尾或 System 段声明"关注点是运营侧重参考，仍须以数据为准、不得编造"。
- 全部启用、按 sort_order 拼接；可按 `scope_key`（如 refund/complaint/reception/global）路由，未命中带 global 兜底。

### 1.3 数据模型（新表 `ai_focus_configs`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint PK | IDMixin |
| `code` | String(32) | 唯一标识(如 focus-refund-litigation) |
| `scope_key` | String(16) | 适用范围: `global`(兜底全带)/`refund`/`complaint`/`reception`；空=global |
| `title` | String(64) | 标题(如"退款关注点") |
| `content` | Text | 关注点正文(喂给模型的指导文本) |
| `sort_order` | Integer | 排序(小在前) |
| `enabled` | Boolean | 默认 true；false=停用不注入 |
| `created_by`/`updated_by` | String(64) | 审计 |
| `created_at`/`updated_at` | DateTime(tz) | TimestampMixin |

**seed**：首次启动/迁移灌入一组默认 Focus（对应 `QUICK_QUESTIONS` 与运营关注点），保证空库也有合理默认；管理界面可改可增可停。

> 不套 SCD（Focus 是偏好不是事实，直接覆盖即可，审计靠 operation_logs 的 POST/PUT 记录）。对齐 SyncSourceConfig/ReportTemplate 骨架（IDMixin+TimestampMixin+comment）。

### 1.4 读取端（`ai_feed.build_messages` 改造）

- 新增 `_load_focus(db) -> str`：查 `ai_focus_configs WHERE enabled` 按 `sort_order`，按 `scope_key` 路由（问句关键词命中 refund/complaint/reception → 带对应 scope + global；未命中只带 global）。
- 带**进程内缓存**（TTL 短，如 60s，仿 cache 习惯）避免每次问句都查库；管理端保存时失效该缓存。
- DB 空 → 不注入 Focus（退化=现状，不报错）。

### 1.5 管理端

- **路由**：`/api/v1/ai/focus-configs`（内部 `AdminUser`）CRUD（列表/增/改/停用/排序）。
- **前端**：新增 `web/src/views/system/AiFocusConfigs.vue`（仿 ExternalAccess/ClassificationRules 弹窗编辑），路由 `system/ai-focus-configs`（admin），菜单"系统管理 / AI 助手提示词"。
- **权限**：`AdminUser`（改提示词=高风险，仅 admin）；读取走内部 feed（`ManagerUser` 会话自动带）。

### 1.6 配置（config.py）

- 不加 env（DB 已是配置源）；可选加 `AI_FOCUS_ENABLED: bool = True` 一键关（应急回退代码默认）。本期建议不加，DB 空即退化为现状已足够安全。

---

## 二、涉及文件

| 文件 | 改动 |
|---|---|
| `backend/app/models/ai_focus_config.py` | 新 ORM（新表 `ai_focus_configs`） |
| `backend/alembic/versions/xxxx_add_ai_focus_configs.py` | migration（down_revision=`c0a1b2c3d4e7`）+ seed |
| `backend/app/schemas/ai_focus.py` | CRUD Pydantic schema |
| `backend/app/api/v1/ai.py` | 增 `/focus-configs` 管理路由(AdminUser) |
| `backend/app/services/ai_feed.py` | `build_messages` 注入 Focus（DB + 短缓存） |
| `backend/app/services/ai_focus_service.py` | 读取/路由/缓存管理 |
| `web/src/api/aiFocus.js` | 前端 API |
| `web/src/views/system/AiFocusConfigs.vue` | 管理页(列表/弹窗编辑) |
| `web/src/router/index.js` + `MainLayout.vue` | 路由+菜单(系统管理 / AI 助手提示词) |
| `backend/tests/test_ai_focus.py` | 读取路由/注入/CRUD/权限 |
| 文档 | 外部规范无涉；AI 架构文档标注 + 本草案 |

---

## 三、风险清单

1. **指令注入/提示词污染**：管理端可写任意文本，若含"忽略上面规则/编造数据"会削弱铁律。缓解：仅 `AdminUser` 可改；Focus 是 user 段建议性文本且 System 铁律仍强约束；可在后端过滤危险指令词（如"忽略系统/忽略铁律"）或提示"Focus 仅作参考，数据为准"。
2. **Focus 与口径卡冲突**：运营可能写与口径矛盾的话。缓解：口径卡仍在 system/business 段更前位置，措辞强调"口径以【业务口径】为准"。
3. **缓存失效**：管理端改后需清进程缓存。缓解：保存接口显式失效 key；缓存 TTL 短。
4. **契约漂移**：`/ai/analyze` 请求/响应不变；新增管理路由不影响会话链路。Focus 注入只影响生成文本质量，不进 feed schema。
5. **误改导致输出质量下降**：有 operation_logs 审计 + enabled 开关可回退；保留默认 seed 便于还原。

---

## 四、测试要求

- **读取路由**：问句含"退款"→ 注入 refund+global Focus；含"投诉"→ complaint+global；未命中 → global；DB 空 → 不注入。
- **注入位置**：Focus 文本出现在 user content，位于业务口径与数据之间。
- **CRUD/权限**：未登录 401；非 Admin 403；增改删排序生效；停用后不注入。
- **缓存**：改后新问句立即用新内容（显式失效生效）。
- **兼容**：无任何 Focus 配置时 build_messages 输出与改造前一致（回归）。

---

## 五、分期与依赖

- 本单（Focus DB 化）**不依赖** ①a/外部 brief v1.2；可与 v1.2 分开验收。
- seed 默认 Focus 建议与既有 `QUICK_QUESTIONS` 语义对应，避免空库时行为跳变。

---

*（本草案 PENDING，待人工终审后：升版 A-20260904-003 → v1.2（或独立编号）→ 打 arch tag → 交 Developer 实现。）*
