# -*- coding: utf-8 -*-
"""外部只读开放平台 - token 管理 + 鉴权状态机测试。

对应架构: docs/arch/A-20260902-021-外部工具只读开放平台.md (LOCKED v1.0)
覆盖:
- token 生成: 明文仅一次 / 库中存 hash+prefix(不存明文)
- scope 校验: 无 scope → 403
- 吊销: enabled=false 后 → 401
- 过期: expires_at 已过 → 401
- 鉴权状态机: 未启用→503 / 缺失→401 / 错误→401 / 正常→200
- 只读端点: 正确 scope → 200 且返回结构
"""

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import external_service as S

TZ = _dt.timezone(_dt.timedelta(hours=8))


@pytest.fixture()
def _enable_external(monkeypatch):
    """测试启用外部只读接口(默认 EXTERNAL_API_ENABLED=false)。"""
    monkeypatch.setattr(settings, "EXTERNAL_API_ENABLED", True)
    monkeypatch.setattr(settings, "EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN", 1000)
    yield


@pytest.fixture()
def admin_client(db, _enable_external):
    """带 admin 用户的 TestClient(管理端点用)。"""
    fake = User(id=1, username="admin", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


# ── token 生成/吊销(service 层) ──────────────────────────────────
def test_generate_token_hash_only(db):
    """正向: 生成 token 明文仅返回一次, 库中只存 hash+prefix。"""
    raw, row = S.generate_token(db, "workbuddy-简报", ["reception"], created_by="admin")
    assert raw and len(raw) >= 32
    assert row.token_prefix == raw[:8]
    # 明文不落库
    assert row.token_hash != raw
    # 校验可用
    found = S.find_token_by_hash(db, S._hash_token(raw))
    assert found is not None and found.id == row.id


def test_generate_token_invalid_scope(db):
    """边界: 非法 scope → 400。"""
    with pytest.raises(Exception) as exc:
        S.generate_token(db, "bad", ["performance"])
    assert exc.value.status_code == 400


def test_revoke_token(db, _enable_external):
    """正向: 吊销后 enabled=false, authenticate → 401。"""
    raw, row = S.generate_token(db, "t1", ["reception"])
    assert S.revoke_token(db, row.id, revoked_by="admin") is True
    db.refresh(row)
    assert row.enabled is False
    with pytest.raises(Exception) as exc:
        S.authenticate(db, raw)
    assert exc.value.status_code == 401


def test_authenticate_disabled(db, monkeypatch):
    """边界: EXTERNAL_API_ENABLED=false → 503。"""
    monkeypatch.setattr(settings, "EXTERNAL_API_ENABLED", False)
    with pytest.raises(Exception) as exc:
        S.authenticate(db, "whatever")
    assert exc.value.status_code == 503


def test_authenticate_bad_token(db, _enable_external):
    """边界: 错误 token → 401。"""
    with pytest.raises(Exception) as exc:
        S.authenticate(db, "wrong-token")
    assert exc.value.status_code == 401


def test_authenticate_expired(db, _enable_external):
    """边界: 过期 token → 401。"""
    raw, row = S.generate_token(
        db, "expired", ["reception"],
        expires_at=_dt.datetime.now(TZ) - _dt.timedelta(hours=1),
    )
    with pytest.raises(Exception) as exc:
        S.authenticate(db, raw)
    assert exc.value.status_code == 401


def test_require_scope(db, _enable_external):
    """边界: token 无对应 scope → 403; 有则通过。"""
    raw, row = S.generate_token(db, "only-reception", ["reception"])
    S.require_scope(row, "reception")  # 不抛
    with pytest.raises(Exception) as exc:
        S.require_scope(row, "refund")
    assert exc.value.status_code == 403


# ── HTTP 鉴权状态机(管理 + 只读) ────────────────────────────────
def test_tokens_crud_and_auth_state(admin_client, db):
    """正向+边界: 管理创建 token → 外部读接口 200; 吊销后 401; 错误 401; 无 scope 403。"""
    # 管理创建(带 refund scope)
    resp = admin_client.post("/api/v1/external/tokens", json={
        "name": "workbuddy", "scopes": ["refund"], "rate_limit_per_min": 5,
    })
    assert resp.status_code == 200
    body = resp.json()["data"]
    raw = body["token"]
    assert raw  # 明文仅此一次
    assert body["token_prefix"] == raw[:8]

    headers = {"Authorization": f"Bearer {raw}"}
    # 正确 scope → 200
    r = admin_client.get("/api/v1/external/refund", headers=headers)
    assert r.status_code == 200
    assert r.json()["code"] == 0
    assert "refund_count" in r.json()["data"]
    assert "_spec" in r.json()["data"]

    # 无 scope(reception) → 403
    r2 = admin_client.get("/api/v1/external/reception", headers=headers)
    assert r2.status_code == 403

    # 错误 token → 401
    r3 = admin_client.get("/api/v1/external/refund", headers={"Authorization": "Bearer bad"})
    assert r3.status_code == 401

    # 吊销 → 401
    rev = admin_client.post(f"/api/v1/external/tokens/{body['id']}/revoke")
    assert rev.status_code == 200
    r4 = admin_client.get("/api/v1/external/refund", headers=headers)
    assert r4.status_code == 401


def test_reception_endpoint_structure(admin_client, db):
    """正向: reception 只读端点返回结构与 _spec。"""
    raw, _ = S.generate_token(db, "r1", ["reception"], created_by="admin")
    resp = admin_client.get("/api/v1/external/reception", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "reception" in data and "date" in data and "_spec" in data
