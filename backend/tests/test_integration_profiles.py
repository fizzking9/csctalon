# -*- coding: utf-8 -*-
"""接入中心 - 配置包模板 + AI 模型配置测试 (#44, A-20260910-002 LOCKED v1.0)

覆盖:
- 配置包模板 CRUD + code 唯一冲突
- 渲染: 白名单变量替换 / 非白名单原样保留 / unresolved 提示 / target_type 枚举校验
- 恢复默认(reset) / 自定义模板不支持恢复
- seed 不覆盖用户改动(审计项 C-P2-2)
- 🔒 安全回归: AI 模型配置响应**绝不含 api_key 明文字段**
- prompt-preview 只读(无写方法)
- 权限: 非 admin → 403
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import integration_profile_service as svc


def _uniq(prefix: str) -> str:
    """生成跨运行唯一的 code。

    必要性: conftest 的 `db` fixture 靠 rollback 做隔离, 但服务层内部 `db.commit()`
    会绕过外层事务真正落库(test_integration_profiles 等直调服务的用例因此会残留)。
    固定 code 会在第二次运行时撞唯一约束/逻辑冲突 → 用唯一后缀规避。
    """
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
    """非 admin(普通客服): 用于权限断言。"""
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


# ─────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────
def test_seed_creates_three_profiles(db):
    """正向: seed 灌入 3 个默认场景。"""
    svc.seed_profiles(db)
    codes = {p["code"] for p in svc.list_profiles(db)}
    assert {"workbuddy-complaint-push", "workbuddy-manual-refund-push",
            "workbuddy-daily-brief-pull"} <= codes


def test_seed_does_not_overwrite_user_edit(db):
    """边界(C-P2-2): 重复 seed 不覆盖用户改动。

    用**唯一 code 的新行**验证, 不动 3 条种子行 —— 种子行是跨用例共享状态,
    改它会造成其他用例假失败。
    """
    code = _uniq("seed-keep")
    from app.models.integration_profile import IntegrationProfile
    db.add(IntegrationProfile(
        code=code, name="seed 测试", scene="push",
        content_md="seed 初始内容", variables=[], enabled=True, sort_order=0,
    ))
    db.commit()

    # 模拟用户改动
    row = svc.get_by_code(db, code)
    row.content_md = "用户自定义内容-不要被覆盖"
    db.commit()

    # 第二次 seed: 不应触碰既有行(此处 code 不在 seed 列表, 天然跳过)
    svc.seed_profiles(db)
    db.refresh(row)
    assert row.content_md == "用户自定义内容-不要被覆盖"

    db.delete(row)
    db.commit()


def test_profile_crud_via_http(admin_client, db):
    """正向: 新建 → 列表 → 更新 → 删除。"""
    code = _uniq("test-scene")
    payload = {
        "code": code,
        "name": "测试场景",
        "scene": "push",
        "content_md": "## 任务\n推送到 {{base_url}}",
        "variables": [{"key": "base_url", "label": "地址", "required": False}],
    }
    r = admin_client.post("/api/v1/integrations/profiles", json=payload)
    assert r.status_code == 200
    pid = r.json()["data"]["id"]

    lst = admin_client.get("/api/v1/integrations/profiles")
    assert any(p["code"] == code for p in lst.json()["data"])

    r2 = admin_client.put(f"/api/v1/integrations/profiles/{pid}", json={"name": "改名后"})
    assert r2.status_code == 200 and r2.json()["data"]["name"] == "改名后"

    assert admin_client.delete(f"/api/v1/integrations/profiles/{pid}").status_code == 200
    assert admin_client.delete(f"/api/v1/integrations/profiles/{pid}").status_code == 404


def test_profile_duplicate_code_400(admin_client):
    """边界: code 重复 → 400。"""
    code = _uniq("dup-code")
    payload = {"code": code, "name": "x", "scene": "push", "content_md": "x"}
    assert admin_client.post("/api/v1/integrations/profiles", json=payload).status_code == 200
    r = admin_client.post("/api/v1/integrations/profiles", json=payload)
    assert r.status_code == 400 and "已存在" in r.json()["detail"]
    # 自洁: 删除本次创建行(服务层 commit 绕过 fixture 回滚)
    pid = next(p["id"] for p in admin_client.get("/api/v1/integrations/profiles").json()["data"]
               if p["code"] == code)
    admin_client.delete(f"/api/v1/integrations/profiles/{pid}")


def test_profile_invalid_scene_400(admin_client):
    """边界: 非法 scene → 400。"""
    r = admin_client.post("/api/v1/integrations/profiles", json={
        "code": _uniq("bad-scene"), "name": "x", "scene": "unknown", "content_md": "x",
    })
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────
# 渲染(纯函数)
# ─────────────────────────────────────────────────────────────────────
def test_render_substitutes_whitelist_vars(db):
    """正向: 白名单变量被替换。"""
    _, row = _make_profile(db, "t-render", "地址 {{base_url}} 文档 {{doc_url}}")
    out = svc.render_profile(row, {"doc_url": "https://doc/x"}, base_url="https://sys")
    assert "https://sys" in out["markdown"]
    assert "https://doc/x" in out["markdown"]
    assert "{{doc_url}}" not in out["markdown"]


def test_render_keeps_unknown_vars(db):
    """边界: 非白名单 {{xxx}} 原样保留(便于模板里写文档示例)。"""
    _, row = _make_profile(db, "t-unknown", "已知 {{base_url}} 未知 {{not_allowed}}")
    out = svc.render_profile(row, {}, base_url="https://sys")
    assert "{{not_allowed}}" in out["markdown"]


def test_render_ignores_non_whitelist_values(db):
    """边界: 传入非白名单键 → 被忽略并在 ignored_keys 回报(不静默)。"""
    _, row = _make_profile(db, "t-ignore", "文本 {{base_url}}")
    out = svc.render_profile(row, {"evil_key": "x", "base_url": "https://s"})
    assert "evil_key" in out["ignored_keys"]


def test_render_reports_unresolved(db):
    """边界: 必填变量未给值 → 出现在 unresolved。"""
    _, row = _make_profile(
        db, "t-unres", "{{doc_url}}",
        variables=[{"key": "doc_url", "label": "文档地址", "required": True}],
    )
    out = svc.render_profile(row, {}, base_url="https://s")
    assert "doc_url" in out["unresolved"]


def test_render_rejects_invalid_target_type(db):
    """边界: target_type 非枚举 → 400(防止渲染出非法值)。"""
    _, row = _make_profile(db, "t-tt", "{{target_type}}")
    with pytest.raises(Exception) as exc:
        svc.render_profile(row, {"target_type": "evil"}, base_url="https://s")
    assert exc.value.status_code == 400


def test_render_does_not_eval_format_placeholders(db):
    """🔒 安全: 模板中的 `{}` 不被当格式串求值(用字面替换而非 str.format)。"""
    _, row = _make_profile(db, "t-brace", 'JSON 示例 {"code":0,"data":{}} 与 {{base_url}}')
    out = svc.render_profile(row, {}, base_url="https://sys")
    assert '{"code":0,"data":{}}' in out["markdown"]  # 原样保留, 未抛错
    assert "https://sys" in out["markdown"]


def test_render_via_http(admin_client):
    """HTTP: 渲染端点返回 markdown + unresolved。"""
    code = _uniq("http-render")
    admin_client.post("/api/v1/integrations/profiles", json={
        "code": code, "name": "x", "scene": "push",
        "content_md": "文档: {{doc_url}} / 接口: {{base_url}}",
        "variables": [{"key": "doc_url", "label": "文档", "required": True}],
    })
    pid = next(p["id"] for p in admin_client.get("/api/v1/integrations/profiles").json()["data"]
               if p["code"] == code)
    r = admin_client.post(f"/api/v1/integrations/profiles/{pid}/render",
                          json={"doc_url": "https://doc/abc", "base_url": "https://sys"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert "https://doc/abc" in data["markdown"]
    assert "https://sys" in data["markdown"]
    admin_client.delete(f"/api/v1/integrations/profiles/{pid}")


def test_render_404_for_unknown_profile(admin_client):
    """边界: 未知模板 → 404。"""
    assert admin_client.post("/api/v1/integrations/profiles/999999/render", json={}).status_code == 404


# ─────────────────────────────────────────────────────────────────────
# 恢复默认
# ─────────────────────────────────────────────────────────────────────
def test_reset_restores_seed_content(admin_client, db):
    """正向: 误改模板后可一键还原默认内容。

    断言基准取**内置 seed 常量**而非"改前的内容" —— 后者受前序测试留下的
    脏状态影响, 会造成假失败。
    """
    svc.seed_profiles(db)
    row = svc.get_by_code(db, "workbuddy-complaint-push")
    expected = next(s["content_md"] for s in svc._SEED_PROFILES
                    if s["code"] == "workbuddy-complaint-push")

    row.content_md = "坏掉了"
    db.commit()

    r = admin_client.post(f"/api/v1/integrations/profiles/{row.id}/reset")
    assert r.status_code == 200
    assert r.json()["data"]["content_md"] == expected
    assert "坏掉了" not in r.json()["data"]["content_md"]


def test_reset_rejects_custom_profile(admin_client):
    """边界: 自定义模板无内置默认 → 400(不静默返回空内容)。"""
    code = _uniq("custom-x")
    admin_client.post("/api/v1/integrations/profiles", json={
        "code": code, "name": "x", "scene": "push", "content_md": "自建",
    })
    pid = next(p["id"] for p in admin_client.get("/api/v1/integrations/profiles").json()["data"]
               if p["code"] == code)
    assert admin_client.post(f"/api/v1/integrations/profiles/{pid}/reset").status_code == 400
    admin_client.delete(f"/api/v1/integrations/profiles/{pid}")


# ─────────────────────────────────────────────────────────────────────
# 🔒 安全回归: AI 模型配置脱敏
# ─────────────────────────────────────────────────────────────────────
def test_ai_model_config_no_api_key_leak(admin_client, monkeypatch):
    """🔒 安全回归: 模型配置响应**绝不含 api_key 明文**(A-20260904-003 LOCKED)。

    明文 key 一旦可经 API 取回, 等于把 LOCKED 架构的「禁入库/禁前端展示」降级为失效。
    """
    secret = "sk-super-secret-key-should-never-leak-1234567890"
    monkeypatch.setattr(settings, "LLM_API_KEY", secret)
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "LLM_MODEL", "deepseek-v4-flash")

    r = admin_client.get("/api/v1/ai/model-config")
    assert r.status_code == 200
    assert secret not in r.text, "模型配置接口泄漏了 API Key 明文!"
    data = r.json()["data"]
    # 只允许「是否配置 + 长度」, 不允许任何形式的内容片段
    assert data["api_key_configured"] is True
    assert data["api_key_length"] == len(secret)
    assert "api_key" not in data, "响应不得出现 api_key 字段"


def test_ai_model_config_reports_unconfigured(admin_client, monkeypatch):
    """边界: 未配置 key → configured=false, length=0。"""
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    r = admin_client.get("/api/v1/ai/model-config")
    assert r.json()["data"]["api_key_configured"] is False
    assert r.json()["data"]["api_key_length"] == 0


def test_ai_prompt_layers_structure(admin_client):
    """正向: 四层结构返回, 且只有 focus 可编辑。"""
    r = admin_client.get("/api/v1/ai/prompt-layers")
    assert r.status_code == 200
    layers = {x["key"]: x for x in r.json()["data"]["layers"]}
    assert set(layers) == {"model", "system", "glossary", "focus"}
    assert layers["system"]["editable"] is False
    assert layers["glossary"]["editable"] is False
    assert layers["focus"]["editable"] is True


def test_ai_prompt_preview_readonly(admin_client):
    """正向+边界: 预览返回只读标记; 非法 layer → 400。"""
    r = admin_client.get("/api/v1/ai/prompt-preview?layer=system")
    assert r.status_code == 200
    assert r.json()["data"]["readonly"] is True
    assert r.json()["data"]["content"]

    assert admin_client.get("/api/v1/ai/prompt-preview?layer=bogus").status_code == 400


def test_ai_prompt_preview_has_no_write_method(admin_client):
    """🔒 安全: 提示词预览端点不提供写方法(铁律/口径卡是代码常量, 不可经 API 改)。"""
    assert admin_client.post("/api/v1/ai/prompt-preview?layer=system", json={}).status_code == 405
    assert admin_client.put("/api/v1/ai/prompt-preview?layer=system", json={}).status_code == 405


# ─────────────────────────────────────────────────────────────────────
# 权限
# ─────────────────────────────────────────────────────────────────────
def test_agent_cannot_access_profiles(agent_client):
    """边界: 非 admin 调模板管理 → 403。"""
    assert agent_client.get("/api/v1/integrations/profiles").status_code == 403


def test_agent_cannot_access_ai_model_config(agent_client):
    """边界: 非 admin 调模型配置 → 403。"""
    assert agent_client.get("/api/v1/ai/model-config").status_code == 403
    assert agent_client.get("/api/v1/ai/prompt-layers").status_code == 403


# ─────────────────────────────────────────────────────────────────────
# helper
# ─────────────────────────────────────────────────────────────────────
def _make_profile(db, code: str, content_md: str, variables=None):
    """直接建模板行(绕过 HTTP, 供纯函数测试用)。

    code 会自动加唯一后缀; 返回 (None, row) 保持与既有调用形式兼容。
    """
    from app.models.integration_profile import IntegrationProfile
    row = IntegrationProfile(
        code=_uniq(code), name=code, scene="push",
        content_md=content_md, variables=variables or [], enabled=True, sort_order=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return None, row
