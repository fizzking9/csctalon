# -*- coding: utf-8 -*-
"""B2-4 (#239 / R-P1-3): LLM 超时口径统一 —— 单次(score) vs 端到端(wall-clock)。

锁定:
- _call_single_provider 在 wall_deadline 下: 预算已过期则零请求; 退避/重试受预算裁剪;
  预算内成功不受影响。
- _call_multi_provider: 预算耗尽停止降级(不请求后续 provider); 各 provider 审计
  duration_ms 是「自身 wall-clock」, 不含前序 provider 的降级累计耗时。

全部用 httpx.MockTransport, 不发真实网络; provider 治理函数 monkeypatch 掉, 不依赖 DB。
"""

import asyncio
import time

import httpx
import pytest

from app.services import llm_provider_service
from app.services.llm_client import LLMError, _call_multi_provider, _call_single_provider


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


# ════════════════════════════════════════════════════════════════════════
# _call_single_provider: wall deadline 行为
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_expired_deadline_sends_no_request():
    """deadline 已过 → 连第一次请求都不发, 直接 DeadlineExceeded。"""
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return _ok(request)

    content, err, status, err_cls = await _call_single_provider(
        url="http://127.0.0.1:1/v1/chat/completions", headers={}, body={},
        timeout_s=30.0, retry_max=3,
        transport=httpx.MockTransport(handler),
        wall_deadline=time.perf_counter() - 1.0,  # 已过期 1s
    )
    assert content is None
    assert err is not None and err.retryable is True
    assert err_cls == "DeadlineExceeded"
    assert hits["n"] == 0, "端到端预算已耗尽时不得再发任何 HTTP 请求"


@pytest.mark.asyncio
async def test_deadline_caps_retry_backoff():
    """503 重试时, 退避被端到端预算裁剪; 预算耗尽不再发起后续 attempt。

    无 deadline 时 _backoff_sleep(0)≈0.8~1.2s 且会继续重试;
    deadline=0.3s 下应只发 1 次请求、总耗时 < 1s 即终止。
    """
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return httpx.Response(503, json={"error": "busy"})

    t0 = time.perf_counter()
    content, err, status, err_cls = await _call_single_provider(
        url="http://127.0.0.1:1/v1/chat/completions", headers={}, body={},
        timeout_s=30.0, retry_max=3,
        transport=httpx.MockTransport(handler),
        wall_deadline=t0 + 0.3,
    )
    elapsed = time.perf_counter() - t0

    assert content is None
    assert status == 503 and err_cls == "http_503"
    assert hits["n"] == 1, "首次 503 后退避应耗尽预算, 不再发起第 2 次 attempt"
    assert elapsed < 1.0, f"端到端应被 0.3s 硬墙限制, 实际 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_success_within_deadline_unaffected():
    """预算宽裕时正常成功(deadline 不影响正常路径)。"""
    content, err, status, err_cls = await _call_single_provider(
        url="http://127.0.0.1:1/v1/chat/completions", headers={}, body={},
        timeout_s=30.0, retry_max=0,
        transport=httpx.MockTransport(_ok),
        wall_deadline=time.perf_counter() + 5.0,
    )
    assert content is not None and status == 200
    assert err is None and err_cls == ""


# ════════════════════════════════════════════════════════════════════════
# _call_multi_provider: 停止降级 + 独立计账
# ════════════════════════════════════════════════════════════════════════
def _provider(port, *, code, retry_max=0):
    return {
        "id": port, "code": code, "model": "qwen-test",
        "base_url": f"http://127.0.0.1:{port}/v1", "api_key": "k",
        "is_cloud": False, "thinking": "disabled",
        "timeout_s": 30.0, "retry_max": retry_max, "fail_threshold": 3,
    }


def _patch_provider_governance(monkeypatch):
    """屏蔽 provider 健康治理, 捕获每次审计日志的 (code, duration_ms)。"""
    logs: list[dict] = []
    monkeypatch.setattr(llm_provider_service, "record_call_log",
                        lambda db, **kw: logs.append(kw))
    monkeypatch.setattr(llm_provider_service, "mark_provider_succeeded",
                        lambda db, pid: None)
    monkeypatch.setattr(llm_provider_service, "mark_provider_failed",
                        lambda db, pid, *, fail_threshold: None)
    return logs


@pytest.mark.asyncio
async def test_each_provider_duration_is_own_wallclock(monkeypatch):
    """p1 耗 ~200ms 失败 → p2 立即成功: p2 审计耗时不得含 p1 的 200ms。"""
    logs = _patch_provider_governance(monkeypatch)
    ports = {"p1": 11431, "p2": 11432}

    async def handler(request: httpx.Request):
        if request.url.port == ports["p1"]:
            await asyncio.sleep(0.2)  # 模拟首个 provider 慢而后失败
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    providers = [_provider(ports["p1"], code="p1"), _provider(ports["p2"], code="p2")]
    content = await _call_multi_provider(
        providers, [{"role": "user", "content": "hi"}],
        temperature=0.2, max_tokens=None, override_timeout_s=30.0,
        transport=httpx.MockTransport(handler),
        db=object(), source="unit-test",
        t0=time.perf_counter(), prompt_chars=2,
        wall_deadline=None,
    )

    assert str(content) == "ok"
    by_code = {row["provider_code"]: row["duration_ms"] for row in logs}
    assert by_code["p1"] >= 150, f"p1 自身耗时应≈200ms, 实得 {by_code['p1']}"
    assert by_code["p2"] < 100, (
        f"p2 计账应为自身 wall-clock(≈0ms), 却含 p1 累计耗时: {by_code['p2']}ms"
    )
    assert by_code["p2"] < by_code["p1"], "旧 bug(复用全局 t0) 会让 p2≈p1 累计耗时"


@pytest.mark.asyncio
async def test_expired_deadline_stops_fallback_without_any_request(monkeypatch):
    """进入降级链前预算已耗尽 → 一个 provider 都不请求, 直接失败。"""
    _patch_provider_governance(monkeypatch)
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return _ok(request)

    providers = [_provider(11441, code="p1"), _provider(11442, code="p2")]
    with pytest.raises(LLMError):
        await _call_multi_provider(
            providers, [{"role": "user", "content": "hi"}],
            temperature=0.2, max_tokens=None, override_timeout_s=30.0,
            transport=httpx.MockTransport(handler),
            db=object(), source="unit-test",
            t0=time.perf_counter(), prompt_chars=2,
            wall_deadline=time.perf_counter() - 1.0,  # 进入前已过期
        )
    assert hits["n"] == 0, "端到端预算耗尽后不得再降级请求任何 provider"
