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


def test_external_invalid_date_returns_400(admin_client, db):
    """CRITICAL-非法date回归: 三个单日端点 + brief daily 传非法 date → 400(架构 §2.1/§八)。

    旧实现会把非法 date 透传给聚合层抛 ValueError → 500; 修复后须在路由层拦截返回 400。
    """
    # 给每个端点配齐对应 scope 的 token
    tok = {}
    for scope in ("reception", "refund", "complaint", "brief"):
        raw, _ = S.generate_token(db, f"d-{scope}", [scope], created_by="admin")
        tok[scope] = {"Authorization": f"Bearer {raw}"}

    for path, scope in (("/reception", "reception"), ("/refund", "refund"),
                        ("/complaint", "complaint")):
        for bad in ("abc", "2026-13-01", "2026/09/01", "2026-9-1"):
            r = admin_client.get(f"/api/v1/external{path}?date={bad}", headers=tok[scope])
            assert r.status_code == 400, f"{path} date={bad!r} 应 400, 实得 {r.status_code}"
    # brief daily 非法 date
    for bad in ("abc", "2026-13-01", "2026/09/01"):
        r = admin_client.get(f"/api/v1/external/brief?period=daily&date={bad}", headers=tok["brief"])
        assert r.status_code == 400, f"brief date={bad!r} 应 400, 实得 {r.status_code}"


def test_external_valid_historical_date_ok(admin_client, db):
    """正向: 合法历史 date(整天, 非实时) → 200(不误伤)。"""
    raw, _ = S.generate_token(db, "h1", ["reception"], created_by="admin")
    resp = admin_client.get("/api/v1/external/reception?date=2026-07-01",
                            headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200


# ── F-P2-5 (2026-09-04): owner_note 责任人/用途备注 ────────────────
def test_generate_token_owner_note_persisted(db):
    """service: generate_token 带 owner_note → 持久化。"""
    _, row = S.generate_token(db, "wb-brief", ["brief"], created_by="admin",
                              owner_note="workbuddy-日报-李四跟进")
    assert row.owner_note == "workbuddy-日报-李四跟进"
    assert row.owner_note[:255] == row.owner_note


def test_tokens_crud_owner_note(admin_client, db):
    """HTTP: create 带 owner_note → list 返回 owner_note; update 可改。"""
    # 创建带 owner_note
    resp = admin_client.post("/api/v1/external/tokens", json={
        "name": "wb-owner", "scopes": ["reception"], "owner_note": "责任人: 张三",
    })
    assert resp.status_code == 200
    created = resp.json()["data"]
    assert created.get("owner_note") == "责任人: 张三"
    tid = created["id"]

    # list 含 owner_note(created_by_display 在无真实 User 时为空, 允许)
    lst = admin_client.get("/api/v1/external/tokens")
    assert lst.status_code == 200
    row = next(x for x in lst.json()["data"] if x["id"] == tid)
    assert row["owner_note"] == "责任人: 张三"
    assert "created_by_display" in row  # 字段存在(可为 None, 因 fake user 未入库)

    # update 修改 owner_note
    up = admin_client.patch(f"/api/v1/external/tokens/{tid}", json={"owner_note": "已转交李四"})
    assert up.status_code == 200
    assert up.json()["data"]["owner_note"] == "已转交李四"

    # update 清空(None 不清, 显式空串清)
    up2 = admin_client.patch(f"/api/v1/external/tokens/{tid}", json={"owner_note": ""})
    assert up2.status_code == 200
    assert up2.json()["data"]["owner_note"] == ""


# ---- v1.2: 限频 0=不限制(三分支语义) ----
class _FakeRedis:
    """最小 Redis fake: incr/expire/get(供 check_rate_limit 测试)。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    def expire(self, key: str, seconds: int) -> bool:
        self.ttl[key] = seconds
        return True

    def get(self, key: str):
        return self.store.get(key)


@pytest.fixture()
def _fake_redis(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr(S, "get_redis", lambda: fr)
    return fr


def test_rate_limit_zero_unlimited_service(db, _fake_redis, _enable_external):
    """service: rate_limit_per_min=0 → check_rate_limit 直接 return(不计数/不 429)。

    v1.2 语义: 0=不限制(免除限频)。
    """
    _, row = S.generate_token(db, "unlimited", ["brief"], rate_limit_per_min=0)
    assert row.rate_limit_per_min == 0
    # 反复调用不抛(不限)
    for _ in range(10):
        S.check_rate_limit(row)  # 不抛
    # 且不做任何 Redis 计数(store 为空)
    assert _fake_redis.store == {}


def test_rate_limit_positive_enforced_service(db, _fake_redis, _enable_external):
    """service: rate_limit_per_min=1 → 超 1 次后抛 429。"""
    _, row = S.generate_token(db, "limited", ["brief"], rate_limit_per_min=1)
    S.check_rate_limit(row)  # 第 1 次 OK
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        S.check_rate_limit(row)  # 第 2 次 > 1 → 429
    assert exc.value.status_code == 429


def test_rate_limit_none_uses_global_default_service(db, _fake_redis, _enable_external):
    """service: rate_limit_per_min=None → 用全局默认(1000), 多次不超。"""
    _, row = S.generate_token(db, "global-default", ["brief"])
    assert row.rate_limit_per_min is None
    for _ in range(5):
        S.check_rate_limit(row)  # 1000 阈值, 不抛
    assert len(_fake_redis.store) == 1  # 已计数(key 存在)


def test_token_create_rate_limit_zero_http(admin_client, db):
    """HTTP: 创建 token 带 rate_limit_per_min=0(schema ge=0 放行) → 落库 0。"""
    resp = admin_client.post("/api/v1/external/tokens", json={
        "name": "wb-unlimited", "scopes": ["brief"], "rate_limit_per_min": 0,
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["rate_limit_per_min"] == 0


def test_token_update_rate_limit_clear_to_null_http(admin_client, db):
    """HTTP: update 显式 null → 清回全局默认(None); 显式 0 → 不限。

    v1.2 用 model_fields_set 区分"未传(不改)"与"显式 null(清回默认)"。
    """
    # 创建带 5
    c = admin_client.post("/api/v1/external/tokens", json={
        "name": "wb-rl", "scopes": ["brief"], "rate_limit_per_min": 5,
    })
    tid = c.json()["data"]["id"]
    # 显式 null → 清回默认(None)
    u1 = admin_client.patch(f"/api/v1/external/tokens/{tid}", json={"rate_limit_per_min": None})
    assert u1.status_code == 200
    assert u1.json()["data"]["rate_limit_per_min"] is None
    # 显式 0 → 不限
    u2 = admin_client.patch(f"/api/v1/external/tokens/{tid}", json={"rate_limit_per_min": 0})
    assert u2.status_code == 200
    assert u2.json()["data"]["rate_limit_per_min"] == 0

