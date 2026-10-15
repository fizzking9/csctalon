# #327 MCP 只读数据出口 — 实现方案

## 背景与目标

#319 P3 最后一步：对外（第三方/AI Agent）提供受控只读数据出口，替代各处临时拼接的不可审计接口。

**已确认边界**：SSE 传输 + 首批 2 个工具（device_behavior / recharge_traffic）+ 会话级只读兜底。排班考勤延后。

## 技术选型

- **不引新依赖**（无 mcp/fastmcp）：用 FastAPI 原生 `StreamingResponse` 实现 SSE 端点，自实现 JSON-RPC 2.0 消息循环（MCP over SSE 协议）。
- **认证**：复用 `external_service.authenticate` + `check_rate_limit` + `touch_last_used`。
- **只读兜底**：每个工具调用前 `SET TRANSACTION READ ONLY`（PG 会话级硬约束），连接池归还时自动 reset。
- **审计**：复用 `S.record_external_access`，每次 MCP 工具调用写 `system_events`。

## 文件变更

### 新增

1. **`backend/app/mcp/__init__.py`** — MCP 协议层（空，标记包）
2. **`backend/app/mcp/server.py`** — MCP SSE Server 核心
   - `McpSseServer` 类：管理 SSE 连接、JSON-RPC 消息路由、工具分发
   - `handle_message()`：JSON-RPC 2.0 入口（initialize / tools/list / tools/call）
   - `tool_registry`：工具注册表（名称 → handler → schema → scope）
   - `set_readonly(db)`：会话级只读兜底
3. **`backend/app/mcp/tools.py`** — 首批 2 个工具实现
   - `device_behavior(sn)` → `ADS.supply_device_behavior()`
   - `recharge_traffic(sn, start, end)` → `ADS.supply_recharge()`
   - 每个工具声明：name / description / inputSchema / scope / handler
4. **`backend/app/api/v1/mcp.py`** — FastAPI 路由
   - `GET /mcp/sse` — SSE 端点（客户端连接，返回 `text/event-stream`）
   - `POST /mcp/messages` — 客户端→服务器 JSON-RPC 消息（MCP over SSE 标准）
   - 认证：从 query param `?token=` 或 `Authorization: Bearer` 取 token（SSE 连接无法带 Header，兼容两种）
   - 限流/审计/scope 校验复用 `_auth` 模式

### 修改

5. **`backend/app/api/v1/router.py`** — `include_router(mcp_router, prefix="/mcp")`
6. **`backend/app/services/external_service.py`** — `SCOPES` 新增 `"mcp"`（MCP 专用 scope，与既有 device/recharge 区分，便于独立授权）

### 测试

7. **`backend/tests/test_mcp_server.py`** — 单元测试
   - MCP initialize 握手
   - tools/list 返回白名单
   - tools/call 无 token → 401
   - tools/call 无效 scope → 403
   - tools/call device_behavior 正常返回
   - tools/call recharge_traffic 正常返回
   - 只读兜底验证（尝试写操作 → 报错）

## 关键设计决策

| 决策 | 理由 |
|---|---|
| 不引 mcp/fastmcp 依赖 | 项目零 MCP 先例，最小实现降低风险；后续若需完整 MCP 生态可再引入 |
| 自实现 JSON-RPC 2.0 | MCP over SSE 协议简单（initialize/list/call 三类消息），200 行内可覆盖 |
| scope 新增 `"mcp"` | 与既有 device/recharge 区分，允许管理员单独授权 MCP 通道（可只给 MCP 不给 REST） |
| 只读兜底用 PG 会话级 | `SET TRANSACTION READ ONLY` 是数据库层硬约束，比应用层判断更可靠；连接池归还自动 reset 无副作用 |
| SSE 端点支持 query token | SSE 连接（EventSource）无法自定义 Header，兼容 `?token=` 与 `Authorization: Bearer` 两种 |

## 安全约束

- 所有工具调用前强制 `SET TRANSACTION READ ONLY`
- 工具白名单硬编码在 `tools.py`，新增工具需显式注册（代码评审把关）
- 审计日志记录 tool_name + 参数摘要 + 调用方 token prefix
- 限流复用 Redis 固定窗口（每 token 独立计数）

## 验证方案

1. **单元测试**：pytest 覆盖握手/鉴权/白名单/只读/正常调用
2. **本地冒烟**：启动 uvicorn，用 `curl -N` 连 SSE 端点，验证 initialize → tools/list → tools/call 全流程
3. **生产冒烟**：发布后用一个测试 token 调用 device_behavior，确认返回结构与 REST 端点一致
4. **回归**：pytest 全量 0 失败 + ruff 通过 + C2 门禁 0 违规

## 验收对应

- [x] MCP 工具清单与白名单登记文档（`tools.py` 内注册表即文档，头部注释说明）
- [x] external_token 鉴权 + 只读兜底 + 审计日志落地
- [x] 端到端：外部客户端经 MCP 查询 device_behavior + recharge_traffic 成功
- [x] pytest 全量 0 失败 + ruff 通过 + 标准发布
