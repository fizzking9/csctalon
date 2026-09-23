# -*- coding: utf-8 -*-
"""跨站免密票据（csctalon 签发 → netmgr 面板消费）单测，工单 #187。

覆盖：
- 票据格式与声明：**独立重算** HMAC 验签（不与被测实现共用代码），校验 aud/sub/exp/jti 形状
- 每次签发 jti 不同（「一次性」的前提）
- 端点鉴权：ADMIN → 200；非 ADMIN（supervisor）→ 403
- 未配置共享密钥 → 503
- 票据与 next 均放 URL fragment（不发给服务端，故不进 access log）
"""

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import sso_ticket as T

SECRET = "unit-test-shared-secret-0123456789abcdef"


def _decode_and_verify(secret: str, ticket: str) -> dict:
    """独立实现的验签（刻意不复用 sso_ticket 内部函数，避免自证循环）。"""
    payload, sig = ticket.rsplit(".", 1)
    expected = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    assert hmac.compare_digest(sig, expected), "签名与独立重算结果不一致"
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return claims


@pytest.fixture()
def _sso_secret(monkeypatch):
    monkeypatch.setattr(settings, "SSO_SHARED_SECRET", SECRET)
    monkeypatch.setattr(settings, "SSO_TICKET_TTL_SECONDS", 60)
    monkeypatch.setattr(settings, "SSO_NETMGR_URL", "https://panel.example.test")
    yield


def _client_with_role(role: RoleEnum, db):
    """构造带指定角色用户的 TestClient（覆盖 get_current_user，与既有用例同款做法）。"""
    fake = User(id=1, username="tester", role=role.value, is_active=True)

    def _override_user():
        return fake

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app), fake


# ── service 层：票据格式与声明 ──────────────────────────────────────
def test_ticket_format_and_claims(_sso_secret):
    ticket = T.issue_ticket("admin")
    claims = _decode_and_verify(SECRET, ticket)

    assert claims["sub"] == "admin"
    assert claims["aud"] == T.AUDIENCE == "netmgr"
    # 消费方 netmgr 的 jti 正则要求 ^[a-f0-9]{16,64}$ —— 长度/字符集必须对齐
    assert len(claims["jti"]) == 16 and all(c in "0123456789abcdef" for c in claims["jti"])
    # exp 应为「当前时间 + TTL」量级（±5 秒容差）
    import time

    assert abs(claims["exp"] - (int(time.time()) + 60)) <= 5


def test_ticket_jti_is_unique_per_issue(_sso_secret):
    """一次性票据的前提：每次签发的 jti 必须不同。"""
    jtis = {_decode_and_verify(SECRET, T.issue_ticket("admin"))["jti"] for _ in range(20)}
    assert len(jtis) == 20


def test_ticket_rejects_wrong_secret(_sso_secret):
    """换一个密钥重算签名必须不一致（证明签名确实绑定共享密钥）。"""
    ticket = T.issue_ticket("admin")
    payload, sig = ticket.rsplit(".", 1)
    other = (
        base64.urlsafe_b64encode(
            hmac.new(b"another-secret", payload.encode("ascii"), hashlib.sha256).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    assert not hmac.compare_digest(sig, other)


def test_entry_url_puts_ticket_in_fragment(_sso_secret):
    url = T.build_netmgr_entry_url("admin")
    assert url.startswith("https://panel.example.test/sso/land#ticket=")
    # 票据与 next 都在 # 之后 → 浏览器不会发给服务端
    assert "&next=/dashboard" in url
    assert url.count("?") == 0


# ── 端点层：鉴权与配置门禁 ────────────────────────────────────────
def test_endpoint_admin_gets_ticket(_sso_secret, db):
    client, _ = _client_with_role(RoleEnum.ADMIN, db)
    try:
        resp = client.post("/api/v1/auth/sso/ticket")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["expires_in"] == 60
    ticket = body["data"]["url"].split("#ticket=", 1)[1].split("&", 1)[0]
    assert _decode_and_verify(SECRET, ticket)["sub"] == "tester"


def test_endpoint_non_admin_forbidden(_sso_secret, db):
    """非 ADMIN（supervisor）不得签票 —— 免密入口只给系统管理员。"""
    client, _ = _client_with_role(RoleEnum.SUPERVISOR, db)
    try:
        resp = client.post("/api/v1/auth/sso/ticket")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


def test_endpoint_disabled_without_secret(monkeypatch, db):
    """未配置共享密钥 → 503，且不影响本系统其它登录能力。"""
    monkeypatch.setattr(settings, "SSO_SHARED_SECRET", "")
    client, _ = _client_with_role(RoleEnum.ADMIN, db)
    try:
        resp = client.post("/api/v1/auth/sso/ticket")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503


def test_entries_reports_availability(_sso_secret, monkeypatch, db):
    """入口可用性：已配密钥 → netmgr_enabled=True；talon 回办公网与 ZT 两个候选地址。"""
    monkeypatch.setattr(settings, "SSO_TALON_URL_LAN", "http://talon-lan.example.test:4080")
    monkeypatch.setattr(settings, "SSO_TALON_URL_ZT", "http://talon-zt.example.test:4080")
    client, _ = _client_with_role(RoleEnum.ADMIN, db)
    try:
        resp = client.get("/api/v1/auth/sso/entries")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data == {
        "netmgr_enabled": True,
        "talon_url_lan": "http://talon-lan.example.test:4080",
        "talon_url_zt": "http://talon-zt.example.test:4080",
    }


def test_entries_empty_when_talon_unconfigured(_sso_secret, monkeypatch, db):
    """talon 两个地址都未配置 → 均回空串（前端据此不显示该入口）。"""
    monkeypatch.setattr(settings, "SSO_TALON_URL_LAN", "")
    monkeypatch.setattr(settings, "SSO_TALON_URL_ZT", "")
    client, _ = _client_with_role(RoleEnum.ADMIN, db)
    try:
        resp = client.get("/api/v1/auth/sso/entries")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["talon_url_lan"] == "" and data["talon_url_zt"] == ""


def test_entries_forbidden_for_non_admin(_sso_secret, db):
    client, _ = _client_with_role(RoleEnum.SUPERVISOR, db)
    try:
        resp = client.get("/api/v1/auth/sso/entries")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
