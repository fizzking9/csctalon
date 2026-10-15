# MCP (Model Context Protocol) 只读数据出口 — 协议层
#
# 实现 MCP over SSE 协议: 客户端连 GET /mcp/sse 建立 SSE 通道,
# 经 POST /mcp/messages 发送 JSON-RPC 2.0 消息(initialize / tools/list / tools/call)。
#
# 本包不引第三方 MCP 依赖, 最小实现覆盖 initialize/list/call 三类消息。
