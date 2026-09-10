# -*- coding: utf-8 -*-
"""导航重组配套的权限矩阵测试 (#46, A-20260910-004 LOCKED §四)

背景: 菜单把「分析视图」从 admin-only 放宽到 manager(可见即可读)时,
**必须同步调整 API 权限**, 否则出现两类问题:
  1. 菜单看得见但点了 403(体验断裂)—— 读接口还停在 CurrentUser 之上更严的层级?
  2. 菜单看不见但 API 能调(越权) —— 写接口停在 CurrentUser, 任何人都能改视图定义。

本测试锁住配对关系:
  - manager 读分析视图 → 200
  - agent 读分析视图 → 403(不再允许全员直读)
  - manager/agent 写分析视图 → 403(写锁 admin)
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User


def _client_for_role(db, role: str, username: str = "u1"):
    """构造带指定角色用户的 TestClient。"""
    fake = User(id=1, username=username, role=role, is_active=True)

    def _override_user():
        return fake

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


@pytest.fixture()
def clients(db):
    """一次拿到三个角色的 client(用完统一清理依赖覆盖)。"""
    made = {}

    def _make(role: str, username: str):
        if role not in made:
            made[role] = _client_for_role(db, role, username)
        return made[role]

    try:
        yield _make
    finally:
        for c in made.values():
            c.close()
        app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────
# 分析视图: 读放给 manager, 写锁 admin
# ─────────────────────────────────────────────────────────────────────
def test_manager_can_read_analysis_views(clients):
    """正向: manager 读分析视图 → 200(与菜单可见性一致)。"""
    c = clients(RoleEnum.SUPERVISOR.value, "sup1")
    r = c.get("/api/v1/analysis-views")
    assert r.status_code == 200


def test_specialty_lead_can_read_analysis_views(clients):
    """正向: 专项组长也属 manager, 可读。"""
    c = clients(RoleEnum.SPECIALTY_LEAD.value, "lead1")
    assert c.get("/api/v1/analysis-views").status_code == 200


def test_agent_cannot_read_analysis_views(clients):
    """边界(#46 配对收紧): agent 读分析视图 → 403。

    收紧前为 CurrentUser(全员可读) —— 那会让"菜单不可见"变成纯装饰,
    普通客服仍可经 API 直读主管视角视图。
    """
    c = clients(RoleEnum.AGENT.value, "agent1")
    assert c.get("/api/v1/analysis-views").status_code == 403


def test_manager_cannot_write_analysis_views(clients):
    """边界: manager 写分析视图 → 403(构建器菜单为 admin-only)。"""
    c = clients(RoleEnum.SUPERVISOR.value, "sup2")
    r = c.post("/api/v1/analysis-views", json={
        "code": "x1", "name": "x", "chart_type": "bar",
        "config": {}, "segments": [],
    })
    assert r.status_code == 403


def test_agent_cannot_write_analysis_views(clients):
    """边界(越权写回归): agent 写分析视图 → 403。

    原实现为 CurrentUser, 属审计项 A-P1-3「越权写」范畴。
    """
    c = clients(RoleEnum.AGENT.value, "agent2")
    r = c.post("/api/v1/analysis-views", json={
        "code": "x2", "name": "x", "chart_type": "bar",
        "config": {}, "segments": [],
    })
    assert r.status_code == 403


def test_agent_cannot_delete_analysis_view(clients):
    """边界: agent 删视图 → 403。"""
    c = clients(RoleEnum.AGENT.value, "agent3")
    assert c.delete("/api/v1/analysis-views/1").status_code == 403


# ─────────────────────────────────────────────────────────────────────
# 分类规则: 同上配对
# ─────────────────────────────────────────────────────────────────────
def test_manager_can_read_classification_rules(clients):
    """正向: manager 读分类规则 → 200。"""
    c = clients(RoleEnum.SUPERVISOR.value, "sup3")
    assert c.get("/api/v1/classification-rules").status_code == 200


def test_agent_cannot_write_classification_rules(clients):
    """边界: agent 写分类规则 → 403。"""
    c = clients(RoleEnum.AGENT.value, "agent4")
    r = c.post("/api/v1/classification-rules", json={
        "name": "r1", "table_name": "refunds",
        "target_field": "status", "mapping": {"a": "b"},
    })
    assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# 接入中心 (#44) — admin-only
# ─────────────────────────────────────────────────────────────────────
def test_manager_cannot_access_integration_profiles(clients):
    """边界: manager 接接入配置包模板 → 403(改接入物料属高风险)。"""
    c = clients(RoleEnum.SUPERVISOR.value, "sup4")
    assert c.get("/api/v1/integrations/profiles").status_code == 403


def test_manager_cannot_access_shared_doc_states(clients):
    """边界: manager 查采集源登录态 → 403(含凭据存在性信息)。"""
    c = clients(RoleEnum.SUPERVISOR.value, "sup5")
    assert c.get("/api/v1/sync/shared-docs/states").status_code == 403
