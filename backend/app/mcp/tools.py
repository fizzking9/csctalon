"""MCP 工具白名单 (#327)

首批 2 个工具, 全部复用 ai_data_supply 统一聚合层, 口径单一真身。
新增工具需在此显式注册, 代码评审把关。

scope 说明:
  - "device": 单设备行为(档案+生命周期KPI+时间线)
  - "recharge": 充值&流量聚合
  与 REST external 端点共用同一 scope, 便于统一授权管理。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.mcp.server import mcp_server
from app.services import ai_data_supply as ADS


def _tool_device_behavior(db: Session, sn: str, timeline_days: int = 90, timeline_limit: int = 50) -> dict | None:
    """单设备档案 + 生命周期 KPI + 近期事件时间线"""
    return ADS.supply_device_behavior(db, sn, timeline_limit=timeline_limit, timeline_days=timeline_days)


def _tool_recharge_traffic(db: Session, sn: str | None = None, start: str | None = None, end: str | None = None) -> dict:
    """充值套餐与流量聚合"""
    return ADS.supply_recharge(db, sn=sn, start=start, end=end)


# ─────────────────────────────────────────────────────────────────────
# 注册白名单
# ─────────────────────────────────────────────────────────────────────
mcp_server.register_tool(
    name="device_behavior",
    description=(
        "单设备 360 视图: 设备档案 + 生命周期 KPI(充值/退款/投诉/工单/流量) + "
        "近期事件时间线。设备不存在时返回 null。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sn": {
                "type": "string",
                "description": "设备 SN(精确匹配, 如 D250903182635081)",
            },
            "timeline_days": {
                "type": "integer",
                "description": "时间线回溯天数, 默认 90, 范围 1-365",
                "default": 90,
                "minimum": 1,
                "maximum": 365,
            },
            "timeline_limit": {
                "type": "integer",
                "description": "时间线事件封顶条数, 默认 50, 范围 1-200",
                "default": 50,
                "minimum": 1,
                "maximum": 200,
            },
        },
        "required": ["sn"],
    },
    scope="device",
    handler=_tool_device_behavior,
)

mcp_server.register_tool(
    name="recharge_traffic",
    description=(
        "充值套餐与流量聚合: 按 SN/时间窗统计充值笔数、金额(元)、可用/已用/剩余流量(字节/GB)。"
        "缺省 sn 时返回全部设备聚合; start/end 为北京日历日, 跨度≤93 天。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sn": {
                "type": "string",
                "description": "设备 SN(精确; 缺省=全部设备聚合)",
            },
            "start": {
                "type": "string",
                "description": "充值时间起 YYYY-MM-DD(与 end 成对, 整天)",
                "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
            },
            "end": {
                "type": "string",
                "description": "充值时间止 YYYY-MM-DD(与 start 成对, ≤今天, 跨度≤93天)",
                "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
            },
        },
        "required": [],
    },
    scope="recharge",
    handler=_tool_recharge_traffic,
)
