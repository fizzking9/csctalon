"""MCP over SSE 路由 (#327)

端点:
  GET  /mcp/sse      — SSE 连接端点(客户端 EventSource 连接, 返回 text/event-stream)
  POST /mcp/messages — 客户端→服务器 JSON-RPC 2.0 消息(经 SSE 通道的 response 字段返回)

认证兼容两种方式(SSE 连接无法自定义 Header):
  1. Authorization: Bearer <token>  — 标准方式, 优先
  2. ?token=<token>                 — EventSource 兼容方式

只读兜底: 每个 tools/call 前 SET TRANSACTION READ ONLY(PG 会话级硬约束)。
审计: 每次 tools/call 写 system_events(category=external)。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import DBSession
from app.mcp.server import mcp_server
from app.mcp.tools import _tool_device_behavior, _tool_recharge_traffic  # noqa: F401  确保注册
from app.services import external_service as S

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _auth(
    db: Session,
    authorization: str | None,
    token_query: str | None,
) -> object:
    """MCP 统一认证: 优先 Header, 兼容 query param(SSE 无法带 Header)"""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif token_query:
        token = token_query.strip()

    if not token:
        raise HTTPException(401, "Missing Bearer token (Authorization header or ?token=)")

    row = S.authenticate(db, token)
    S.check_rate_limit(row)
    S.touch_last_used(db, row)
    return row


@router.get("/sse", summary="MCP SSE 连接端点")
async def mcp_sse(
    request: Request,
    db: DBSession,
    token: Annotated[str | None, Query(description="Bearer token(EventSource 兼容方式)")] = None,
    authorization: Annotated[str | None, Header()] = None,
):
    """MCP over SSE 连接端点。

    客户端经 EventSource 连接后, 服务器通过 SSE 推送 endpoint 事件(告知 POST /mcp/messages 地址)。
    后续客户端经 POST /mcp/messages 发送 JSON-RPC, 响应经 SSE 的 message 事件返回。
    """
    _auth(db, authorization, token)

    async def event_stream():
        # MCP over SSE 标准: 先发送 endpoint 事件, 告知客户端消息 POST 地址
        yield {
            "event": "endpoint",
            "data": json.dumps({"uri": f"/api/v1/mcp/messages?token={token or ''}"}),
        }
        # 保持连接(心跳 30s), 等待客户端断开
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(30)
            yield {"event": "ping", "data": "{}"}

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )


@router.post("/messages", summary="MCP JSON-RPC 消息端点")
async def mcp_messages(
    request: Request,
    db: DBSession,
    token: Annotated[str | None, Query(description="Bearer token(EventSource 兼容方式)")] = None,
    authorization: Annotated[str | None, Header()] = None,
):
    """MCP JSON-RPC 2.0 消息处理。

    客户端经 POST 发送 JSON-RPC 请求体, 响应经 HTTP 直接返回(非 SSE 推送)。
    标准 MCP over SSE 中 SSE 用于 server→client 通知, 请求-响应走 POST。
    """
    row = _auth(db, authorization, token)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    # 支持单条或批量消息
    if isinstance(body, list):
        responses = [mcp_server.handle_message(db, row, msg) for msg in body]
        return {"jsonrpc": "2.0", "result": responses}
    response = mcp_server.handle_message(db, row, body)
    return response
