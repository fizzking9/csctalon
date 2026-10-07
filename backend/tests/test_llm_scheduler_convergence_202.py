# -*- coding: utf-8 -*-
"""工单 #202: 同步路径 5 处直调 LLM 收口 llm_scheduler 的测试。

验收对应:
- 5 处调用全部经 llm_scheduler 统一入口(spy acquire 断言被调用)
- 优先级标注正确: extract/semantic/time_parse=L2(实时问答), canary_smoke=L1(admin
  手动触发), governance D7=L0(凌晨后台体检) —— 实时问答 L2 不被误降为 L0
- 并发场景下在飞 LLM 数符合调度器承诺: 同门闸(extract+semantic 同档位链首)
  并发时 chat_completion 在飞峰值 = 1
- 不改变各模型节点路由: chat_completion 收到的 provider_code 链与收口前一致

mock 策略与 test_ai_extract_service.py 一致: patch llm_client.chat_completion,
不触真实 LLM。spy acquire 用 wraps 真函数, 保证队列语义真实生效(并发互斥是真门闸)。
"""

import asyncio
import datetime as _dt
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import llm_scheduler


class _FakeResult(str):
    """LLM 返回值桩: 真实 chat_completion 返回 str 子类(带 usage 等), 本桩模拟其 str 行为。"""


def _mk_llm_result(text: str):
    """LLMResult-like 桩(必须是 str 子类: time_parser 会直接 .strip() 而非 str())。"""
    return _FakeResult(text)


def _spy_scheduler(calls: list):
    """包一层真 acquire: 记录 (name, level, provider_code), 队列语义不变。"""
    real_acquire = llm_scheduler.acquire

    async def spy(level=2, *, name=None, provider_code=None):
        calls.append({"name": name, "level": level, "provider_code": provider_code})
        return await real_acquire(level, name=name, provider_code=provider_code)

    return spy


# ── 1) extract_section: 经调度器 + L2(实时问答) ─────────────────────────
@pytest.mark.asyncio
async def test_extract_section_uses_scheduler_l2():
    calls = []
    r = MagicMock()
    r.id = 1
    r.answer_md = "## 退款分析\n退款总额 5000 元。"
    with patch("app.services.llm_scheduler.acquire", side_effect=_spy_scheduler(calls)), \
         patch("app.services.ai_extract_service.llm_client.chat_completion",
               AsyncMock(return_value=_mk_llm_result("退款 5000 元。"))):
        out = await extract_section_stub(r)
    assert out["need_clarify"] is False
    assert len(calls) == 1
    assert calls[0]["level"] == 2            # 实时问答档, 不降级
    assert calls[0]["name"] == "ai_extract"


async def extract_section_stub(report):
    from app.services.ai_extract_service import extract_section
    return await extract_section(db=None, question="退款情况", report=report)


# ── 2) semantic_match: 经调度器 + L2 ────────────────────────────────────
@pytest.mark.asyncio
async def test_semantic_match_uses_scheduler_l2():
    from app.services.ai_semantic_matcher import semantic_match

    calls = []
    cand = MagicMock()
    cand.id = 7
    cand.question = "今日退款"
    cand.answer_md = "退款 5000 元。"
    payload = json.dumps([{"index": 1, "confidence": 0.9, "reason": "相关"}])
    with patch("app.services.llm_scheduler.acquire", side_effect=_spy_scheduler(calls)), \
         patch("app.services.ai_semantic_matcher.llm_client.chat_completion",
               AsyncMock(return_value=_mk_llm_result(payload))):
        out = await semantic_match(db=None, question="今日退款情况",
                                   candidates=[cand])
    assert out["confidence"] >= 0.6
    assert len(calls) == 1
    assert calls[0]["level"] == 2
    assert calls[0]["name"] == "ai_semantic"


# ── 3) llm_parse_time_intent: 经调度器 + L2 ─────────────────────────────
@pytest.mark.asyncio
async def test_time_parser_uses_scheduler_l2(monkeypatch):
    from app.core.config import settings
    from app.services.ai_time_parser import TimeIntent, llm_parse_time_intent

    calls = []
    # 隔离环境: 不连库(get_active_providers 打桩)、SessionLocal 打桩,
    # 强制走"有 provider"分支进入调度器(否则无 provider + 无 .env 会跳过)
    monkeypatch.setattr(
        "app.services.llm_provider_service.get_active_providers",
        lambda _db: [{"code": "fake-a"}])
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://fake", raising=False)
    monkeypatch.setattr(settings, "LLM_MODEL", "fake-model", raising=False)
    payload = json.dumps({"start": "2026-10-01", "end": "2026-10-07",
                          "confidence": 0.9, "matched": "最近一周"})
    with patch("app.services.llm_scheduler.acquire", side_effect=_spy_scheduler(calls)), \
         patch("app.services.llm_client.chat_completion",
               AsyncMock(return_value=_mk_llm_result(payload))):
        out = await llm_parse_time_intent("最近一周退款如何")
    assert isinstance(out, TimeIntent)
    assert out.is_effective
    assert len(calls) == 1
    assert calls[0]["level"] == 2
    assert calls[0]["name"] == "llm_time_parse"
    assert calls[0]["provider_code"] is None  # 未指定 provider → default 门闸


# ── 4) canary_smoke: 经调度器 + L1(admin 手动触发) ──────────────────────
def test_canary_smoke_uses_scheduler_l1():
    from app.services.prompt_config_service import canary_smoke

    calls = []
    with patch("app.services.llm_scheduler.acquire", side_effect=_spy_scheduler(calls)), \
         patch("app.services.llm_client.chat_completion",
               AsyncMock(return_value=_mk_llm_result("今日退款总额 1000 元, 共 5 笔。"))):
        out = canary_smoke(db=None, code="gen_system", content="你是经营问答助手。")
    assert out["ok"] is True
    assert len(calls) == 1
    assert calls[0]["level"] == 1
    assert calls[0]["name"] == "prompt_canary_smoke"


# ── 5) governance D7: 经调度器 + L0(凌晨后台体检) ───────────────────────
def test_governance_d7_uses_scheduler_l0(db):
    from app.models.refund_wide import RefundWide
    from app.services.governance_detector import scan_semantic_anomaly

    # refund_wide 造 1 行(必填: refund_no/matched_at; device_sn=None 避 FK)
    db.add(RefundWide(refund_no="D7TEST-001",
                      refund_reason="网络故障",
                      refund_amount=100.0,
                      service_item="设备无法开机",
                      service_category="维修",
                      has_external_complaint=False,
                      external_complaint_count=0,
                      matched_at=_dt.datetime.now()))
    db.flush()

    calls = []
    with patch("app.services.llm_scheduler.acquire", side_effect=_spy_scheduler(calls)), \
         patch("app.services.llm_client.chat_completion",
               AsyncMock(return_value=_mk_llm_result(json.dumps([])))):
        findings = scan_semantic_anomaly(db)
    assert findings == []
    assert len(calls) == 1
    assert calls[0]["level"] == 0            # 后台体检最高优先, 可抢占实时问答
    assert calls[0]["name"] == "governance_d7"


# ── 6) 并发互斥: 同门闸下在飞 LLM 峰值 = 1(调度器承诺) ────────────────────
@pytest.mark.asyncio
async def test_concurrent_same_gate_single_flight():
    """extract + semantic 并发(同档位链首 → 同门闸) → chat_completion 在飞峰值 1。

    收口前两处直调可同时飞(互斥失效); 收口后经真门闸排队, 任一时刻仅 1 个在飞。
    patch resolve_tier 统一链首, 避免依赖环境 DB 配置。
    """
    from app.services.ai_extract_service import extract_section
    from app.services.ai_semantic_matcher import semantic_match

    inflight = 0
    peak = 0
    payload = json.dumps([{"index": 1, "confidence": 0.9, "reason": "相关"}])

    async def fake_chat(msgs, **kwargs):
        # 真异步桩(patch new=, 非 AsyncMock.side_effect —— 后者不会自动 await
        # async side_effect, 会把 coroutine 对象当结果返回)
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.05)   # 放大并发窗口
        inflight -= 1
        if kwargs.get("source") == "ai_semantic":
            return _mk_llm_result(payload)
        return _mk_llm_result("ok")

    r = MagicMock()
    r.id = 1
    r.answer_md = "## 退款分析\n退款 5000 元。"
    cand = MagicMock()
    cand.id = 7
    cand.question = "今日退款"
    cand.answer_md = "退款 5000 元。"

    with patch("app.services.llm_tiers.resolve_tier", return_value=["prov-x"]), \
         patch("app.services.ai_extract_service.llm_client.chat_completion", new=fake_chat), \
         patch("app.services.ai_semantic_matcher.llm_client.chat_completion", new=fake_chat):
        await asyncio.gather(
            extract_section(db=None, question="退款情况", report=r),
            semantic_match(db=None, question="退款情况", candidates=[cand]),
        )

    assert peak == 1, f"同门闸并发在飞峰值应为 1, 实测 {peak}"


# ── 7) 门闸释放: 调用结束后该 provider 门闸无在飞/无排队(防 handle 泄漏) ──
@pytest.mark.asyncio
async def test_gate_released_after_call():
    from app.services.ai_extract_service import extract_section

    r = MagicMock()
    r.id = 1
    r.answer_md = "## 退款分析\n退款 5000 元。"
    with patch("app.services.llm_tiers.resolve_tier", return_value=["prov-release"]), \
         patch("app.services.ai_extract_service.llm_client.chat_completion",
               AsyncMock(return_value=_mk_llm_result("ok"))):
        await extract_section(db=None, question="退款情况", report=r)
    snap = llm_scheduler.queue_status()
    gate = snap["providers"].get("prov-release", {})
    assert gate.get("running") == 0
    assert gate.get("queue_length") == 0
