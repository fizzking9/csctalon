# -*- coding: utf-8 -*-
"""接入配置包模板回滚测试 (#47, C 方案: previous_content_md 单字段 + 可逆回滚)

覆盖:
- 更新 content_md 后 previous_content_md 快照有值, has_previous=True
- 同内容重复提交不产生空快照(避免覆盖真实历史)
- 回滚后 content_md 复原为更新前值, previous_content_md 变为回滚前的值(可逆)
- 连续两次回滚可往返(回滚→再回滚→恢复到回滚前状态)
- 无历史版本时回滚返回 400(符合 #47 路由层契约)
- 缺 confirm=true 时返回 400(二次确认防误点)
- 404 未知模板
- 权限: 非 admin 调回滚 → 403
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import integration_profile_service as svc


def _update_payload(content_md: str | None = None, **kw):
    """构造 update_profile 入参(模拟 Pydantic IntegrationProfileUpdate)。

    service 读 payload.name/scene/content_md/variables/enabled/sort_order,
    缺一不可(否则 AttributeError) → 用 SimpleNamespace 把全字段补齐默认 None。
    """
    return SimpleNamespace(
        name=None, scene=None, content_md=content_md,
        variables=None, enabled=None, sort_order=None, **kw,
    )


def _uniq(prefix: str) -> str:
    """跨运行唯一 code(避免与 test_integration_profiles 残留冲突)。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def admin_client(db):
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


@pytest.fixture()
def agent_client(db):
    fake = User(id=2, username="agent01", role=RoleEnum.AGENT.value, is_active=True)

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


def _make_profile(db, code: str, content_md: str):
    """直建模板行(绕过 HTTP, 供服务层测试用)。"""
    from app.models.integration_profile import IntegrationProfile
    row = IntegrationProfile(
        code=_uniq(code), name=code, scene="push",
        content_md=content_md, variables=[], enabled=True, sort_order=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ─────────────────────────────────────────────────────────────────────
# 服务层
# ─────────────────────────────────────────────────────────────────────
def test_update_snapshots_previous_content_md(db):
    """正向: 更新 content_md 后 previous_content_md = 旧值, has_previous=True。"""
    row = _make_profile(db, "snap-init", "v1")
    # 第一次更新: previous 应记录 v1
    svc.update_profile(db, row.id, _update_payload(content_md="v2"))
    db.refresh(row)
    assert row.content_md == "v2"
    assert row.previous_content_md == "v1"

    # 第二次更新: previous 应更新为 v2
    svc.update_profile(db, row.id, _update_payload(content_md="v3"))
    db.refresh(row)
    assert row.content_md == "v3"
    assert row.previous_content_md == "v2"

    db.delete(row)
    db.commit()


def test_update_same_content_does_not_overwrite_previous(db):
    """边界: 提交相同 content_md 不产生空快照(保留真实历史)。"""
    row = _make_profile(db, "snap-same", "v1")
    # 先产生一个真实的 previous
    svc.update_profile(db, row.id, _update_payload(content_md="v2"))
    db.refresh(row)
    assert row.previous_content_md == "v1"

    # 再次提交相同 v2: previous 不应被覆盖为 v2(否则历史丢失)
    svc.update_profile(db, row.id, _update_payload(content_md="v2"))
    db.refresh(row)
    assert row.previous_content_md == "v1"

    db.delete(row)
    db.commit()


def test_rollback_restores_previous_and_keeps_current_as_new_previous(db):
    """正向+可逆: 回滚后 content_md 复原为 previous; 当前 content_md 变为新的 previous。"""
    row = _make_profile(db, "rb-init", "v1")
    svc.update_profile(db, row.id, _update_payload(content_md="v2"))
    db.refresh(row)
    # 此时: content_md=v2, previous=v1

    out = svc.rollback_profile(db, row.id, actor="tester")
    assert out["content_md"] == "v1"
    assert out["previous_content_md"] == "v2"  # 当前 v2 变为新的 previous(可逆)
    assert out["has_previous"] is True

    db.delete(row)
    db.commit()


def test_rollback_twice_restores_to_pre_rollback_state(db):
    """可逆回滚: 回滚→再回滚→回到回滚前状态。"""
    row = _make_profile(db, "rb-twice", "v1")
    svc.update_profile(db, row.id, _update_payload(content_md="v2"))
    db.refresh(row)
    # 状态: content=v2, previous=v1

    # 第一次回滚: content→v1, previous→v2
    svc.rollback_profile(db, row.id)
    db.refresh(row)
    assert row.content_md == "v1"
    assert row.previous_content_md == "v2"

    # 第二次回滚: content→v2, previous→v1 (回到回滚前)
    svc.rollback_profile(db, row.id)
    db.refresh(row)
    assert row.content_md == "v2"
    assert row.previous_content_md == "v1"

    db.delete(row)
    db.commit()


def test_rollback_returns_no_previous_marker_when_no_history(db):
    """边界: previous_content_md IS NULL 时返回 {"_no_previous": True} 让路由层转 400。"""
    row = _make_profile(db, "rb-empty", "v1")  # 从未更新过 → previous=None
    result = svc.rollback_profile(db, row.id)
    assert isinstance(result, dict)
    assert result.get("_no_previous") is True

    db.delete(row)
    db.commit()


def test_rollback_returns_none_for_unknown_profile(db):
    """边界: 未知模板 id → 返回 None(由路由层转 404)。"""
    assert svc.rollback_profile(db, 999_999_999) is None


# ─────────────────────────────────────────────────────────────────────
# HTTP 路由层
# ─────────────────────────────────────────────────────────────────────
def test_rollback_http_requires_confirm_true(admin_client, db):
    """边界: 请求体缺 confirm=true → 400(防误点)。"""
    row = _make_profile(db, "http-noconfirm", "v1")
    svc.update_profile(db, row.id, _update_payload(content_md="v2"))

    # 不带 confirm
    r = admin_client.post(f"/api/v1/integrations/profiles/{row.id}/rollback", json={})
    assert r.status_code == 400
    assert "二次确认" in r.json()["detail"]

    # confirm=false
    r2 = admin_client.post(
        f"/api/v1/integrations/profiles/{row.id}/rollback", json={"confirm": False}
    )
    assert r2.status_code == 400

    db.delete(row)
    db.commit()


def test_rollback_http_success(admin_client, db):
    """正向: confirm=true + 有历史版本 → 200 + content_md 复原。"""
    row = _make_profile(db, "http-ok", "v1")
    svc.update_profile(db, row.id, _update_payload(content_md="v2"))

    r = admin_client.post(
        f"/api/v1/integrations/profiles/{row.id}/rollback", json={"confirm": True}
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["content_md"] == "v1"
    assert data["previous_content_md"] == "v2"  # 当前 v2 存为新 previous(可逆)
    assert data["has_previous"] is True

    db.delete(row)
    db.commit()


def test_rollback_http_no_history_returns_400(admin_client, db):
    """边界: 无历史版本 → 400 + 提示「没有可回滚的历史版本」。"""
    row = _make_profile(db, "http-empty", "v1")  # 从未更新

    r = admin_client.post(
        f"/api/v1/integrations/profiles/{row.id}/rollback", json={"confirm": True}
    )
    assert r.status_code == 400
    assert "没有可回滚" in r.json()["detail"]

    db.delete(row)
    db.commit()


def test_rollback_http_404_unknown_profile(admin_client):
    """边界: 未知模板 id → 404。"""
    r = admin_client.post(
        "/api/v1/integrations/profiles/999999/rollback", json={"confirm": True}
    )
    assert r.status_code == 404


def test_rollback_http_returns_previous_content_md_in_list(admin_client, db):
    """正向: GET /profiles 返回 has_previous / previous_content_md 字段(供前端显隐按钮)。"""
    row = _make_profile(db, "http-list", "v1")
    svc.update_profile(db, row.id, _update_payload(content_md="v2"))

    lst = admin_client.get("/api/v1/integrations/profiles", params={"include_disabled": True}).json()["data"]
    hit = next(p for p in lst if p["id"] == row.id)
    assert hit["has_previous"] is True
    assert hit["previous_content_md"] == "v1"

    db.delete(row)
    db.commit()


# ─────────────────────────────────────────────────────────────────────
# 权限
# ─────────────────────────────────────────────────────────────────────
def test_rollback_http_agent_forbidden(agent_client, db):
    """边界: 非 admin 调回滚 → 403(同其他接入模板管理端点)。"""
    row = _make_profile(db, "http-forbidden", "v1")
    svc.update_profile(db, row.id, _update_payload(content_md="v2"))

    r = agent_client.post(
        f"/api/v1/integrations/profiles/{row.id}/rollback", json={"confirm": True}
    )
    assert r.status_code == 403

    db.delete(row)
    db.commit()
