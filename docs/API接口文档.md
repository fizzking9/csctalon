# API 接口文档

> 版本：v1.0 · 日期：2026-08-17
> Base URL：`http://<服务器IP>:8000/api/v1`
> 认证：JWT Bearer Token（除 `/auth/login` 外所有接口需 `Authorization: Bearer <token>`）

## 一、接口总览

| 模块 | 前缀 | 文件 | 主要功能 |
|---|---|---|---|
| 认证 | `/auth` | auth.py | 登录/登出/token刷新 |
| 用户 | `/users` | users.py | 用户CRUD/密码修改 |
| 人员 | `/persons` | persons.py | 人员管理/别名/分组/专项 |
| 行政分组 | `/groups` | groups.py | 分组CRUD/成员/组长 |
| 业务专项 | `/specialties` | specialties.py | 专项CRUD/分配/负责人 |
| 数据采集 | `/fetch` | fetch.py | BMS/SCRM同步/调度 |
| 数据上传 | `/uploads` | uploads.py | 文件上传/导入 |
| 数据浏览 | `/wide-tables` | wide_tables.py | 宽表查询 |
| 数据匹配 | `/match` | match.py | 跨表关联匹配 |
| 分析引擎 | `/analytics` | analytics.py | 运营看板/效率分析 |
| 看板 | `/dashboards` | dashboards.py | 决策看板/视图 |
| 分析视图 | `/analysis-views` | analysis_views.py | 自定义视图CRUD |
| 设备 | `/devices` | devices.py | 设备/套餐管理 |
| 充值 | `/recharges` | recharges.py | 充值记录 |
| 报告 | `/reports` | reports.py | 报告CRUD |
| 周报 | `/weekly-reports` | weekly_reports.py | 周报管理 |
| 报告模板 | `/report-templates` | report_templates.py | 模板管理 |
| 报告评论 | `/report-comments` | report_comments.py | 评论管理 |
| 策略 | `/strategy` | strategy.py | 策略发布/已读 |
| 任务 | `/tasks` | tasks.py | 任务管理/跟进 |
| 待办 | `/todos` | todos.py | 待办管理/跟进 |
| 措施 | `/measures` | measures.py | 改进措施 |
| 事件 | `/incidents` | incidents.py | 事件管理 |
| 制度 | `/policies` | policies.py | 制度管理 |
| 部门同步 | `/dept-info-syncs` | dept_info_syncs.py | 部门信息同步 |
| 数据源 | `/data-sources` | data_sources.py | 数据源管理 |
| SCRM员工 | `/scrm-staff` | scrm_staff.py | SCRM员工档案 |
| SCRM聊天 | `/scrm-chat-records` | scrm_chat.py | SCRM聊天记录 |
| 操作日志 | `/operation-logs` | operation_logs.py | 操作审计 |
| 系统 | `/system` | system.py | 系统配置 |

## 二、核心接口详情

### 2.1 认证 `/auth`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 登录（用户名+密码），返回 JWT |
| POST | `/auth/logout` | 登出 |
| GET | `/auth/me` | 获取当前用户信息 |
| POST | `/auth/refresh` | 刷新 Token |

### 2.2 人员 `/persons`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/persons` | 人员列表（支持部门/分组/专项筛选） |
| POST | `/persons` | 新增人员 |
| PUT | `/persons/{id}` | 更新人员 |
| DELETE | `/persons/{id}` | 删除人员 |
| GET | `/persons/tree` | 人员树（部门→分组→人员） |
| POST | `/persons/{id}/aliases` | 添加别名 |
| GET | `/persons/{id}/specialty-history` | 专项分配历史 |
| GET | `/persons/{id}/group-history` | 分组归属历史 |

### 2.3 行政分组 `/groups`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/groups` | 分组列表 |
| POST | `/groups` | 新增分组 |
| PUT | `/groups/{id}` | 更新分组 |
| DELETE | `/groups/{id}` | 删除分组 |
| GET | `/groups/{id}/members` | 分组成员列表 |
| POST | `/groups/{id}/members` | 添加成员 |
| PUT | `/groups/{id}/leader` | 设置组长 |
| GET | `/groups/{id}/leadership-history` | 组长变更历史 |

### 2.4 业务专项 `/specialties`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/specialties` | 专项列表 |
| POST | `/specialties` | 新增专项 |
| PUT | `/specialties/{id}` | 更新专项 |
| GET | `/specialties/{id}/assignments` | 专项分配人员 |
| POST | `/specialties/{id}/assign` | 分配人员到专项 |

### 2.5 数据采集 `/fetch`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/fetch/sources` | 数据源列表 |
| POST | `/fetch/bms/sync` | 提交 BMS 同步任务 |
| GET | `/fetch/bms/status` | BMS 同步状态 |
| GET | `/fetch/bms/logs` | BMS 同步日志 |
| POST | `/fetch/scrm/sync` | 提交 SCRM 同步任务 |
| GET | `/fetch/scrm/status` | SCRM 同步状态 |
| GET | `/fetch/scrm/logs` | SCRM 同步日志 |
| GET | `/fetch/schedule/status` | 调度器状态 |
| POST | `/fetch/schedule/start` | 启动调度器 |
| POST | `/fetch/schedule/stop` | 停止调度器 |
| GET | `/fetch/task/preflight` | 任务预检 |
| POST | `/fetch/task/submit` | 提交同步任务 |
| GET | `/fetch/task/status` | 任务执行状态 |

### 2.6 分析引擎 `/analytics`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/analytics/today` | 运营看板-今日概览 |
| GET | `/analytics/staff-efficiency` | 人均效率（支持 start/end 参数） |
| GET | `/analytics/refund-summary` | 退款摘要 |
| GET | `/analytics/complaint-pending` | 未闭环投诉列表 |
| GET | `/analytics/refund-recovery` | 退款挽回分析 |
| GET | `/analytics/merchant-refunds` | 商户号退款 |
| GET | `/analytics/dashboard/{code}` | 按代码获取看板数据 |

### 2.7 看板 `/dashboards`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/dashboards` | 看板列表 |
| POST | `/dashboards` | 新建看板 |
| PUT | `/dashboards/{id}` | 更新看板 |
| GET | `/dashboards/by-code/{code}` | 按代码获取看板 |
| POST | `/dashboards/{id}/render` | 渲染看板 |

### 2.8 分析视图 `/analysis-views`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/analysis-views` | 视图列表 |
| POST | `/analysis-views` | 新建视图 |
| PUT | `/analysis-views/{id}` | 更新视图 |
| DELETE | `/analysis-views/{id}` | 删除视图 |
| POST | `/analysis-views/{id}/render` | 渲染视图 |
| GET | `/analysis-views/rules` | 分类规则列表 |

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
