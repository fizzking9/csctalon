# API 接口文档

> 版本：v1.1 · 日期：2026-08-26
> Base URL：`http://<服务器IP>:8000/api/v1`
> 认证：JWT Bearer Token（除 `/auth/login`、`/auth/login-json`、`/wechat/auth/code-login` 外所有接口需 `Authorization: Bearer <token>`）
> 说明：本版以代码为准重写，补齐缺失模块，修正已失效路径（如 `/auth/refresh`、`/analytics/today`、`/fetch/bms/*`、`/dashboards/{id}/render` 等）。

## 一、接口总览

| 模块 | 前缀 | 文件 | 主要功能 |
|---|---|---|---|
| 认证 | `/auth` | auth.py | 登录（表单/JSON）/登出/当前用户 |
| 用户 | `/users` | users.py | 用户CRUD/密码修改 |
| 人员 | `/persons` | persons.py | 人员管理/别名归并/批量导入 |
| 人员任职 | `/persons` | staff_assignments.py | 任职历史/HR快照导入/导出 |
| 行政分组 | `/groups` | groups.py | 分组列表/详情/轮岗 |
| 业务专项 | `/specialties` | specialties.py | 专项CRUD/分配/轮岗/归档 |
| 数据采集 | `/fetch` | fetch.py | BMS/SCRM同步/调度/验证码登录 |
| 数据上传 | `/uploads` | uploads.py | 文件上传/解析/导入/预览diff |
| 数据浏览 | `/wide-tables` | wide_tables.py | 宽表查询（refund_wide） |
| 数据匹配 | `/match` | match.py | 跨表关联匹配 |
| 采集日志 | `/collection/logs` | collection_logs.py | 采集日志/数据类型 |
| 数据源 | `/data-sources` | data_sources.py | 数据源管理/记录浏览 |
| 同步源分组 | `/source-groups` | source_groups.py | 同步源分组/测试登录 |
| 投诉同步 | `/sync/complaints` | sync_complaints.py | 投诉数据推送/采集模式 |
| 手动退款同步 | `/sync/manual-refunds` | sync_manual_refunds.py | 手动退款推送/配置 |
| 共享文档同步 | `/sync/docs` | doc_sync.py | 腾讯文档授权/同步 |
| 分析引擎 | `/analytics` | analytics.py | 运营/现金流/退款/接待/今日看板（已废弃，见分析视图） |
| 看板 | `/dashboards` | dashboards.py | 看板CRUD/执行 |
| 分析视图 | `/analysis-views` | analysis_views.py | 自定义视图CRUD/执行 |
| 分类规则 | `/classification-rules` | analysis_views.py | 分类规则CRUD |
| 设备 | `/devices` | devices.py | 设备/套餐/标签管理 |
| 充值流量 | `/recharges` | recharges.py | 设备套餐/流量充值查询 |
| 报告 | `/reports` | reports.py | 报告CRUD/审计备注/附件 |
| 周报 | `/weekly-reports` | weekly_reports.py | 周报管理/生成/绑定 |
| 报告模板 | `/report-templates` | report_templates.py | 模板管理/实例化 |
| 报告评论 | `/report-comments` | report_comments.py | 评论管理 |
| 策略 | `/strategy` | strategy.py | 策略发布/版本/已读 |
| 任务 | `/tasks` | tasks.py | 专项任务/设备批次/跟进 |
| 待办 | `/todos` | todos.py | 待办管理/跟进 |
| 统一待办 | `/agenda` | agenda.py | 待办中心聚合 |
| 措施 | `/measures` | measures.py | 改进措施/到期复核 |
| 事件 | `/incidents` | incidents.py | 异常事件登记/跟进/转换 |
| 制度 | `/policies` | policies.py | 制度管理/审批/废止 |
| 部门同步 | `/dept-info-syncs` | dept_info_syncs.py | 部门信息同步/分类/回复 |
| 投诉分析 | `/complaints` | complaints.py | 投诉分析/审计/待闭环 |
| 排班 | `/schedules` | schedules.py | 排班/班次/考勤 |
| 发票 | `/invoices` | invoices.py | 发票登记/报销/图片识别 |
| 知识库 | `/kb-articles` | kb_articles.py | 知识库/话术库 |
| 预警 | `/alerts` | alerts.py | 预警规则/扫描/投递 |
| 指标 | `/metrics` | metrics.py | 指标注册/阈值/快照 |
| 通知 | `/notifications` | notifications.py | 站内通知/已读 |
| 通知通道 | `/channels` | channels.py | 通道配置/测试 |
| 数据质量 | `/data-quality` | data_quality.py | 数据质量规则/扫描/确认 |
| 跨部门协作 | `/upstream-tickets` | upstream_tickets.py | 上游工单/统计 |
| SCRM员工 | `/scrm-staff` | scrm_staff.py | SCRM员工档案 |
| SCRM聊天 | `/scrm-chat-records` | scrm_chat.py | SCRM聊天记录（预留） |
| 操作日志 | `/operation-logs` | operation_logs.py | 操作审计 |
| 系统 | `/system` | system.py | 系统配置/同步源 |
| 事务通知 | `/system-events` | system_events.py | 系统事件/已读 |
| 缓存管理 | `/system/cache` | cache_admin.py | 缓存信息/重建 |
| 微信 | `/wechat` | wechat.py | 扫码注册/登录/审批 |
| 小程序 | `/miniapp` | miniapp.py | 精简看板/采集触发 |

## 二、核心接口详情

### 2.1 认证 `/auth`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 登录（表单：username+password），返回 JWT |
| POST | `/auth/login-json` | JSON 方式登录（前端用） |
| POST | `/auth/logout` | 登出（吊销当前用户所有 token，token_version+1） |
| GET | `/auth/me` | 获取当前用户信息 |

> **说明**：无 `/auth/refresh` 刷新端点（服务端用 JWT 吊销版本号 `token_version` 管理会话，无 refresh token 机制）。

### 2.2 人员 `/persons`

> 本前缀由 `persons.py`（人员/别名）与 `staff_assignments.py`（任职/导入）两个模块共享。

**人员/别名：**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/persons` | 人员列表（支持部门/分组/专项/关键字筛选） |
| GET | `/persons/{person_id}` | 人员详情 |
| PUT | `/persons/{person_id}` | 更新人员 |
| DELETE | `/persons/{person_id}` | 停用人员（is_active=false，非物理删除） |
| GET | `/persons/{person_id}/timeline` | 人员审计时序流 |
| GET | `/persons/suggestions` | 别名归并建议 |
| GET | `/persons/unmerged-aliases` | 待归并别名列表 |
| POST | `/persons/merge` | 新建人员+归并别名 |
| POST | `/persons/{person_id}/merge` | 归并别名到该人员 |
| POST | `/persons/{person_id}/split` | 拆出别名 |
| POST | `/persons/refresh-aliases` | 刷新别名索引 |
| POST | `/persons/rebuild-aliases` | 重建别名索引 |
| POST | `/persons/batch-merge` | 批量归并 |
| POST | `/persons/batch-create-by-normalized` | 按归一化名称批量创建 |
| POST | `/persons/batch-import-by-prefix` | 按前缀批量导入 |
| POST | `/persons/batch-update` | 批量更新 |

**任职/导入（staff_assignments.py）：**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/persons/{person_id}/assignments` | 任职历史 |
| POST | `/persons/{person_id}/assignments` | 新增任职 |
| PUT | `/persons/{person_id}/assignments/{assignment_id}` | 更新任职 |
| DELETE | `/persons/{person_id}/assignments/{assignment_id}` | 删除任职 |
| GET | `/persons/{person_id}/assignment-at` | 任职点查询 |
| POST | `/persons/import-snapshot/preview` | HR 快照导入预览 |
| POST | `/persons/import-snapshot` | HR 快照导入 |
| GET | `/persons/export` | HR 主表导出 |

### 2.3 行政分组 `/groups`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/groups` | 分组列表 |
| GET | `/groups/{group_id}` | 分组详情（内嵌 members + leadership_history） |
| POST | `/groups/{group_id}/transfer-leadership` | 组长/副主管轮岗（Query：person_id/role/effective_from/change_reason） |

> **说明**：分组的新增/编辑/删除/成员管理未在 API 层暴露，由 `dept_info_syncs` 部门同步流程或初始化脚本维护。

### 2.4 业务专项 `/specialties`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/specialties` | 专项列表 |
| POST | `/specialties` | 新增专项 |
| GET | `/specialties/{specialty_id}` | 专项详情（内嵌 assigned_persons + leadership_history） |
| POST | `/specialties/{specialty_id}/assign` | 分配人员到专项 |
| POST | `/specialties/{specialty_id}/transfer-lead` | 负责人轮岗 |
| POST | `/specialties/{specialty_id}/archive` | 归档专项 |

> **说明**：专项无 PUT 更新接口，更新操作需通过分配/轮岗/归档完成。

### 2.5 数据采集 `/fetch`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/fetch/data-sources` | 可同步的数据源列表 |
| POST | `/fetch/sync-all` | 一键同步 BMS 数据（兼容旧版 `/bms/sync`） |
| GET | `/fetch/task/preflight` | 同步前预检：增量时间/冷却/忙闲 |
| POST | `/fetch/task/submit` | [新版]异步提交同步任务 |
| GET | `/fetch/task/status` | [新版]查询同步任务进度 |
| GET | `/fetch/sync-logs` | 同步日志 |
| GET | `/fetch/schedule/status` | 定时同步状态 |
| POST | `/fetch/schedule/start` | 启动定时同步 |
| POST | `/fetch/schedule/stop` | 停止定时同步 |
| GET | `/fetch/scrm/status` | SCRM 4 个数据源最近同步状态 |
| POST | `/fetch/scrm/sync` | SCRM 手动提交同步任务 |
| GET | `/fetch/scrm/logs` | SCRM 同步日志 |
| GET | `/fetch/test` | 测试浏览器可用性 |
| GET | `/fetch/captcha-login/check` | 检查验证码登录态文件 |
| POST | `/fetch/captcha-login/start` | 启动浏览器并截图验证码 |
| POST | `/fetch/captcha-login/submit` | 提交验证码登录，保存 storage_state |
| POST | `/fetch/captcha-login/logout` | 清除验证码登录态 |

> **说明**：旧文档 `/fetch/bms/sync`→`/fetch/sync-all`、`/fetch/bms/logs`→`/fetch/sync-logs`、`/fetch/sources`→`/fetch/data-sources`、`/fetch/bms/status` 已拆分到 `/task/status` + `/schedule/status` + `/scrm/status`。

### 2.6 分析引擎 `/analytics`

> **已废弃**：本模块 48 个硬编码端点为旧前端兼容保留，新方案使用 `/analysis-views/execute` + 预设视图。以下列出仍在使用的核心端点。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/analytics/today/kpi` | 今日看板-KPI 汇总卡片（6 项+昨日环比） |
| GET | `/analytics/today/hourly-trend` | 今日看板-时间节奏（hour/day/week/month 粒度） |
| GET | `/analytics/today/reception` | 今日看板-接待分项 |
| GET | `/analytics/today/refund` | 今日看板-退款分项（原因 Top10+方式分布） |
| GET | `/analytics/today/queue` | 今日看板-处理队列 |
| GET | `/analytics/today/staff` | 今日看板-人均效率表（替代旧 `/staff-efficiency`） |
| GET | `/analytics/today/refund-recovery` | 今日看板-退款挽回专项分析 |
| GET | `/analytics/summary` | 首页汇总卡片 |
| GET | `/analytics/operation/trend` | 退款趋势（按日） |
| GET | `/analytics/operation/status` | 退费状态分布 |
| GET | `/analytics/operation/reason` | 退费原因 TOP |
| GET | `/analytics/operation/method` | 退款方式占比 |
| GET | `/analytics/cash/trend` | 真实现金流出趋势 |
| GET | `/analytics/cash/breakdown` | 真实现金流构成（直接退款+用户提现） |
| GET | `/analytics/cash/wallet-vs-withdraw` | 退回钱包 vs 实际提现 |
| GET | `/analytics/cash/merchant` | 商户号对账（替代旧 `/merchant-refunds`） |
| GET | `/analytics/cash/wallet-balance` | 钱包余额分布 |
| GET | `/analytics/category/service-item` | 按服务项分类 |
| GET | `/analytics/category/merchant` | 按商户分类 |
| GET | `/analytics/category/package` | 按套餐分类 |
| GET | `/analytics/operator/ticket` | 人员工单处理统计 |
| GET | `/analytics/operator/audit` | 人员审核统计 |
| GET | `/analytics/operator/refund` | 人员退费操作统计 |
| GET | `/analytics/reception/overview` | 接待看板-总体接待量（按周） |
| GET | `/analytics/reception/category-summary` | 接待看板-分项汇总 |
| GET | `/analytics/reception/per-capita` | 接待看板-人均工作 |
| GET | `/analytics/reception/group-detail` | 接待看板-各组明细 |
| GET | `/analytics/refund-withdrawal/*` | 提现全链路看板（8 个端点） |
| GET | `/analytics/refund/*` | 退款看板（人员/设备/趋势/对比等 12 个端点） |
| GET | `/analytics/manual-refund/quality` | 手动退款数据质量审核列表 |
| POST | `/analytics/manual-refund/quality/{rid}/confirm` | 人工确认通过 |
| POST | `/analytics/manual-refund/quality/{rid}/fix` | 人工修正字段 |
| GET | `/analytics/refund/export` | 退款明细导出 Excel（四路经营） |

> **说明**：旧文档 `/analytics/today`→拆分为 `/today/kpi` 等 7 个子端点；`/analytics/refund-recovery`→`/today/refund-recovery`；`/analytics/refund-summary`、`/analytics/complaint-pending`、`/analytics/dashboard/{code}` 已不存在（未闭环投诉迁移至 `/complaints`，按 code 取看板迁移至 `/dashboards`）。

### 2.7 看板 `/dashboards`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/dashboards` | 看板列表 |
| POST | `/dashboards` | 新建看板 |
| GET | `/dashboards/{dashboard_id}` | 看板详情 |
| PUT | `/dashboards/{dashboard_id}` | 更新看板 |
| DELETE | `/dashboards/{dashboard_id}` | 删除看板 |
| POST | `/dashboards/{dashboard_id}/execute` | 批量执行（一次取全部视图结果，替代 `/render`） |

> **说明**：旧文档 `/dashboards/by-code/{code}` 无此路由；按 code 获取看板的能力当前无独立 API 入口（可在看板列表筛选）。

### 2.8 分析视图 `/analysis-views`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/analysis-views` | 视图列表 |
| POST | `/analysis-views` | 新建视图 |
| GET | `/analysis-views/{view_id}` | 视图详情 |
| PUT | `/analysis-views/{view_id}` | 更新视图 |
| DELETE | `/analysis-views/{view_id}` | 删除视图 |
| POST | `/analysis-views/execute` | 执行视图（body 传 `view_id` 或 `inline_config`，替代 `/render`） |

**分类规则（独立前缀 `/classification-rules`）：**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/classification-rules` | 分类规则列表 |
| POST | `/classification-rules` | 新建规则 |
| PUT | `/classification-rules/{rule_id}` | 更新规则 |
| DELETE | `/classification-rules/{rule_id}` | 删除规则 |
| POST | `/classification-rules/preview` | 规则预览 |

> **说明**：旧文档 `/analysis-views/rules` 已迁移至独立前缀 `/classification-rules`。

### 2.9 微信 `/wechat`

扫码注册与小程序登录（`code-login` 无需 JWT，其余审批接口需管理员权限）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/wechat/auth/code-login` | 微信 code 登录/自动创建待审批申请（返回 `status`：`approved`/`pending`；approved 时返回 JWT） |
| POST | `/wechat/auth/register` | 提交注册资料（pending 等待审批） |
| GET | `/wechat/registrations/pending` | 管理员：待审批注册列表（`?status=` 可筛选） |
| POST | `/wechat/registrations/{reg_id}/approve` | 管理员：审批通过（创建 User + 绑定 WechatAccount）或驳回（approved=false） |
| POST | `/wechat/registrations/{reg_id}/cancel` | 管理员：驳回注册（与 approve 分离） |

- `code-login` 流程：`wx.login()` 取 code → 后端 `code2session` 换 openid/unionid：
  - 已绑定 `WechatAccount` → 直接返回 JWT；
  - 存在 pending 申请 → 返回 `registration_id` 引导等待；
  - 首次扫码 → 创建 pending 申请并返回 `registration_id`。
- 本地开发未配置 `WECHAT_APPID/WECHAT_SECRET` 时，`code` 作为 `mock-` 前缀 openid 使用。
- `desired_role` 仅限 `agent`/`specialist`（防提权），审批时由管理员指定角色。

### 2.10 小程序 `/miniapp`

小程序精简看板与数据采集触发（需 JWT，前端经 `code-login` 获取）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/miniapp/summary` | 精简看板摘要（今日 KPI + 退款挽回专项，复用 `today:*` 缓存），任意登录用户 |
| GET | `/miniapp/sync/status` | 查询当前采集任务进度，仅管理员 |
| POST | `/miniapp/sync/trigger` | 超级管理员启动数据采集（BMS+SCRM 并发，`time_range`：`incremental`/`today`），仅管理员 |

## 三、通用约定

### 3.1 分页

```
GET /xxx?page=1&page_size=20

响应:
{
  "items": [...],
  "total": 100,
  "page": 1,
  "page_size": 20
}
```

### 3.2 时间参数

- 单日：`?date=2026-08-17`
- 范围：`?start=2026-08-10&end=2026-08-17`
- 时间格式：ISO 8601（`YYYY-MM-DD` 或 `YYYY-MM-DDTHH:MM:SS`）

### 3.3 错误响应

```json
{
  "detail": "错误描述",
  "code": "ERROR_CODE"
}
```

常见状态码：200 成功 / 201 创建 / 400 参数错误 / 401 未认证 / 403 无权限 / 404 不存在 / 500 服务器错误
