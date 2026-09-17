# -*- coding: utf-8 -*-
"""AI 报告摘取层测试 (工单 #135 批次 B2)。

覆盖 split_sections / extract_section 的:
- split_sections H2 锚点切分(正常/空/无 H2 兜底)
- _SCOPE_TO_H2 映射与 ai_feed._select_feed_sections 同款
- H2_ANCHORS 5 个锚点完整(防 #134 锚点被误删)
- extract_section 空草稿 → need_clarify
- extract_section LLM 成功 → result_md + confidence=0.7
- extract_section LLM 失败 → need_clarify + clarify_options 含 H2 标题
- extract_section 泛问(无 scope 命中)→ 取全部段落
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai_extract_service import (
    _SCOPE_TO_H2,
    H2_ANCHORS,
    extract_section,
    split_sections,
)


# ── split_sections(同步纯函数) ─────────────────────────────
def test_split_sections_h2_anchors():
    md = """## 接待分析
今日接待 100 人。

## 退款分析
退款总额 5000 元。

## 投诉分析
暂无
"""
    sections = split_sections(md)
    assert "## 接待分析" in sections
    assert "## 退款分析" in sections
    assert "## 投诉分析" in sections
    assert "100" in sections["## 接待分析"]
    assert "5000" in sections["## 退款分析"]
    assert sections["## 投诉分析"] == "暂无"


def test_split_sections_empty():
    assert split_sections("") == {}
    assert split_sections(None) == {}  # type: ignore[arg-type]
    assert split_sections("   \n  \n  ") == {}


def test_split_sections_no_h2_fallback_para():
    """日报无 H2 → 按 \\n\\n 段落分(架构文档 §3.2 兜底)。"""
    md = "段落一内容。\n\n段落二内容。"
    sections = split_sections(md)
    assert len(sections) == 2
    assert "段落一内容" in list(sections.values())[0]
    assert "段落二内容" in list(sections.values())[1]


def test_split_sections_trailing_anchor():
    """草稿末尾有 ## 但无后续内容 → body 为空字符串(不报错)。"""
    md = "## 接待分析\n接待 100\n## 退款分析\n"
    sections = split_sections(md)
    assert "## 退款分析" in sections
    assert sections["## 退款分析"] == ""


# ── 常量一致性(防 #134 锚点漂移) ─────────────────────────
def test_scope_to_h2_mapping():
    """scope → H2 映射(与 ai_feed._select_feed_sections 同款)。"""
    assert _SCOPE_TO_H2["reception"] == "## 接待分析"
    assert _SCOPE_TO_H2["refund"] == "## 退款分析"
    assert _SCOPE_TO_H2["complaint"] == "## 投诉分析"


def test_h2_anchors_complete():
    """5 个 H2 锚点(与 #134 _SYSTEM_PROMPT 第 3 条一致; 防锚点被误删)。"""
    assert H2_ANCHORS == ["## 接待分析", "## 退款分析", "## 投诉分析",
                          "## 趋势对比", "## 结论建议"]


# ── extract_section(async) ─────────────────────────────────
def _mock_report(answer_md):
    r = MagicMock()
    r.id = 1
    r.answer_md = answer_md
    return r


def _mk_llm_result(text: str):
    """构造 LLMResult-like mock(继承 str 行为, 与 llm_client.chat_completion 返回一致)。"""
    m = MagicMock()
    m.__str__ = lambda self: text
    return m


@pytest.mark.asyncio
async def test_extract_section_empty_answer():
    """空草稿 → need_clarify=True(架构文档 §3.2)。"""
    r = _mock_report("")
    out = await extract_section(db=None, question="退款", report=r)
    assert out["need_clarify"] is True
    assert out["result_md"] == ""
    assert out["confidence"] == 0.0
    assert out["clarify_options"] == []


@pytest.mark.asyncio
async def test_extract_section_llm_success():
    """LLM 成功 → result_md + confidence=0.7(架构文档 §3.2)。"""
    md = "## 退款分析\n退款总额 5000 元, 4 笔, 均单价 1250 元。"
    r = _mock_report(md)
    mock_result = _mk_llm_result("退款 5000 元, 4 笔。")
    with patch("app.services.ai_extract_service.llm_client.chat_completion",
               AsyncMock(return_value=mock_result)):
        out = await extract_section(db=None, question="退款情况", report=r)
    assert out["need_clarify"] is False
    assert out["result_md"] == "退款 5000 元, 4 笔。"
    assert out["confidence"] == 0.7
    assert out["clarify_options"] == []


@pytest.mark.asyncio
async def test_extract_section_llm_failure_returns_clarify():
    """LLM 异常 → need_clarify=True + clarify_options 含 H2 标题(架构文档 §3.2 失败兜底)。"""
    md = "## 退款分析\n退款总额 5000 元。\n\n## 接待分析\n接待 100 人。"
    r = _mock_report(md)
    with patch("app.services.ai_extract_service.llm_client.chat_completion",
               AsyncMock(side_effect=Exception("LLM timeout"))):
        out = await extract_section(db=None, question="退款情况", report=r)
    assert out["need_clarify"] is True
    assert out["result_md"] == ""
    assert out["confidence"] == 0.0
    assert "## 退款分析" in out["clarify_options"]
    assert "## 接待分析" in out["clarify_options"]


@pytest.mark.asyncio
async def test_extract_section_generic_question_takes_all():
    """泛问(无 scope 命中)→ 取全部段落喂给 LLM(架构文档 §3.2)。"""
    md = "## 接待分析\n接待 100 人。\n\n## 退款分析\n退款 5000 元。"
    r = _mock_report(md)
    mock_result = _mk_llm_result("总览: 接待 100, 退款 5000。")
    captured = {}

    async def fake_chat(msgs, **_kwargs):
        captured["input"] = msgs[1]["content"]
        return mock_result

    with patch("app.services.ai_extract_service.llm_client.chat_completion",
               side_effect=fake_chat):
        out = await extract_section(db=None, question="今日总览", report=r)
    assert "接待 100" in captured["input"]
    assert "退款 5000" in captured["input"]
    assert out["result_md"] == "总览: 接待 100, 退款 5000。"


@pytest.mark.asyncio
async def test_extract_section_multi_scope_concat():
    """问句同时命中多 scope → 多段拼接喂给 LLM(架构文档 §8.1 取舍①)。

    keywords_to_scopes("接待和退款怎么样") 命中 reception + refund
    (接待→reception, 退款→refund; 见 ai_focus_service._KEYWORD_TO_SCOPE)。
    """
    md = "## 接待分析\n接待 100。\n\n## 退款分析\n退款 5000。\n\n## 投诉分析\n投诉 3。"
    r = _mock_report(md)
    mock_result = _mk_llm_result("接待+退款+投诉。")
    captured = {}

    async def fake_chat(msgs, **_kwargs):
        captured["input"] = msgs[1]["content"]
        return mock_result

    with patch("app.services.ai_extract_service.llm_client.chat_completion",
               side_effect=fake_chat):
        out = await extract_section(db=None, question="接待和退款怎么样", report=r)
    # 输入应含 ## 接待分析 + ## 退款分析 段, 不含 ## 投诉分析
    assert "接待 100" in captured["input"]
    assert "退款 5000" in captured["input"]
    assert "投诉 3" not in captured["input"]
    assert out["result_md"] == "接待+退款+投诉。"
