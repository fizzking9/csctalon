# -*- coding: utf-8 -*-
"""AI 报告语义匹配测试 (工单 #135 批次 B2)。

覆盖 semantic_match 的:
- 默认阈值 0.6(架构文档 §4.3)
- 空候选 → hit_report=None
- confidence≥0.6 → 命中
- confidence<0.6 → 不命中
- LLM 异常 → hit_report=None(走 L3 兜底)
- LLM 输出 ```json 包裹 → 能解析
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai_semantic_matcher import _DEFAULT_THRESHOLD, semantic_match


def test_default_threshold():
    """默认阈值 0.6(架构文档 §4.3)。"""
    assert _DEFAULT_THRESHOLD == 0.6


@pytest.mark.asyncio
async def test_empty_candidates():
    """空候选 → hit_report=None(架构文档 §4.2)。"""
    result = await semantic_match(db=None, question="退款", candidates=[])
    assert result["hit_report"] is None
    assert result["confidence"] == 0.0
    assert result["all_confidences"] == []


def _mock_cand(rid, ans):
    r = MagicMock()
    r.id = rid
    r.answer_md = ans
    r.question = f"问题{rid}"
    return r


def _mk_llm_result(text: str):
    """构造 LLMResult-like mock(继承 str 行为)。"""
    m = MagicMock()
    m.__str__ = lambda self: text
    return m


@pytest.mark.asyncio
async def test_semantic_match_hit():
    """confidence≥0.6 → 命中(架构文档 §4.3)。"""
    cands = [_mock_cand(1, "退款 5000"), _mock_cand(2, "接待 100")]
    mock_result = _mk_llm_result(json.dumps([
        {"index": 1, "confidence": 0.9, "reason": "问句问退款, 报告1是退款"},
        {"index": 2, "confidence": 0.2, "reason": "报告2是接待"},
    ]))
    with patch("app.services.ai_semantic_matcher.llm_client.chat_completion",
               AsyncMock(return_value=mock_result)):
        out = await semantic_match(db=None, question="退款情况", candidates=cands)
    assert out["hit_report"] is cands[0]
    assert out["confidence"] == 0.9
    assert len(out["all_confidences"]) == 2


@pytest.mark.asyncio
async def test_semantic_match_no_hit_below_threshold():
    """confidence<0.6 → 不命中(架构文档 §4.3)。"""
    cands = [_mock_cand(1, "接待 100")]
    mock_result = _mk_llm_result(json.dumps([
        {"index": 1, "confidence": 0.3, "reason": "弱相关"},
    ]))
    with patch("app.services.ai_semantic_matcher.llm_client.chat_completion",
               AsyncMock(return_value=mock_result)):
        out = await semantic_match(db=None, question="投诉", candidates=cands)
    assert out["hit_report"] is None
    assert out["confidence"] == 0.3


@pytest.mark.asyncio
async def test_semantic_match_llm_failure_returns_none():
    """LLM 异常 → hit_report=None(架构文档 §4.2 失败兜底, 走 L3)。"""
    cands = [_mock_cand(1, "退款 5000")]
    with patch("app.services.ai_semantic_matcher.llm_client.chat_completion",
               AsyncMock(side_effect=Exception("LLM timeout"))):
        out = await semantic_match(db=None, question="退款", candidates=cands)
    assert out["hit_report"] is None
    assert out["confidence"] == 0.0
    assert out["all_confidences"] == []


@pytest.mark.asyncio
async def test_semantic_match_json_with_codeblock():
    """LLM 输出 ```json 包裹 → 能解析(架构文档 §4.2 兼容)。"""
    cands = [_mock_cand(1, "退款 5000")]
    mock_result = _mk_llm_result(
        '```json\n[{"index":1,"confidence":0.8,"reason":"ok"}]\n```'
    )
    with patch("app.services.ai_semantic_matcher.llm_client.chat_completion",
               AsyncMock(return_value=mock_result)):
        out = await semantic_match(db=None, question="退款", candidates=cands)
    assert out["hit_report"] is cands[0]
    assert out["confidence"] == 0.8


@pytest.mark.asyncio
async def test_semantic_match_invalid_json_returns_none():
    """LLM 输出非 JSON → hit_report=None(走 L3 兜底, 不抛异常)。"""
    cands = [_mock_cand(1, "退款 5000")]
    mock_result = _mk_llm_result("这不是 JSON 数组")
    with patch("app.services.ai_semantic_matcher.llm_client.chat_completion",
               AsyncMock(return_value=mock_result)):
        out = await semantic_match(db=None, question="退款", candidates=cands)
    assert out["hit_report"] is None
    assert out["confidence"] == 0.0
    assert out["all_confidences"] == []


@pytest.mark.asyncio
async def test_semantic_match_takes_top5_only():
    """候选取前 5 条(架构文档 §4.2 防输入过长)。"""
    cands = [_mock_cand(i, f"内容{i}") for i in range(1, 11)]  # 10 条
    mock_result = _mk_llm_result(json.dumps([
        {"index": i, "confidence": 0.1 * i, "reason": "x"} for i in range(1, 6)
    ]))
    captured = {}

    async def fake_chat(msgs, **_kwargs):
        captured["input"] = msgs[1]["content"]
        return mock_result

    with patch("app.services.ai_semantic_matcher.llm_client.chat_completion",
               side_effect=fake_chat):
        out = await semantic_match(db=None, question="问", candidates=cands)
    # 输入只含前 5 条 candidate
    assert "report_id=1" in captured["input"]
    assert "report_id=5" in captured["input"]
    assert "report_id=6" not in captured["input"]
    assert "report_id=10" not in captured["input"]
    # 命中第 5 条(confidence=0.5<0.6 → 不命中; 但 all_confidences 含 5 条)
    assert out["hit_report"] is None  # 0.5<0.6
    assert len(out["all_confidences"]) == 5
