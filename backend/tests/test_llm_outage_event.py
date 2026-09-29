"""LLM 故障治理测试(2026-09-29 事故修复)

事故: talon 库重建后 csctalon 的 key 失配, 401/403 被当作 permanent 立即抛出,
旧逻辑既不计数也不发事件 —— 14 小时 100 次失败, 管理面零感知。

本测试锁定:
  1. 403 → 计入连续失败 + 发 error 级系统事件(action=auth_failed)
  2. 全链 transient 失败 → 发 chain_unavailable 事件
  3. 同类事件 30 分钟去重(持续故障不刷屏)
  4. 400(非鉴权 permanent)→ 不发鉴权事件
"""
import time

import httpx
import pytest

from app.services import llm_provider_service, system_event_service
from app.services.llm_client import LLMError, _call_multi_provider, _record_llm_outage


# ── 轻量 fake, 不依赖真实 DB ─────────────────────────────────────────

class _FakeQuery:
    """模拟 SystemEvent 去重查询的链式 filter().first()。"""

    def __init__(self, existing):
        self._existing = existing

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._existing


class _FakeDB:
    def __init__(self, existing_event=None):
        self.existing_event = existing_event

    def query(self, *cols):
        return _FakeQuery(self.existing_event)

    def rollback(self):
        pass


def _provider(**overrides):
    base = {
        "id": 1, "code": "talon-1", "model": "qwen2.5:7b",
        "base_url": "http://talon.test/v1", "api_key": "talon-x",
        "is_cloud": False, "thinking": "disabled",
        "timeout_s": 30.0, "retry_max": 0, "fail_threshold": 3,
    }
    base.update(overrides)
    return base


async def _run_chain(monkeypatch, status_codes, *, db=None, providers=None):
    """跑一遍多 provider 链; status_codes 按 provider 顺序返回。"""
    events = []

    def fake_record(db, category, action, title, summary=None,
                    status="info", level=None, **kw):
        events.append({"category": category, "action": action,
                       "title": title, "level": level})

    monkeypatch.setattr(system_event_service, "record_system_event", fake_record)
    monkeypatch.setattr(llm_provider_service, "record_call_log",
                        lambda *a, **k: None)
    failed = []
    monkeypatch.setattr(
        llm_provider_service, "mark_provider_failed",
        lambda db, pid, *, fail_threshold: failed.append(pid))

    codes = list(status_codes)

    def handler(request: httpx.Request) -> httpx.Response:
        code = codes.pop(0)
        return httpx.Response(code, json={"error": f"http {code}"})

    provs = providers or [_provider()]
    with pytest.raises(LLMError):
        await _call_multi_provider(
            provs, [{"role": "user", "content": "hi"}],
            temperature=0.2, max_tokens=5, override_timeout_s=None,
            transport=httpx.MockTransport(handler),
            db=db or _FakeDB(), source="unit-test",
            t0=time.perf_counter(), prompt_chars=2,
        )
    return events, failed


class TestAuthFailureGovernance:
    """401/403 鉴权失败治理。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [401, 403])
    async def test_auth_failure_emits_event_and_counts(self, monkeypatch, code):
        events, failed = await _run_chain(monkeypatch, [code])
        assert failed == [1], "鉴权失败必须计入连续失败(达阈值摘链路)"
        assert len(events) == 1
        ev = events[0]
        assert ev["category"] == "llm"
        assert ev["action"] == "auth_failed"
        assert ev["level"] == "error"

    @pytest.mark.asyncio
    async def test_non_auth_4xx_emits_nothing(self, monkeypatch):
        """400 等非鉴权 permanent: 保持旧行为, 不发鉴权事件、不计数。"""
        events, failed = await _run_chain(monkeypatch, [400])
        assert events == []
        assert failed == []

    @pytest.mark.asyncio
    async def test_all_transient_failures_emits_chain_event(self, monkeypatch):
        """两个 provider 都 503(transient 降级到底)→ chain_unavailable 事件。"""
        provs = [_provider(id=1, code="p1"), _provider(id=2, code="p2")]
        events, failed = await _run_chain(monkeypatch, [503, 503], providers=provs)
        assert failed == [1, 2], "transient 失败逐个计数"
        assert [e["action"] for e in events] == ["chain_unavailable"]
        assert events[0]["level"] == "error"

    @pytest.mark.asyncio
    async def test_auth_then_transient_only_auth_event(self, monkeypatch):
        """首个 provider 403 立即抛出, 不继续后续 provider(鉴权错全局同源)。"""
        provs = [_provider(id=1, code="p1"), _provider(id=2, code="p2")]
        events, failed = await _run_chain(monkeypatch, [403], providers=provs)
        assert failed == [1]
        assert [e["action"] for e in events] == ["auth_failed"]


class TestOutageEventDedupe:
    """30 分钟去重(直接测 _record_llm_outage)。"""

    def test_first_event_written(self, monkeypatch):
        written = []
        monkeypatch.setattr(
            system_event_service, "record_system_event",
            lambda db, **kw: written.append(kw))
        _record_llm_outage(_FakeDB(existing_event=None),
                           action="auth_failed", title="t", summary="s")
        assert len(written) == 1

    def test_duplicate_within_window_skipped(self, monkeypatch):
        written = []
        monkeypatch.setattr(
            system_event_service, "record_system_event",
            lambda db, **kw: written.append(kw))
        # 去重查询命中 30 分钟内的同类 failed 事件 → 跳过
        _record_llm_outage(_FakeDB(existing_event=object()),
                           action="auth_failed", title="t", summary="s")
        assert written == []

    def test_db_none_is_noop(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            system_event_service, "record_system_event",
            lambda *a, **k: called.append(1))
        _record_llm_outage(None, action="auth_failed", title="t", summary="s")
        assert called == []

    def test_record_failure_never_raises(self, monkeypatch):
        """事件写入自身报错也不能影响调用主流程。"""
        def boom(*a, **k):
            raise RuntimeError("event store down")
        monkeypatch.setattr(system_event_service, "record_system_event", boom)
        _record_llm_outage(_FakeDB(existing_event=None),
                           action="auth_failed", title="t", summary="s")
