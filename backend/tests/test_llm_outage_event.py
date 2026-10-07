"""LLM 故障治理 + 系统事件降噪/去重/保留期测试(2026-09-29)

事故: talon 库重建后 csctalon 的 key 失配, 401/403 被当作 permanent 立即抛出,
旧逻辑既不计数也不发事件 —— 14 小时 100 次失败, 管理面零感知。
治理二期: info 事件不进未读(降噪)、通用 dedup_minutes 去重、error 外发 webhook、
日志保留期每日清理。

本测试锁定:
  LLM 链: 403 → 计入连续失败 + error 事件; 全链失败 → chain_unavailable; 400 不误报
  事件服务: info 默认已读不亮角标 / error 进未读 / 去重窗口内只写一条 / 去重仍外发一次
  保留期: system_events 超 90 天被清、近期保留
"""
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services import llm_provider_service, system_event_service
from app.services.llm_client import LLMError, _call_multi_provider

# ── 轻量 fake, 不依赖真实 DB ─────────────────────────────────────────

class _FakeQuery:
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
    events = []

    def fake_record(db, category, action, title, summary=None,
                    status="info", level=None, **kw):
        events.append({"category": category, "action": action,
                       "title": title, "level": level, "status": status})

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


class TestEventNoiseAndDedup:
    """事件服务降噪/去重/外发(commit=False 走事务内可见, 不污染测试库)。"""

    def test_info_default_read_no_badge(self, db, monkeypatch):
        """info 级默认 is_read=True —— 入库留痕但不亮角标。"""
        monkeypatch.setattr(
            "app.services.notification_channel.dispatch_system_alert_async",
            lambda **kw: pytest.fail("info 不应触发外发"))
        ev = system_event_service.record_system_event(
            db, category="sync", action="unit_noise_info",
            title="单测-成功事件不进未读", status="success", commit=False)
        assert ev.is_read is True and ev.read_at is not None

    def test_error_default_unread_and_dispatches(self, db, monkeypatch):
        """error 级进未读 + 触发一次外发调度。"""
        sent = []
        monkeypatch.setattr(
            "app.services.notification_channel.dispatch_system_alert_async",
            lambda **kw: sent.append(kw))
        ev = system_event_service.record_system_event(
            db, category="llm", action="unit_noise_err",
            title="单测-error进未读", status="failed", commit=False)
        assert ev.is_read is False and ev.read_at is None
        assert len(sent) == 1 and sent[0]["category"] == "llm"

    def test_dedup_second_call_skipped_and_no_dispatch(self, db, monkeypatch):
        """去重窗口内同 category+action+level+title 只写一条, 且只外发一次。"""
        sent = []
        monkeypatch.setattr(
            "app.services.notification_channel.dispatch_system_alert_async",
            lambda **kw: sent.append(kw))
        kw = dict(category="ai", action="unit_dedup",
                  title="单测-去重持续性故障", status="failed", commit=False)
        first = system_event_service.record_system_event(db, dedup_minutes=30, **kw)
        db.flush()  # fixture 为 autoflush=False; 真实运行 commit=True 首次调用即落库可见
        second = system_event_service.record_system_event(db, dedup_minutes=30, **kw)
        assert first is not None and second is None
        assert len(sent) == 1, "去重命中后不得再次外发"

    def test_dedup_different_title_both_kept(self, db, monkeypatch):
        """不同 title(如不同模板/不同源)不去重, 各留各的。"""
        monkeypatch.setattr(
            "app.services.notification_channel.dispatch_system_alert_async",
            lambda **kw: None)
        a = system_event_service.record_system_event(
            db, category="ai", action="unit_dedup2",
            title="单测-去重A", status="failed", commit=False, dedup_minutes=30)
        b = system_event_service.record_system_event(
            db, category="ai", action="unit_dedup2",
            title="单测-去重B", status="failed", commit=False, dedup_minutes=30)
        assert a is not None and b is not None

    def test_badge_override_forces_unread_info(self, db, monkeypatch):
        monkeypatch.setattr(
            "app.services.notification_channel.dispatch_system_alert_async",
            lambda **kw: None)
        ev = system_event_service.record_system_event(
            db, category="auth", action="unit_badge",
            title="单测-info强制角标", status="info", commit=False, badge=True)
        assert ev.is_read is False


class TestLogRetention:
    """保留期清理(真实 pg 测试库, 带唯一标记, 用后清理)。"""

    def test_system_events_90d_purge(self, _engine):
        from sqlalchemy.orm import Session

        from app.models.system_event import SystemEvent

        marker = f"单测-retention-{datetime.now(timezone.utc).timestamp()}"
        old_title = marker + "-old"
        new_title = marker + "-new"
        session = Session(_engine)
        try:
            now = datetime.now(timezone.utc)
            session.add_all([
                SystemEvent(category="schedule", action="unit_retention_old",
                            title=old_title, status="info", level="info",
                            is_read=True,
                            created_at=now - timedelta(days=100)),
                SystemEvent(category="schedule", action="unit_retention_new",
                            title=new_title, status="info", level="info",
                            is_read=True, created_at=now),
            ])
            session.commit()

            from app.services.log_retention_service import run_retention_purge

            out = run_retention_purge(session, retention_days={"system_events": 90})
            assert out["total"] >= 1
            # 清理自身可能产生的"日志保留期清理"事件会污染断言, 用精确标记查
            old_left = session.query(SystemEvent).filter(
                SystemEvent.title == old_title).count()
            new_left = session.query(SystemEvent).filter(
                SystemEvent.title == new_title).count()
            assert old_left == 0, "100 天前的事件应被清掉"
            assert new_left == 1, "近期事件必须保留"
        finally:
            # 用后清理(标记行 + 本用例产生的清理事件, 精确条件精删)
            session.query(SystemEvent).filter(
                SystemEvent.title.like(marker + "%")).delete(synchronize_session=False)
            session.query(SystemEvent).filter(
                SystemEvent.action == "log_retention_purge",
                SystemEvent.title == "日志保留期清理").delete(
                synchronize_session=False)
            session.commit()
            session.close()
