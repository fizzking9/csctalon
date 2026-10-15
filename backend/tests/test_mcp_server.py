"""MCP Server 单元测试 (#327)

覆盖: initialize 握手 / tools/list / tools/call 鉴权与只读兜底。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.mcp.server import INVALID_PARAMS, METHOD_NOT_FOUND, McpSseServer, _err, _ok
from app.mcp.tools import _tool_device_behavior, _tool_recharge_traffic  # noqa: F401  触发注册
from app.models.external_token import ExternalAccessToken


@pytest.fixture()
def mcp_server():
    """每个测试独立 server 实例, 但复用全局已注册的工具"""
    server = McpSseServer()
    # 手动注册工具(测试隔离, 不依赖导入顺序)
    server.register_tool(
        name="device_behavior",
        description="单设备 360 视图",
        input_schema={"type": "object", "properties": {"sn": {"type": "string"}}, "required": ["sn"]},
        scope="device",
        handler=_tool_device_behavior,
    )
    server.register_tool(
        name="recharge_traffic",
        description="充值套餐与流量聚合",
        input_schema={"type": "object", "properties": {}},
        scope="recharge",
        handler=_tool_recharge_traffic,
    )
    return server


@pytest.fixture()
def mock_token():
    """模拟已认证的 external token"""
    t = MagicMock(spec=ExternalAccessToken)
    t.id = 1
    t.token_prefix = "test"
    t.scopes = ["device", "recharge"]
    t.enabled = True
    return t


class TestJsonRpcHelpers:
    def test_ok(self):
        assert _ok(1, {"a": 1}) == {"jsonrpc": "2.0", "id": 1, "result": {"a": 1}}

    def test_err(self):
        r = _err(1, -32600, "bad")
        assert r["jsonrpc"] == "2.0"
        assert r["id"] == 1
        assert r["error"]["code"] == -32600


class TestMcpSseServer:
    def test_initialize(self, mcp_server):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "test"}}}
        resp = mcp_server.handle_message(MagicMock(), MagicMock(), msg)
        assert resp["id"] == 1
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert resp["result"]["serverInfo"]["name"] == "csctalon-mcp"

    def test_tools_list(self, mcp_server):
        msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        resp = mcp_server.handle_message(MagicMock(), MagicMock(), msg)
        assert resp["id"] == 2
        tools = resp["result"]["tools"]
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"device_behavior", "recharge_traffic"}

    def test_unknown_method(self, mcp_server):
        msg = {"jsonrpc": "2.0", "id": 3, "method": "unknown/method"}
        resp = mcp_server.handle_message(MagicMock(), MagicMock(), msg)
        assert resp["error"]["code"] == METHOD_NOT_FOUND

    def test_tools_call_unknown_tool(self, mcp_server, mock_token):
        msg = {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "unknown_tool"}}
        resp = mcp_server.handle_message(MagicMock(), mock_token, msg)
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "Unknown tool" in resp["error"]["message"]

    def test_tools_call_scope_denied(self, mcp_server, mock_token):
        """无 scope 时 403"""
        from fastapi import HTTPException

        mock_token.scopes = ["device"]  # 只有 device, 无 recharge
        msg = {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "recharge_traffic", "arguments": {}}
        }
        with pytest.raises(HTTPException) as exc_info:
            mcp_server.handle_message(MagicMock(), mock_token, msg)
        assert exc_info.value.status_code == 403

    def test_readonly_enforced(self, mcp_server, mock_token, db: Session):
        """只读兜底: SET TRANSACTION READ ONLY 后写操作报错"""
        mcp_server._set_readonly(db)
        # 尝试写操作(创建临时表)应失败
        with pytest.raises(Exception):
            db.execute(text("CREATE TEMPORARY TABLE test_ro (id int)"))

    def test_device_behavior_tool(self, db: Session):
        """device_behavior 工具 handler 直接调用"""
        # 设备不存在返回 None
        result = _tool_device_behavior(db, "NONEXIST_SN_12345")
        assert result is None

    def test_recharge_traffic_tool(self, db: Session):
        """recharge_traffic 工具 handler 直接调用"""
        result = _tool_recharge_traffic(db, sn="NONEXIST_SN_12345")
        assert result["recharge_count"] == 0
        assert result["total_amount"] == 0.0


class TestMcpToolsRegistration:
    def test_tools_registered(self):
        """tools.py 导入后全局 server 已注册 2 个工具"""
        from app.mcp.server import mcp_server as global_server
        tools = global_server.list_tools()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"device_behavior", "recharge_traffic"}
