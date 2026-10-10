# -*- coding: utf-8 -*-
"""数据源列表接口测试(示例: 正向 + 边界场景)。

对应规范: docs/standards/task-work-order.md 测试要求
- 每个功能至少 1 个正向测试 + 2 个边界测试
- 测试文件命名: test_模块名_功能点.py
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app


@pytest.fixture()
def authed_client(db):
    """带假用户的 TestClient: 覆盖 get_current_user 返回一个伪用户。"""
    from app.models.user import RoleEnum, User

    fake_user = User(id=1, username="tester", role=RoleEnum.AGENT.value, is_active=True)

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


def test_list_data_sources_ok(authed_client):
    """正向: 认证用户可获取数据源列表, 返回统一 {code, data, message} 结构。"""
    resp = authed_client.get("/api/v1/data-sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    assert isinstance(body["data"], list)


def test_list_data_sources_include_placeholder(authed_client):
    """边界: include_placeholder=true 时返回包含占位源; 默认应排除。"""
    # 默认不包含 placeholder
    resp_default = authed_client.get("/api/v1/data-sources")
    assert resp_default.status_code == 200
    # 显式包含 placeholder
    resp_all = authed_client.get("/api/v1/data-sources", params={"include_placeholder": "true"})
    assert resp_all.status_code == 200
    # include 时列表应不少于默认列表(placeholder 只会增加不会减少)
    assert len(resp_all.json()["data"]) >= len(resp_default.json()["data"])


def test_list_data_sources_unauthorized(client):
    """边界: 未认证访问应返回 401(FastAPI 默认 {detail} 格式)。"""
    resp = client.get("/api/v1/data-sources")
    assert resp.status_code == 401
    body = resp.json()
    # get_current_user 抛 HTTPException(401), FastAPI 返回 {"detail": "..."}
    assert "detail" in body
