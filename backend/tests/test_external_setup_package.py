# -*- coding: utf-8 -*-
"""外部接入体验与配置包 - 测试 (#43, A-20260910-003 LOCKED v1.3)

覆盖:
- enable: 吊销后可恢复 / 幂等 / 404 / 过期告警
- setup-package: **绝不含明文 token**(安全回归) / 按 scope 裁剪 / 端点表与 SCOPES 一致 / 限频三值渲染
- rotate: 旧 token 立即失效 / 404
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
from app.services import setup_package as SP

TZ = _dt.timezone(_dt.timedelta(hours=8))


@pytest.fixture()
def _enable_external(monkeypatch):
    monkeypatch.setattr(settings, "EXTERNAL_API_ENABLED", True)
    monkeypatch.setattr(settings, "EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN", 1000)
    yield


@pytest.fixture()
def admin_client(db, _enable_external):
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


# ─────────────────────────────────────────────────────────────────────
# enable: 吊销可逆
# ─────────────────────────────────────────────────────────────────────
def test_enable_restores_revoked_token(db, _enable_external):
    """正向: 吊销 → 恢复 → 可用。"""
    raw, row = S.generate_token(db, "t-enable", ["reception"], created_by="admin")
    S.revoke_token(db, row.id, revoked_by="admin")
    with pytest.raises(Exception) as exc:
        S.authenticate(db, raw)
    assert exc.value.status_code == 401

    restored = S.enable_token(db, row.id, restored_by="admin")
    assert restored is not None and restored.enabled is True
    assert restored.revoked_by is None  # 恢复后清空吊销人
    # 恢复后可正常鉴权
    assert S.authenticate(db, raw).id == row.id


def test_enable_is_idempotent(db, _enable_external):
    """边界: 对已启用 token 重复恢复 → 状态不变, 不报错。"""
    raw, row = S.generate_token(db, "t-idem", ["reception"])
    assert row.enabled is True
    again = S.enable_token(db, row.id, restored_by="admin")
    assert again is not None and again.enabled is True


def test_enable_returns_none_for_unknown_id(db):
    """边界: 不存在的 id → None(路由层转 404)。"""
    assert S.enable_token(db, 999999, restored_by="admin") is None


def test_enable_http_restores_and_404(admin_client, db):
    """HTTP: 恢复端点 200 + 未知 id 404。"""
    _, row = S.generate_token(db, "t-http", ["refund"], created_by="admin")
    admin_client.post(f"/api/v1/external/tokens/{row.id}/revoke")
    resp = admin_client.post(f"/api/v1/external/tokens/{row.id}/enable")
    assert resp.status_code == 200
    assert resp.json()["data"]["enabled"] is True
    assert resp.json()["data"]["restored_by"] == "admin"

    assert admin_client.post("/api/v1/external/tokens/999999/enable").status_code == 404


def test_enable_warns_when_expired(admin_client, db):
    """边界: 已过期 token 恢复 → 带 warning(不静默失败)。"""
    _, row = S.generate_token(
        db, "t-exp", ["brief"],
        expires_at=_dt.datetime.now(TZ) - _dt.timedelta(hours=2),
    )
    admin_client.post(f"/api/v1/external/tokens/{row.id}/revoke")
    resp = admin_client.post(f"/api/v1/external/tokens/{row.id}/enable")
    assert resp.status_code == 200
    assert "warning" in resp.json()["data"]
    assert "过期" in resp.json()["data"]["warning"]


# ─────────────────────────────────────────────────────────────────────
# rotate: 换钥即失效
# ─────────────────────────────────────────────────────────────────────
def test_rotate_invalidates_old_token(db, _enable_external):
    """正向: 换钥后旧明文 401, 新明文 200。"""
    old_raw, row = S.generate_token(db, "t-rot", ["reception"])
    res = S.rotate_token(db, row.id)
    assert res is not None
    new_raw, _ = res
    assert new_raw != old_raw
    with pytest.raises(Exception) as exc:
        S.authenticate(db, old_raw)
    assert exc.value.status_code == 401
    assert S.authenticate(db, new_raw).id == row.id


def test_rotate_returns_none_for_unknown_id(db):
    """边界: 不存在 id → None。"""
    assert S.rotate_token(db, 999999) is None


def test_rotate_http_returns_plain_once(admin_client, db):
    """HTTP: 换钥返回新明文 + notice; 旧 token 立即失效。"""
    old_raw, row = S.generate_token(db, "t-rot2", ["brief"], created_by="admin")
    resp = admin_client.post(f"/api/v1/external/tokens/{row.id}/rotate")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["token"] and data["token"] != old_raw
    assert "失效" in data["notice"]
    # 旧 token 401
    r = admin_client.get("/api/v1/external/brief?period=daily",
                         headers={"Authorization": f"Bearer {old_raw}"})
    assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────
# setup-package: 安全 + 裁剪 + 一致性
# ─────────────────────────────────────────────────────────────────────
def test_setup_package_has_no_plain_token(admin_client, db):
    """🔒 安全回归: 配置包响应中任何字段都不得含真实明文 token。

    配置包不得成为绕过 hash 存储的明文回看后门 (A-20260910-003 §2.2)。
    """
    raw, row = S.generate_token(db, "t-pkg", ["brief", "refund"], created_by="admin")
    resp = admin_client.get(f"/api/v1/external/tokens/{row.id}/setup-package")
    assert resp.status_code == 200
    payload = resp.text
    # 明文 token 绝不出现在响应全文任何位置
    assert raw not in payload, "配置包响应泄漏了 token 明文!"
    # 占位符必须存在(提示调用方自行粘贴)
    assert SP.TOKEN_PLACEHOLDER in resp.json()["data"]["markdown"]


def test_setup_package_scoped_endpoints(admin_client, db):
    """正向: scope=brief 时配置包只含 brief 端点。"""
    _, row = S.generate_token(db, "t-scope", ["brief"], created_by="admin")
    resp = admin_client.get(f"/api/v1/external/tokens/{row.id}/setup-package")
    endpoints = resp.json()["data"]["endpoints"]
    assert endpoints, "brief scope 应至少有一个端点"
    assert all(e["scope"] == "brief" for e in endpoints)


def test_setup_package_endpoints_match_scopes_constant():
    """一致性: 端点映射表覆盖 S.SCOPES 全集(防新增 scope 漏更新配置包)。"""
    assert set(SP.SCOPE_ENDPOINTS.keys()) == set(S.SCOPES), (
        f"配置包端点表 {sorted(SP.SCOPE_ENDPOINTS)} 与 SCOPES {sorted(S.SCOPES)} 不一致"
    )
    # 口径提示词也应覆盖全部 scope
    assert set(SP.SCOPE_PROMPTS.keys()) == set(S.SCOPES)


def test_setup_package_rate_limit_rendering(db):
    """边界: 限频三值语义渲染正确(0=不限制 / NULL=全局默认 / >0=个性化)。"""
    # 0 → 不限制
    _, r0 = S.generate_token(db, "rl0", ["brief"], rate_limit_per_min=0)
    md0 = SP.build_setup_package(r0, "https://x.com", global_rate_limit=60)["markdown"]
    assert "不限制" in md0

    # NULL → 全局默认
    _, rn = S.generate_token(db, "rln", ["brief"], rate_limit_per_min=None)
    mdn = SP.build_setup_package(rn, "https://x.com", global_rate_limit=77)["markdown"]
    assert "全局默认(77 次/分钟)" in mdn

    # >0 → 个性化
    _, rp = S.generate_token(db, "rlp", ["brief"], rate_limit_per_min=12)
    mdp = SP.build_setup_package(rp, "https://x.com", global_rate_limit=77)["markdown"]
    assert "12 次/分钟" in mdp


def test_setup_package_contains_base_url_and_auth(db):
    """正向: 配置包含接口前缀 + 鉴权格式 + 调用示例。"""
    _, row = S.generate_token(db, "t-md", ["brief"])
    pkg = SP.build_setup_package(row, "https://csctalon.example.com", global_rate_limit=60)
    md = pkg["markdown"]
    assert "https://csctalon.example.com/api/v1/external" in md
    assert "Authorization: Bearer" in md
    assert "curl" in md
    assert "输出要求" in md  # 提示词段


def test_setup_package_no_endpoints_placeholder(db):
    """边界: 无可用端点时给出明确占位说明, 不产出残缺表格。"""
    _, row = S.generate_token(db, "t-empty", ["brief"])
    row.scopes = []  # 模拟异常数据(库中理论上不会出现)
    pkg = SP.build_setup_package(row, "https://x.com", global_rate_limit=60)
    assert pkg["endpoints"] == []
    assert "未配置任何可用端点" in pkg["markdown"]


def test_setup_package_404_for_unknown_token(admin_client):
    """边界: 未知 token id → 404。"""
    assert admin_client.get("/api/v1/external/tokens/999999/setup-package").status_code == 404
