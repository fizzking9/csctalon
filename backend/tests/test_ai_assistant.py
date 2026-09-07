# -*- coding: utf-8 -*-
"""AI 分析助手一期测试 (A-20260904-003 LOCKED #12)。

覆盖:
- ai_glossary: 问句关键词路由口径卡子集 / 未命中全量兜底
- ai_feed.build_messages: Prompt 三层(System/口径卡/Data+Question)结构
- llm_client: OpenAI 兼容端点 200 / 429 重试后成功 / 400 permanent 不重试 / 缺 base_url 503
- ai_task_manager: 状态流转 queued→running→success/failed
- /api/v1/ai: LLM_ENABLED=false→503; analyze 提交返回 task_id; config 需鉴权
"""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import llm_client
from app.services.ai_glossary import glossary_for_question
from app.services.ai_task_manager import ai_task_manager

_TZ = None


@pytest.fixture()
def manager_client(db, monkeypatch):
    """带 manager 用户的 TestClient(admin ∈ MANAGER_ROLES)。"""
    monkeypatch.setattr(settings, "LLM_ENABLED", True)
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


# ── ai_glossary ──────────────────────────────────────────────
def test_glossary_routes_refund():
    """问句含'退款'/'涉诉' → 注入退款+涉诉+金额口径卡子集。"""
    ctx = glossary_for_question("今日退款和涉诉情况怎么样")
    assert "退款金额口径=四路经营合计" in ctx
    assert "涉诉率=涉诉金额" in ctx
    assert "A线上直退" in ctx


def test_glossary_routes_complaint():
    """问句含'投诉'/'积压' → 注入三源+登记处理口径卡。"""
    ctx = glossary_for_question("昨日投诉积压情况")
    assert "投诉三源分类" in ctx
    assert "登记" in ctx and "处理" in ctx


def test_glossary_fallback_all():
    """未命中任何域关键词 → 全量口径卡兜底。"""
    ctx = glossary_for_question("随便聊聊这个系统")
    # 全量 = 所有口径卡都应在
    assert "投诉三源分类" in ctx
    assert "退款金额口径" in ctx
    assert "涉诉率" in ctx
    assert "接待量=客服处理记录数" in ctx


# ── ai_feed.build_messages ───────────────────────────────────
def test_build_messages_structure(db):
    """Prompt 三层结构: system 含铁律; user 含口径卡 + feed JSON + 问句。"""
    from app.services import ai_feed
    msgs = ai_feed.build_messages(db, period="daily", date="2026-07-15", question="今日退款如何?")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "给定数据" in msgs[0]["content"]
    user_content = msgs[1]["content"]
    assert "【业务口径(必须遵守)】" in user_content
    assert "【给定数据" in user_content
    assert "今日退款如何?" in user_content
    assert "今日退款如何?" in msgs[1]["content"]


def test_build_feed_matches_brief(db):
    """feed = external._build_brief 同结构(period/date/generated_at/reception/refund/complaint/_spec)。"""
    from app.services import ai_feed
    feed = ai_feed._build_feed(db, period="daily", date="2026-07-15")
    assert feed["period"] == "daily"
    assert feed["date"] == "2026-07-15"
    for key in ("reception", "refund", "complaint", "_spec"):
        assert key in feed


def test_build_feed_range_start_end(db):
    """v1.1: _build_feed 支持 start/end 任意区间(透传 external._build_brief range)。"""
    from app.services import ai_feed
    feed = ai_feed._build_feed(db, period="daily", date=None, start="2026-07-01", end="2026-07-07")
    assert feed["period"] == "range"
    assert feed["range"]["span_days"] == 7
    assert feed["date_start"].startswith("2026-07-01T00:00:00")
    for key in ("reception", "refund", "complaint", "_spec"):
        assert key in feed


def test_build_messages_range_window_label(db):
    """v1.1: build_messages 传 start/end → 窗口说明标注 range。"""
    from app.services import ai_feed
    msgs = ai_feed.build_messages(db, period="weekly", date=None, question="这个区间经营如何?",
                                  start="2026-07-01", end="2026-07-07")
    user_content = msgs[1]["content"]
    assert "period=range(start=2026-07-01, end=2026-07-07)" in user_content


# ── llm_client ───────────────────────────────────────────────
def _mk_response(status: int, content: str | None = None) -> httpx.Response:
    if status == 200:
        return httpx.Response(200, json={"choices": [{"message": {"content": content or "ok"}}]})
    return httpx.Response(status, text=content or "err")


@pytest.mark.asyncio
async def test_llm_client_success(monkeypatch):
    """200 → 返回 content。"""
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://mock.deepseek.com")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "deepseek-v4-flash")
    transport = httpx.MockTransport(lambda req: _mk_response(200, "分析结果"))
    out = await llm_client.chat_completion([{"role": "user", "content": "hi"}], transport=transport)
    assert out == "分析结果"


@pytest.mark.asyncio
async def test_llm_client_retry_then_success(monkeypatch):
    """429 两次后 200 → 最终成功(transient 重试)。"""
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://mock.deepseek.com")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "m")
    monkeypatch.setattr(settings, "LLM_RETRY_MAX", 2)
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_S", 0.0)  # 避免测试 sleep
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] <= 2:
            return _mk_response(429, "rate limited")
        return _mk_response(200, "成功")

    transport = httpx.MockTransport(handler)
    out = await llm_client.chat_completion([{"role": "user", "content": "hi"}], transport=transport)
    assert out == "成功"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_llm_client_permanent_400(monkeypatch):
    """400(鉴权等) → permanent 不重试, 抛 LLMError。"""
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://mock.deepseek.com")
    monkeypatch.setattr(settings, "LLM_MODEL", "m")
    monkeypatch.setattr(settings, "LLM_RETRY_MAX", 3)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _mk_response(400, "bad request")

    transport = httpx.MockTransport(handler)
    with pytest.raises(llm_client.LLMError):
        await llm_client.chat_completion([{"role": "user", "content": "hi"}], transport=transport)
    assert calls["n"] == 1  # 不重试


def test_llm_client_missing_base_url(monkeypatch):
    """未配置 base_url → LLMError(status=503)。"""
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    with pytest.raises(llm_client.LLMError) as e:
        llm_client._endpoint()
    assert e.value.status_code == 503


# ── ai_task_manager ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_ai_task_manager_success():
    """submit → running → success(result_md 写回)。"""
    mgr = ai_task_manager

    async def runner(task):
        await asyncio.sleep(0.01)
        task.result_md = "# 分析\n要点"

    task_id = mgr.submit("今日退款", "daily", None, runner)
    assert task_id.startswith("ai_")
    # 等待后台完成
    for _ in range(50):
        t = mgr.get(task_id)
        if t and t.status == "success":
            break
        await asyncio.sleep(0.01)
    t = mgr.get(task_id)
    assert t is not None and t.status == "success"
    assert t.result_md == "# 分析\n要点"


@pytest.mark.asyncio
async def test_ai_task_manager_failed():
    """runner 抛异常 → failed + error。"""
    mgr = ai_task_manager

    async def runner(task):
        raise ValueError("boom")

    task_id = mgr.submit("x", "daily", None, runner)
    for _ in range(50):
        t = mgr.get(task_id)
        if t and t.status == "failed":
            break
        await asyncio.sleep(0.01)
    t = mgr.get(task_id)
    assert t is not None and t.status == "failed"
    assert "boom" in (t.error or "")


# ── /api/v1/ai ───────────────────────────────────────────────
def test_ai_config_requires_auth():
    """未登录 → 401(证明路由注册)。"""
    c = TestClient(app)
    assert c.get("/api/v1/ai/config").status_code == 401


def test_ai_analyze_disabled_503(manager_client):
    """LLM_ENABLED=false → analyze 503。"""
    # manager_client 已置 true; 单独关掉
    old = settings.LLM_ENABLED
    settings.LLM_ENABLED = False
    try:
        r = manager_client.post("/api/v1/ai/analyze", json={"question": "今日退款"})
        assert r.status_code == 503
    finally:
        settings.LLM_ENABLED = old


def test_ai_config_structure(manager_client):
    """config → enabled/periods/notice。"""
    r = manager_client.get("/api/v1/ai/config")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["enabled"] is True
    assert "periods" in data
    assert data["periods"][0]["period"] == "daily"
