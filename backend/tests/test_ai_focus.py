# -*- coding: utf-8 -*-
"""AI 助手关注点/分析视角配置测试 (A-20260904-003 v1.2 / 工单 #26)。

覆盖:
- ai_focus_service.focus_for_question: 问句关键词路由 scope(退款→refund+global 等)/未命中→global/空库→""
- ai_feed.build_messages: 有配置时注入【本期关注点】段; 无配置时与改造前一致(回归)
- /api/v1/ai/focus-configs CRUD + 权限(401/403)
- 缓存: 增删改后 focus_for_question 立即生效(失效)
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.ai_focus_config import AiFocusConfig
from app.models.user import RoleEnum, User
from app.services import ai_focus_service


@pytest.fixture(autouse=True)
def _clear_focus_cache():
    """每个用例前清进程缓存, 防 service 模块级缓存跨用例污染(测试事务回滚但缓存残留)。"""
    ai_focus_service.invalidate_cache()
    yield
    ai_focus_service.invalidate_cache()


@pytest.fixture()
def admin_client(db, monkeypatch):
    """带 admin 用户的 TestClient(管理端点用; 不依赖 LLM_ENABLED)。"""
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


def _mk(db, code="focus-t", scope="refund", title="退款关注", content="退款要看四路与涉诉",
        sort_order=10, enabled=True):
    r = AiFocusConfig(code=code, scope_key=scope, title=title, content=content,
                      sort_order=sort_order, enabled=enabled, created_by="test")
    db.add(r)
    db.flush()
    return r


# ---- focus_for_question 路由 ----
def test_focus_routes_refund_plus_global(db):
    """问句含'退款' → 注入 refund + global。"""
    _mk(db, "focus-refund", "refund", "退款", "退款要看四路与涉诉", 10)
    _mk(db, "focus-global", "global", "通用", "先结论后数据", 1)
    text = ai_focus_service.focus_for_question(db, "今日退款情况如何")
    assert "退款要看四路与涉诉" in text
    assert "先结论后数据" in text


def test_focus_routes_complaint(db):
    """问句含'投诉' → 注入 complaint(+global 若存在)。"""
    _mk(db, "focus-complaint", "complaint", "投诉", "投诉要看积压", 10)
    text = ai_focus_service.focus_for_question(db, "昨日投诉积压如何")
    assert "投诉要看积压" in text
    assert "退款要看四路" not in text  # 不注入其它域


def test_focus_no_hit_global_only(db):
    """未命中任何域且只有 refund → 空(不注入); 有 global → 只带 global。"""
    _mk(db, "focus-refund", "refund", "退款", "退款内容", 10)
    ai_focus_service.invalidate_cache()
    assert ai_focus_service.focus_for_question(db, "随便聊聊") == ""
    _mk(db, "focus-global", "global", "通用", "通用内容", 1)
    ai_focus_service.invalidate_cache()  # 模拟管理端新增后失效缓存
    text = ai_focus_service.focus_for_question(db, "随便聊聊")
    assert "通用内容" in text
    assert "退款内容" not in text


def test_focus_disabled_not_injected(db):
    """enabled=false 不注入。"""
    _mk(db, "focus-disabled", "global", "停用", "不应出现", 1, enabled=False)
    assert ai_focus_service.focus_for_question(db, "任何问题") == ""


def test_focus_cache_invalidation(db):
    """写操作后 invalidate → 下次读取用新内容。"""
    _mk(db, "focus-global", "global", "通用", "旧内容", 1)
    text1 = ai_focus_service.focus_for_question(db, "任何问题")
    assert "旧内容" in text1
    # 改内容 + 失效
    row = db.query(AiFocusConfig).filter_by(code="focus-global").one()
    row.content = "新内容"
    db.flush()
    ai_focus_service.invalidate_cache()
    text2 = ai_focus_service.focus_for_question(db, "任何问题")
    assert "新内容" in text2
    assert "旧内容" not in text2
    # 清理(防污染其它用例)
    ai_focus_service.invalidate_cache()


# ---- build_messages 注入 ----
def test_build_messages_injects_focus(db):
    """有配置时 user content 含【本期关注点】段(位于口径与数据之间)。"""
    from app.services import ai_feed
    _mk(db, "focus-global", "global", "通用", "先结论后数据", 1)
    ai_focus_service.invalidate_cache()
    msgs = ai_feed.build_messages(db, period="daily", date="2026-07-15", question="今日退款如何?")
    user_content = msgs[1]["content"]
    assert "【本期关注点" in user_content
    assert "先结论后数据" in user_content
    # 位置: 在【业务口径】与【给定数据】之间
    assert user_content.index("【本期关注点") > user_content.index("【业务口径")
    assert user_content.index("【给定数据") > user_content.index("【本期关注点")
    ai_focus_service.invalidate_cache()


def test_build_messages_no_focus_regression(db):
    """无任何配置 → 输出与改造前一致(不含 focus 段)。"""
    from app.services import ai_feed
    ai_focus_service.invalidate_cache()
    msgs = ai_feed.build_messages(db, period="daily", date="2026-07-15", question="今日退款如何?")
    user_content = msgs[1]["content"]
    assert "【本期关注点" not in user_content
    assert "【业务口径" in user_content and "【给定数据" in user_content


# ---- CRUD + 权限(HTTP) ----
def test_focus_crud_http(admin_client, db):
    """HTTP: 增→列表→改→删。"""
    # 建
    r = admin_client.post("/api/v1/ai/focus-configs", json={
        "code": "focus-http", "scope_key": "refund", "title": "HTTP退款",
        "content": "HTTP内容", "sort_order": 5,
    })
    assert r.status_code == 200
    data = r.json()["data"]
    cid = next(x["id"] for x in data if x["code"] == "focus-http")
    assert cid
    # 改
    u = admin_client.patch(f"/api/v1/ai/focus-configs/{cid}", json={"content": "改后内容"})
    assert u.status_code == 200
    row = next(x for x in u.json()["data"] if x["id"] == cid)
    assert row["content"] == "改后内容"
    # 删
    d = admin_client.delete(f"/api/v1/ai/focus-configs/{cid}")
    assert d.status_code == 200
    lst = admin_client.get("/api/v1/ai/focus-configs").json()["data"]
    assert not any(x["id"] == cid for x in lst)


def test_focus_crud_duplicate_code(db, admin_client):
    """code 重复 → 400。"""
    _mk(db, "focus-dup", "refund", "退款", "已有", 10)
    r = admin_client.post("/api/v1/ai/focus-configs", json={
        "code": "focus-dup", "scope_key": "refund", "title": "重复", "content": "x",
    })
    assert r.status_code == 400


def test_focus_crud_requires_admin(db):
    """未登录 → 401。"""
    from fastapi.testclient import TestClient as TC

    # 无依赖覆盖的裸 client → 应 401(未带 token)
    from app.api.deps import get_db as _get_db
    from app.core.database import SessionLocal

    def _override_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[_get_db] = _override_db
    try:
        with TC(app) as c:
            resp = c.get("/api/v1/ai/focus-configs")
            assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides.clear()
