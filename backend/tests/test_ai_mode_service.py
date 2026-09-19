# -*- coding: utf-8 -*-
"""AI 问句意图分级器测试 (工单 #152)。

覆盖 classify_question 的:
- deep 意图词(为什么/对比/根因/趋势/环比/同比/深挖...)→ deep
- standard 维度词(明细/四路/涉诉/挽回/笔数/金额/分析/详情...)→ standard
- 泛问(无词命中)→ brief(简报秒回保底)
- 优先级 deep > standard > brief(同时含深度词与维度词取 deep)
- 空问句 → brief(不抛异常)
"""

from app.services.ai_mode_service import classify_question


def test_deep_intent_words():
    """深度意图词 → deep。"""
    for q in ("为什么昨天退款上升", "对比上周接待量", "退款异常的原因是什么",
              "本月投诉趋势如何", "环比上周怎么变", "同比分析一下", "深挖一下退款四路",
              "深度分析投诉处理"):
        assert classify_question(q)["mode"] == "deep", q


def test_standard_dimension_words():
    """维度词 → standard(明细细问走快照优先+补算)。"""
    for q in ("昨天退款明细分析", "四路分布如何", "涉诉情况", "挽回多少笔",
              "退款金额是多少", "今天投诉多少件", "接待详情"):
        assert classify_question(q)["mode"] == "standard", q


def test_brief_generic_question():
    """泛问(无深度/维度词)→ brief(快照摘取秒回)。"""
    for q in ("今天经营怎么样", "昨天退款情况如何", "本周怎么样"):
        assert classify_question(q)["mode"] == "brief", q


def test_deep_beats_standard():
    """优先级 deep > standard: 同时含深度词与维度词取 deep。"""
    assert classify_question("为什么昨天退款四路这么少")["mode"] == "deep"
    assert classify_question("退款明细突然增加的原因是什么")["mode"] == "deep"


def test_empty_question():
    """空问句 → brief(不抛异常)。"""
    assert classify_question("")["mode"] == "brief"
    assert classify_question(None)["mode"] == "brief"  # type: ignore[arg-type]


def test_source_is_rule():
    """v1 纯规则实现, source 恒为 rule(LLM 兜底未接线, 架构文档 §4.2)。"""
    out = classify_question("退款明细分析")
    assert out["source"] == "rule"
