# -*- coding: utf-8 -*-
"""看板可见性权限 + 模型持久化测试。

对应架构文档: docs/arch/_pending/dashboard-system-rebuild.md (已实现, 待锁定)
权限双轨:
- visibility 字段(public/manager/admin) 做 API 行级过滤
- _allowed_visibility 按角色返回可见列表
- _check_visibility 无权则 403
"""


import pytest
from fastapi import HTTPException

from app.api.v1.dashboards import _allowed_visibility, _check_visibility
from app.models.dashboard import Dashboard

# ---- _allowed_visibility: 角色 → 可见性白名单(纯函数, 无需 DB) ----

def test_allowed_visibility_admin_sees_all():
    """正向: admin 可见 public/manager/admin 全部。"""
    assert _allowed_visibility("admin") == ["public", "manager", "admin"]


def test_allowed_visibility_supervisor_no_admin():
    """边界: supervisor 可见 public/manager, 但不可见 admin 专属。"""
    assert _allowed_visibility("supervisor") == ["public", "manager"]
    assert "admin" not in _allowed_visibility("supervisor")


def test_allowed_visibility_agent_public_only():
    """边界: 普通客服(agent) 仅可见 public。"""
    assert _allowed_visibility("agent") == ["public"]
    assert "manager" not in _allowed_visibility("agent")
    assert "admin" not in _allowed_visibility("agent")


def test_allowed_visibility_unknown_role_public_only():
    """边界: 未知角色按最保守处理(仅 public)。"""
    assert _allowed_visibility("unknown_role") == ["public"]


# ---- _check_visibility: 单看板校验, 无权 403 ----

def _dashboard(visibility: str) -> Dashboard:
    """构造最小 Dashboard 对象(不落库, 仅用于权限校验)。"""
    return Dashboard(
        name="测试看板",
        category="退款",
        items=[{"view_id": 1, "span": 12, "visible": True}],
        visibility=visibility,
    )


def test_check_visibility_allowed_no_error():
    """正向: 角色可见该看板 → 不抛异常。"""
    d = _dashboard("manager")
    _check_visibility(d, "supervisor")  # 不抛即通过
    _check_visibility(d, "admin")


def test_check_visibility_denied_raises_403():
    """边界: agent 访问 manager 看板 → 403。"""
    d = _dashboard("manager")
    with pytest.raises(HTTPException) as exc:
        _check_visibility(d, "agent")
    assert exc.value.status_code == 403


def test_check_visibility_admin_board_restricted():
    """边界: 非 admin(supervisor) 访问 admin 看板 → 403。"""
    d = _dashboard("admin")
    with pytest.raises(HTTPException):
        _check_visibility(d, "supervisor")


# ---- 模型持久化: items JSONB 字段 ----

def test_dashboard_items_jsonb_roundtrip(db):
    """正向: items JSONB 数组可写入并读回(结构保真)。"""
    d = Dashboard(
        name="退款看板",
        category="退款",
        items=[
            {"view_id": 12, "view_code": "preset_refund_trend",
             "title": None, "span": 16, "visible": True},
            {"view_id": 15, "view_code": "preset_refund_status",
             "title": "状态分布", "span": 8, "visible": True},
        ],
        time_range_default={"days": 30},
        is_preset=True,
        visibility="manager",
    )
    db.add(d)
    db.flush()

    reloaded = db.get(Dashboard, d.id)
    assert reloaded.items == [
        {"view_id": 12, "view_code": "preset_refund_trend",
         "title": None, "span": 16, "visible": True},
        {"view_id": 15, "view_code": "preset_refund_status",
         "title": "状态分布", "span": 8, "visible": True},
    ]
    assert reloaded.time_range_default == {"days": 30}
    assert reloaded.is_preset is True
