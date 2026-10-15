"""MCP 只读数据出口 — SSE Server 核心 (#327)

实现 MCP over SSE 协议(最小子集):
  - initialize: 握手, 返回 serverInfo + capabilities
  - tools/list: 返回白名单工具清单
  - tools/call: 分发到注册 handler, 复用 ai_data_supply 统一聚合层

安全约束:
  - 每个 tools/call 前执行 SET TRANSACTION READ ONLY(PG 会话级只读兜底)
  - 认证/限流/审计复用 external_service 体系
  - 工具白名单硬编码于 tools.py, 新增需代码评审
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.external_token import ExternalAccessToken
from app.services import external_service as S

logger = logging.getLogger(__name__)

# MCP 协议版本(2024-11-05 为当前 stable)
MCP_VERSION = "2024-11-05"

# JSON-RPC 2.0 错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _err(id_: Any, code: int, message: str, data: Any = None) -> dict:
    """JSON-RPC 2.0 错误响应"""
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def _ok(id_: Any, result: Any) -> dict:
    """JSON-RPC 2.0 成功响应"""
    return {"jsonrpc": "2.0", "id": id_, "result": result}


class McpSseServer:
    """MCP over SSE 服务器。

    职责:
      1. 管理工具注册表(白名单)
      2. 处理 JSON-RPC 2.0 消息路由
      3. 只读兜底(每个 call 前 SET TRANSACTION READ ONLY)
    """

    def __init__(self):
        self._tools: dict[str, dict] = {}  # name → {handler, schema, scope, description}

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        scope: str,
        handler: Callable[..., Any],
    ) -> None:
        """注册工具到白名单"""
        self._tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
            "scope": scope,
            "handler": handler,
        }

    def list_tools(self) -> list[dict]:
        """tools/list: 返回白名单工具清单"""
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["inputSchema"],
            }
            for t in self._tools.values()
        ]

    def _set_readonly(self, db: Session) -> None:
        """会话级只读兜底: PG 硬约束, 连接池归还自动 reset"""
        db.execute(text("SET TRANSACTION READ ONLY"))

    def handle_message(
        self,
        db: Session,
        token_row: ExternalAccessToken,
        msg: dict,
    ) -> dict:
        """JSON-RPC 2.0 消息入口。

        Args:
            db: SQLAlchemy Session(已认证)
            token_row: 已认证的 ExternalAccessToken
            msg: JSON-RPC 2.0 请求体

        Returns:
            JSON-RPC 2.0 响应体
        """
        if not isinstance(msg, dict):
            return _err(None, INVALID_REQUEST, "Request must be a JSON object")

        id_ = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", {})

        if method == "initialize":
            return self._handle_initialize(id_, params)
        if method == "tools/list":
            return self._handle_tools_list(id_)
        if method == "tools/call":
            return self._handle_tools_call(db, token_row, id_, params)

        return _err(id_, METHOD_NOT_FOUND, f"Method not found: {method}")

    def _handle_initialize(self, id_: Any, params: dict) -> dict:
        """initialize 握手"""
        client_info = params.get("clientInfo", {})
        logger.info("MCP initialize from client: %s", client_info.get("name", "unknown"))
        return _ok(id_, {
            "protocolVersion": MCP_VERSION,
            "serverInfo": {"name": "csctalon-mcp", "version": "0.1.0"},
            "capabilities": {
                "tools": {"listChanged": False},
            },
        })

    def _handle_tools_list(self, id_: Any) -> dict:
        """tools/list"""
        return _ok(id_, {"tools": self.list_tools()})

    def _handle_tools_call(
        self,
        db: Session,
        token_row: ExternalAccessToken,
        id_: Any,
        params: dict,
    ) -> dict:
        """tools/call: 分发到注册 handler"""
        name = params.get("name")
        args = params.get("arguments", {})

        if not name or name not in self._tools:
            return _err(id_, INVALID_PARAMS, f"Unknown tool: {name}")

        tool = self._tools[name]

        # scope 校验
        S.require_scope(token_row, tool["scope"])

        # 只读兜底
        self._set_readonly(db)

        # 审计
        args_summary = json.dumps(args, ensure_ascii=False, default=str)[:200]
        S.record_external_access(db, token_row, f"mcp:{name}:{args_summary}")

        # 执行 handler
        try:
            result = tool["handler"](db, **args)
            if result is None:
                return _err(id_, INVALID_PARAMS, f"Resource not found for tool: {name}")
            return _ok(id_, {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, default=str),
                    }
                ],
                "isError": False,
            })
        except ValueError as exc:
            return _err(id_, INVALID_PARAMS, str(exc))
        except Exception as exc:
            logger.exception("MCP tool call failed: %s", name)
            return _err(id_, INTERNAL_ERROR, f"Internal error: {type(exc).__name__}")


# 全局单例
mcp_server = McpSseServer()
