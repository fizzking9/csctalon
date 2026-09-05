# -*- coding: utf-8 -*-
"""device_flow 接口测试(设备流量审计)

覆盖:
1. GET /device-flow/usage 本地查询(空库 0 条) — 正向
2. POST /device-flow/fetch mock 上游返回数据 → 正确调用 + 返回统计 — 正向
3. POST /device-flow/fetch 缺 sn → 400 — 边界
4. 普通用户访问 fetch(管理员接口) → 403 — 边界
5. month 格式错误 → 400 — 边界
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app


@pytest.fixture()
def admin_client(db):
    """带 ADMIN 角色的 TestClient。"""
    from app.models.user import RoleEnum, User

    fake_user = User(id=1, username="admin", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake_user

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def agent_client(db):
    """普通 AGENT 用户(无 fetch 权限)。"""
    from app.models.user import RoleEnum, User

    fake_user = User(id=1, username="agent", role=RoleEnum.AGENT.value, is_active=True)

    def _override_user():
        return fake_user

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def test_usage_empty_ok(admin_client):
    """正向: 空库查询返回 0 条。"""
    resp = admin_client.get("/api/v1/device-flow/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["total"] == 0
    assert body["data"]["items"] == []


def test_fetch_missing_sn_400(admin_client, monkeypatch):
    """边界: 缺 sn → 422(FastAPI 必填 query 校验)。"""
    # 不真正触发上游(因 sn 缺失会被 FastAPI 校验拦截)
    resp = admin_client.post("/api/v1/device-flow/fetch")
    assert resp.status_code == 422


def test_fetch_bad_month_400(admin_client, monkeypatch):
    """边界: month 格式错 → 400。"""
    # mock 掉凭证解析避免依赖 DB 中的 refund 配置; 但 month 校验在凭证前? 看实现顺序
    # 实现里 month 格式校验在 _resolve_bms_credentials 之前, 故无需 mock 凭证
    resp = admin_client.post("/api/v1/device-flow/fetch", params={"sn": "SN1", "month": "2026-8"})
    assert resp.status_code == 400


def test_fetch_forbidden_for_agent(agent_client):
    """边界: 非管理员调 fetch → 403。"""
    resp = agent_client.post("/api/v1/device-flow/fetch", params={"sn": "SN1"})
    assert resp.status_code == 403


def test_fetch_mock_upstream_ok(admin_client, monkeypatch):
    """正向: mock 上游 sync_device_flow_sn 返回, 验证端点透传。"""
    import app.api.v1.device_flow as df_module

    captured = {}

    def fake_sync(sn, month, username, password, base_url):
        captured.update(sn=sn, month=month)
        return {
            "sn": sn, "month": month, "fetched_rows": 2,
            "inserted": 2, "updated": 0, "total_flow_gb": 30.0,
            "months": ["2026-08", "2026-07"], "message": "入库成功",
        }

    # mock _resolve_bms_credentials 避免依赖 refund 配置
    monkeypatch.setattr(df_module, "_resolve_bms_credentials", lambda db: ("https://wifi.cloud-cat.cn/bms", "u", "p"))
    # 由于 fetch 端点内直接 from app.services.device_flow_sync import sync_device_flow_sn,
    # monkeypatch 模块内引用无效 → patch service 函数本体
    import app.services.device_flow_sync as dfs
    monkeypatch.setattr(dfs, "sync_device_flow_sn", fake_sync)

    resp = admin_client.post("/api/v1/device-flow/fetch", params={"sn": "SN-TEST-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["inserted"] == 2
    assert body["data"]["total_flow_gb"] == 30.0
    assert captured["sn"] == "SN-TEST-1"
