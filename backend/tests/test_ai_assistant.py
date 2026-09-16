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


def test_glossary_fallback_minimal():
    """#115: 未命中任何域关键词 → 极简全域卡兜底, 含四类 _spec 缺失判据且大幅瘦身。"""
    from app.services.ai_glossary import glossary_all
    ctx = glossary_for_question("随便聊聊这个系统")
    # 四类必备口径都在: 三源 / 四路 / 接待 / yoy 对比期语义
    assert "投诉三源" in ctx
    assert "四路经营合计" in ctx
    assert "接待量=客服处理记录数" in ctx
    assert "对比期语义" in ctx and "yoy_value" in ctx
    # 已被 feed._spec 覆盖的口径(涉诉率定义 / lead 双体系)不在 glossary 重复
    assert "涉诉率=涉诉金额" not in ctx
    assert "行政组长" not in ctx
    # 体积约束: ≤350 字符(实测 327, 含英文术语), 较全量 1287 字符降 70%+
    assert len(ctx) <= 350
    assert len(ctx) < len(glossary_all()) * 0.3


def test_glossary_hit_route_unchanged():
    """#115: 命中域关键词仍走原子集(退款卡原文), 不受极简兜底影响。"""
    ctx = glossary_for_question("今日退款如何")
    assert "退款金额口径=四路经营合计" in ctx  # 原 refund_routes 卡原文
    assert "投诉三源: 内部=商品/服务投诉" not in ctx  # 极简兜底卡不应出现


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


# ── #66: 自然语言窗口贯通(端到端到 Prompt) ──────────────────────
def test_build_messages_injects_nl_window_label(db):
    """B1: window_label 传入后必须出现在 Prompt 里(让模型知道窗口怎么定的)。"""
    from app.services import ai_feed
    msgs = ai_feed.build_messages(
        db, period="daily", date=None, question="昨天的运营数据",
        start="2026-07-14", end="2026-07-14",
        window_label="2026-07-14(昨天)", window_source="nl",
    )
    content = msgs[1]["content"]
    assert "窗口判定=2026-07-14(昨天)" in content
    assert "自动识别" in content


def test_build_messages_window_label_absent_keeps_old_output(db):
    """回归: 不传 window_label → 输出与改造前一致(不含"窗口判定")。"""
    from app.services import ai_feed
    msgs = ai_feed.build_messages(db, period="daily", date="2026-07-15", question="今日退款如何?")
    assert "窗口判定" not in msgs[1]["content"]


def test_system_prompt_requires_window_echo_and_yoy_semantics(db):
    """D1: System 铁律须要求回显窗口, 并说明 yoy 不是独立明细。"""
    from app.services import ai_feed
    msgs = ai_feed.build_messages(db, period="daily", date="2026-07-15", question="今日退款如何?")
    sys_content = msgs[0]["content"]
    assert "写明本次统计窗口" in sys_content
    assert "不是另一天的独立明细" in sys_content


def test_glossary_timezone_has_compare_semantics():
    """D1: timezone 口径卡须含 yoy 对比期语义说明。"""
    from app.services.ai_glossary import glossary_for_question
    ctx = glossary_for_question("昨天的运营数据")
    assert "对比期语义" in ctx
    assert "yoy_value" in ctx


def test_glossary_compare_word_injects_timezone():
    """D1: 含对比类词(如"对比")的问句即使未命中域, 也必须注入 timezone 卡。"""
    from app.services.ai_glossary import glossary_for_question
    ctx = glossary_for_question("这两天数据对比")
    assert "对比期语义" in ctx


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


def test_payload_thinking_disabled_adds_chat_template_kwargs():
    """#111/#113: thinking=disabled 必须同时传 chat_template_kwargs + think:false,
    否则 ollama+Qwen3 系列默认 thinking=on 会烧光 max_tokens 致 content 空。
    - chat_template_kwargs: ollama OpenAI /v1 端点识别
    - think:False: ollama 原生 /api/chat 字段, talon-api 透传, 长 prompt 下最稳
    DeepSeek/OpenAI 官方端点会忽略未知字段(无害)。
    """
    body = llm_client._payload(
        [{"role": "user", "content": "hi"}],
        model="qwen3.8:27b",
        max_tokens=200,
        thinking="disabled",
    )
    assert body["thinking"] == {"type": "disabled"}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["think"] is False


def test_payload_thinking_low_no_chat_template_kwargs():
    """thinking=low/high 时不加 chat_template_kwargs/think (保持 reasoning_effort 控制)。"""
    body = llm_client._payload(
        [{"role": "user", "content": "hi"}],
        model="deepseek-v4-flash",
        max_tokens=200,
        thinking="low",
    )
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"
    assert "chat_template_kwargs" not in body
    assert "think" not in body


# ── #116: LLMResult + _extract_content 取 usage ───────────────────
def test_llm_result_str_compat_and_usage():
    """LLMResult 继承 str, 旧调用零侵入: == 比对字符串; .usage 拿 dict。"""
    r = llm_client.LLMResult("分析结果", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
    assert r == "分析结果"  # 继承 str 的 ==
    assert isinstance(r, str)
    assert len(r) == 4
    assert r.usage == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


def test_llm_result_no_usage():
    """LLMResult 不传 usage → usage=None(规则层秒回/服务端不返 usage)。"""
    r = llm_client.LLMResult("ok")
    assert r == "ok"
    assert r.usage is None


def test_extract_content_returns_usage():
    """#116: _extract_content 从 OpenAI 兼容响应取 (content, usage) 二元组。"""
    content, usage = llm_client._extract_content({
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })
    assert content == "hello"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_extract_content_no_usage_field():
    """#116: 响应不含 usage → usage=None(ollama 旧版/服务端不返)。"""
    content, usage = llm_client._extract_content({
        "choices": [{"message": {"content": "hello"}}],
    })
    assert content == "hello"
    assert usage is None


@pytest.mark.asyncio
async def test_chat_completion_timeout_s_override_short(monkeypatch):
    """#120: timeout_s=0.05 触发超时(前台调用方可以传短 timeout)。

    用 .env 单源模式 + 真实外网 URL + 短 timeout_s, 期望 LLMError with retryable=True。
    """
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://www.deepseek.com")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "m")
    monkeypatch.setattr(settings, "LLM_RETRY_MAX", 0)
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_S", 0.0)
    with pytest.raises(llm_client.LLMError) as exc_info:
        await llm_client.chat_completion(
            [{"role": "user", "content": "hi"}],
            max_tokens=10, timeout_s=0.05, db=None,
        )
    # 超时属于 transient, 标 retryable=True
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_chat_completion_timeout_s_param_accepted(monkeypatch):
    """#120: chat_completion 接受 timeout_s 参数不报错(签名兼容性回归)。

    用 MockTransport 立即返回 200, 验证 timeout_s=180 也能正常完成。
    """
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://mock.local")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "m")
    monkeypatch.setattr(settings, "LLM_RETRY_MAX", 0)
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_S", 0.0)

    def handler(req):  # noqa: ANN001
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        })
    transport = httpx.MockTransport(handler)
    # timeout_s=180(后台场景) 也应正常返回
    r = await llm_client.chat_completion(
        [{"role": "user", "content": "hi"}],
        max_tokens=10, timeout_s=180.0, transport=transport, db=None,
    )
    assert r == "ok"
    assert r.usage == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}


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
        llm_client._endpoint("")  # _endpoint(base_url) 需显式传参
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
    """runner 抛异常 → failed + error(非空)。

    #66 起: 非 LLMError 的异常不再透出原文(防内部细节外泄), 只保留异常类型 +
    通用提示; 完整堆栈由 logger.exception 记到服务端日志。
    """
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
    assert (t.error or "").strip(), "error 不得为空"
    assert "ValueError" in t.error


@pytest.mark.asyncio
async def test_ai_task_manager_internal_error_not_leaked():
    """#66 WARNING 收敛: 内部异常原文(SQL/表名等)不得出现在对外 error 里。"""
    mgr = ai_task_manager

    async def runner(task):
        raise RuntimeError('relation "service_tickets" does not exist')

    task_id = mgr.submit("x", "daily", None, runner)
    for _ in range(50):
        t = mgr.get(task_id)
        if t and t.status == "failed":
            break
        await asyncio.sleep(0.01)
    err = mgr.get(task_id).error
    assert "service_tickets" not in err, "内部表名不得外泄"
    assert "does not exist" not in err


def test_humanize_error_does_not_leak_via_status_code_attribute():
    """#66 复审: 判定必须用 isinstance(LLMError), 不能用 hasattr(e,'status_code')。

    `fastapi.HTTPException` / `httpx.HTTPStatusError` 同样带 status_code,
    鸭子类型会把它们误判为"可透传", 从而泄漏内部异常原文。
    """
    import httpx
    from fastapi import HTTPException

    from app.services.ai_task_manager import _humanize_error

    leaked = 'relation "service_tickets" does not exist'
    for e in (
        HTTPException(400, leaked),
        httpx.HTTPStatusError("boom", request=httpx.Request("GET", "http://x"),
                              response=httpx.Response(500)),
        RuntimeError(leaked),
    ):
        out = _humanize_error(e)
        assert "service_tickets" not in out, f"{type(e).__name__} 泄漏了内部表名"
        assert "does not exist" not in out


@pytest.mark.asyncio
async def test_humanize_error_llm_error_passthrough():
    """LLMError 是业务侧构造的面向用户文案 → 应透传原文(用户需知道额度/鉴权问题)。"""
    from app.services.ai_task_manager import _humanize_error
    from app.services.llm_client import LLMError

    assert "402" in _humanize_error(LLMError("LLM 调用失败(status=402)", status_code=402))


# ── #66 C1/C2: 失败可读 + 窗口随任务回显 ────────────────────────
@pytest.mark.asyncio
async def test_ai_task_manager_failed_never_empty_error():
    """C1: 空消息异常 → error 仍非空(生产曾出现 error=None → 前端只能显示"未知错误")。"""
    mgr = ai_task_manager

    async def runner(task):
        raise RuntimeError()   # str(RuntimeError()) == ""

    task_id = mgr.submit("x", "daily", None, runner)
    for _ in range(50):
        t = mgr.get(task_id)
        if t and t.status == "failed":
            break
        await asyncio.sleep(0.01)
    t = mgr.get(task_id)
    assert t.status == "failed"
    assert (t.error or "").strip(), "error 不得为空, 否则前端只能显示'未知错误'"
    assert "RuntimeError" in t.error


@pytest.mark.asyncio
async def test_ai_task_manager_timeout_error_is_humanized():
    """C1: TimeoutError → 面向用户的可读提示(而非裸异常名)。"""
    mgr = ai_task_manager

    async def runner(task):
        raise TimeoutError()

    task_id = mgr.submit("x", "daily", None, runner)
    for _ in range(50):
        t = mgr.get(task_id)
        if t and t.status == "failed":
            break
        await asyncio.sleep(0.01)
    t = mgr.get(task_id)
    assert "超时" in (t.error or "")


@pytest.mark.asyncio
async def test_ai_task_to_public_dict_failed_without_error_has_fallback():
    """C1: failed 且 error 为空时, 对外字典必须兜底非空文案。"""
    from app.services.ai_task_manager import AiTask
    t = AiTask(task_id="ai_x", question="q", period="daily", date=None)
    t.status = "failed"
    t.error = None
    assert t.to_public_dict()["error"], "对外输出不得出现空 error"


@pytest.mark.asyncio
async def test_ai_task_window_meta_persisted():
    """C2: 窗口来源/说明随任务持久化, 供前端回显。"""
    mgr = ai_task_manager

    async def runner(task):
        task.result_md = "ok"

    task_id = mgr.submit("昨天的运营数据", "daily", None, runner,
                         start="2026-09-10", end="2026-09-10",
                         window_source="nl", window_label="2026-09-10(昨天)")
    for _ in range(50):
        t = mgr.get(task_id)
        if t and t.status == "success":
            break
        await asyncio.sleep(0.01)
    d = mgr.get(task_id).to_public_dict()
    assert d["window_source"] == "nl"
    assert d["window_label"] == "2026-09-10(昨天)"
    assert d["start"] == "2026-09-10"


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


# ── #98 层2: 问题路由 + 精确取数 ──────────────────────────────
def test_keyword_to_scope_refund():
    """问句含'退款'→ scope=refund(与 feed 顶层段名对齐)。"""
    from app.services.ai_focus_service import keyword_to_scope
    assert keyword_to_scope("今日退款情况如何?") == "refund"
    assert keyword_to_scope("昨天提现多少笔") == "refund"
    assert keyword_to_scope("涉诉率高不高") == "refund"


def test_keyword_to_scope_complaint():
    """问句含'投诉'/'积压'→ scope=complaint。"""
    from app.services.ai_focus_service import keyword_to_scope
    assert keyword_to_scope("今日投诉积压多少") == "complaint"
    assert keyword_to_scope("商户号投诉登记数") == "complaint"


def test_keyword_to_scope_reception():
    """问句含'接待'/'会话'→ scope=reception。"""
    from app.services.ai_focus_service import keyword_to_scope
    assert keyword_to_scope("今日接待量") == "reception"
    assert keyword_to_scope("本周会话多少") == "reception"


def test_keyword_to_scope_miss_returns_none():
    """问句未命中任何域关键词 → None(回退全量 feed, 兼容)。"""
    from app.services.ai_focus_service import keyword_to_scope
    assert keyword_to_scope("经营情况分析一下") is None
    assert keyword_to_scope("给点建议") is None
    assert keyword_to_scope("") is None


def test_select_feed_sections_refund_only():
    """scope=refund → 只保留 refund 业务段 + 恒量段, 丢 reception/complaint。"""
    from app.services.ai_feed import _select_feed_sections, _CONSTANT_FEED_KEYS
    feed = {
        "period": "daily", "date": "2026-09-15",
        "date_start": "2026-09-15T00:00:00+08:00",
        "date_end": "2026-09-15T23:59:59+08:00",
        "generated_at": "2026-09-15T09:00:00+08:00",
        "_spec": {"金额单位": "元"},
        "reception": {"total": 100},
        "refund": {"total_amount": 5000, "total_count": 10, "a_route": {"litigation_ratio": {"value": 0.2}}},
        "complaint": {"registered": 5},
    }
    out = _select_feed_sections(feed, "refund")
    # refund 段保留(含涉诉率子段, 不漏)
    assert "refund" in out
    assert out["refund"]["a_route"]["litigation_ratio"]["value"] == 0.2
    # 未命中的业务段被切掉
    assert "reception" not in out
    assert "complaint" not in out
    # 恒量段全保留
    for k in _CONSTANT_FEED_KEYS:
        if k in feed:
            assert k in out


def test_select_feed_sections_none_returns_full():
    """scope=None → 原样返回全量(兼容, 改造前行为)。"""
    from app.services.ai_feed import _select_feed_sections
    feed = {"period": "daily", "reception": {"x": 1}, "refund": {"y": 2}, "complaint": {"z": 3}}
    out = _select_feed_sections(feed, None)
    assert out is feed  # 同一对象, 零拷贝


def test_build_messages_scope_refund_drops_complaint(db):
    """端到端: 问'今日退款如何?' → user content 不含 complaint 顶层段(dict)。

    验证层2 段选取在 build_messages 链路生效。refund 段保留(涉诉率不漏)。
    断言用 `'"complaint": {'`(冒号空格大括号)区分: 顶层 complaint 段是 dict
    (`"complaint": {`); _spec 里 complaint 是 str 值(`"complaint": "`).
    单元测试 test_select_feed_sections_refund_only 已用 dict 级别断言
    充分覆盖"切掉 complaint 顶层段", 此处只验证链路串通。
    """
    from app.services import ai_feed
    msgs = ai_feed.build_messages(db, period="daily", date="2026-07-15", question="今日退款如何?")
    user_content = msgs[1]["content"]
    # 顶层 complaint 段(dict)被切掉; _spec 里的 complaint(str 值)不算
    assert '"complaint": {' not in user_content
    assert '"reception": {' not in user_content
    # refund 顶层段(dict)保留
    assert '"refund": {' in user_content


# ── #98 层3: 规则层秒回 ──────────────────────────────────────
def test_rule_engine_refund_count_hit(db):
    """问'今日退款多少笔' → 命中 refund 规则, 返回含退款笔数/金额/涉诉率的 Markdown。"""
    from app.services.ai_rule_engine import try_rule_answer
    eff = {"period": "daily", "date": "2026-07-15",
            "start": None, "end": None, "window_label": "2026-07-15(指定日期)"}
    md = try_rule_answer(db, "今日退款多少笔", eff)
    assert md is not None
    assert "退款笔数" in md
    assert "退款金额" in md
    assert "涉诉率" in md
    assert "2026-07-15" in md  # 窗口回显


def test_rule_engine_analysis_word_falls_back(db):
    """问句含推理词'分析' → 返回 None 回退 LLM(规则层不接推理类)。"""
    from app.services.ai_rule_engine import try_rule_answer
    eff = {"period": "daily", "date": "2026-07-15",
            "start": None, "end": None, "window_label": "今日"}
    assert try_rule_answer(db, "分析下今日退款的策略", eff) is None
    assert try_rule_answer(db, "今日退款情况如何改善", eff) is None


def test_rule_engine_miss_scope_falls_back(db):
    """问句未命中 scope 关键词 → 返回 None 回退 LLM。"""
    from app.services.ai_rule_engine import try_rule_answer
    eff = {"period": "daily", "date": "2026-07-15",
            "start": None, "end": None, "window_label": "今日"}
    assert try_rule_answer(db, "今日经营怎么样", eff) is None
    assert try_rule_answer(db, "给点建议", eff) is None


def test_ai_analyze_rule_short_circuits_refund(manager_client):
    """端到端: POST /ai/analyze 问'今日退款多少笔' → status=success + task_id=None(不入队)。

    规则层秒回, 不走 LLM。返回结构与 LLM 路径一致(前端零改兼容)。
    """
    r = manager_client.post("/api/v1/ai/analyze",
                            json={"question": "今日退款多少笔", "period": "daily"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "success"
    assert data.get("task_id") is None  # 规则层不入队
    assert "退款笔数" in data.get("result_md", "")
    assert "window_label" in data  # 窗口回显


def test_ai_analyze_analysis_question_still_uses_llm(manager_client):
    """端到端: 问'分析下今日经营' → 不命中规则层, 走 LLM 路径(入队 task_id 非空)。

    验证规则层未误伤推理类问句。LLM 路径可能 success(快)/running(慢)/failed(配置缺),
    都接受; 关键是**入队了**(task_id 非空), 不是规则层秒回(task_id=None)。
    测试环境常缺 LLM_BASE_URL → failed, 但只要 task_id 非空就证明走了 LLM 路径。
    """
    r = manager_client.post("/api/v1/ai/analyze",
                            json={"question": "分析下今日经营给点建议", "period": "daily"})
    assert r.status_code == 200
    data = r.json()["data"]
    # 走 LLM 路径: 入队 → task_id 非空(规则层秒回时 task_id=None)
    assert data.get("task_id") is not None, "推理类问句不该命中规则层(应入队 LLM)"
    assert data["status"] in ("success", "running", "failed")
