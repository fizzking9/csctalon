# -*- coding: utf-8 -*-
"""共享文档登录多 worker 部署守卫测试 (#48, A2 方案)

覆盖:
- 单 worker 时 4 个 login 端点正常放行(不抛 501)
- 多 worker 时 4 个 login 端点返回 501 + 提示「命令行」按钮
- is_multi_worker / check_single_worker 单元行为(monkeypatch multiprocessing.parent_process)
- check_single_worker 多 worker 返回 False 并打 WARNING(不 fail-fast)
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import shared_doc_login_service as sdls


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


# ─────────────────────────────────────────────────────────────────────
# 单元: is_multi_worker / check_single_worker
# ─────────────────────────────────────────────────────────────────────
def test_is_multi_worker_true_when_parent_process_not_none(monkeypatch):
    """多 worker: multiprocessing.parent_process() 返回非 None → True。"""
    monkeypatch.setattr(
        "multiprocessing.parent_process", lambda: object(),  # 非 None
    )
    assert sdls.is_multi_worker() is True


def test_is_multi_worker_false_when_parent_process_none(monkeypatch):
    """单 worker: parent_process() 返回 None → False。"""
    monkeypatch.setattr("multiprocessing.parent_process", lambda: None)
    assert sdls.is_multi_worker() is False


def test_is_multi_worker_false_when_parent_process_unavailable(monkeypatch):
    """边界: parent_process 抛异常(老版本/被破坏) → 按单 worker 处理(不阻断业务)。"""
    def _raise():
        raise RuntimeError("parent_process not available")
    monkeypatch.setattr("multiprocessing.parent_process", _raise)
    assert sdls.is_multi_worker() is False


def test_check_single_worker_returns_true_on_single(monkeypatch):
    """单 worker: check_single_worker 返回 True, 不打 WARNING。"""
    monkeypatch.setattr(sdls, "is_multi_worker", lambda: False)
    assert sdls.check_single_worker() is True


def test_check_single_worker_returns_false_on_multi_and_warns(monkeypatch, caplog):
    """多 worker: check_single_worker 返回 False 并打 WARNING(不 fail-fast)。"""
    monkeypatch.setattr(sdls, "is_multi_worker", lambda: True)
    with caplog.at_level(logging.WARNING, logger=sdls.logger.name):
        result = sdls.check_single_worker()
    assert result is False
    # 关键日志关键词: 让运维一眼看到「为什么不可用 + 怎么办」
    msgs = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
    assert "多 worker" in msgs
    assert "CLI 脚本" in msgs or "scripts" in msgs


# ─────────────────────────────────────────────────────────────────────
# HTTP: 4 个 login 端点守卫
# ─────────────────────────────────────────────────────────────────────
def _force_multi_worker(monkeypatch):
    """让 4 个 login 端点的守卫判定走多 worker 分支。"""
    monkeypatch.setattr(sdls, "is_multi_worker", lambda: True)


def _force_single_worker(monkeypatch):
    """让守卫判定走单 worker 分支(放行)。"""
    monkeypatch.setattr(sdls, "is_multi_worker", lambda: False)


def test_login_start_returns_501_on_multi_worker(admin_client, monkeypatch):
    """多 worker: POST /{id}/login/start 返回 501 + 提示「命令行」按钮。"""
    _force_multi_worker(monkeypatch)
    # 给一个真实存在的 source_id(随便取一个; 守卫先抛, 不查 source)
    r = admin_client.post("/api/v1/sync/shared-docs/1/login/start")
    assert r.status_code == 501
    detail = r.json()["detail"]
    assert "多 worker" in detail
    assert "命令行" in detail


def test_login_status_returns_501_on_multi_worker(admin_client, monkeypatch):
    """多 worker: GET /login/{sid}/status 返回 501。"""
    _force_multi_worker(monkeypatch)
    r = admin_client.get("/api/v1/sync/shared-docs/login/any-session-id/status")
    assert r.status_code == 501


def test_login_confirm_returns_501_on_multi_worker(admin_client, monkeypatch):
    """多 worker: POST /login/{sid}/confirm 返回 501。"""
    _force_multi_worker(monkeypatch)
    r = admin_client.post("/api/v1/sync/shared-docs/login/any-session-id/confirm")
    assert r.status_code == 501


def test_login_cancel_returns_501_on_multi_worker(admin_client, monkeypatch):
    """多 worker: DELETE /login/{sid} 返回 501。"""
    _force_multi_worker(monkeypatch)
    r = admin_client.delete("/api/v1/sync/shared-docs/login/any-session-id")
    assert r.status_code == 501


def test_login_endpoints_passthrough_on_single_worker(admin_client, monkeypatch):
    """单 worker: 4 个 login 端点不抛 501(走原有业务逻辑: 404/400 等)。"""
    _force_single_worker(monkeypatch)
    # start 对不存在 source: 守卫放行 → 业务层 400(source_id 不是 int 或不存在)
    # 注意: source_id 是 path int, FastAPI 自动转换; 不存在的 source → 业务层 404
    # 此处只断言「不是 501」即可证明守卫放行
    r1 = admin_client.post("/api/v1/sync/shared-docs/999999/login/start")
    assert r1.status_code != 501

    r2 = admin_client.get("/api/v1/sync/shared-docs/login/nonexistent/status")
    assert r2.status_code != 501  # 走原有 404

    r3 = admin_client.post("/api/v1/sync/shared-docs/login/nonexistent/confirm")
    assert r3.status_code != 501

    r4 = admin_client.delete("/api/v1/sync/shared-docs/login/nonexistent")
    # cancel 是幂等的, 单 worker 时即使无 session 也应 200
    assert r4.status_code == 200


def test_states_endpoint_not_guarded_on_multi_worker(admin_client, monkeypatch):
    """边界: GET /states 不受守卫(只读接口, 多 worker 也能查登录态文件 mtime)。"""
    _force_multi_worker(monkeypatch)
    r = admin_client.get("/api/v1/sync/shared-docs/states")
    # /states 不加守卫, 仍应正常返回(200)
    assert r.status_code == 200


def test_cli_fallback_endpoint_not_guarded_on_multi_worker(admin_client, monkeypatch):
    """边界: GET /cli-fallback 不受守卫(多 worker 时反而最该可用, 给降级命令)。"""
    _force_multi_worker(monkeypatch)
    r = admin_client.get("/api/v1/sync/shared-docs/cli-fallback")
    assert r.status_code == 200
    data = r.json().get("data", {})
    assert "commands" in data
