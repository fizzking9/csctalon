# -*- coding: utf-8 -*-
"""B2-3 (#238 / R-P1-1): 预生成报告写库前 schema/数值校验闸门测试。

两部分:
1. validate_pregen_report 纯文本闸门各分支(空/过短/围栏不成对/占位符/维度缺失/合法)。
2. ai_report_worker.generate_for_template 与闸门的衔接:
   - 首发合法 → 直接落库(1 次 LLM / 1 次 save)
   - 首发非法、重试合法 → 落库(2 次 LLM / 1 次 save)
   - 两次均非法 → 不落库(2 次 LLM / 0 次 save)+ pregen_validation_failed 事件
   - 被抢占 CancelledError → 首次即跳过(1 次 LLM / 0 次 save)

不连真实 LLM/DB: build_messages / compute_pregen_window / _invoke_pregen_once /
save_report / record_system_event 全部 monkeypatch; 维度判定仍走真实 check_dimensions。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.services import ai_report_worker as W
from app.services.ai_report_validator import validate_pregen_report

# 一份合法的日报成稿(去空白 ≥30 字、无占位符、无代码围栏)。
_GOOD_MD = "**退款概览**：本周退款金额合计 1234.56 元，环比下降 2.3%。\n**投诉概览**：投诉 5 单，均已跟进处理。"
_BAD_SHORT = "太短了"  # 去空白 < 30 字


# ════════════════════════════════════════════════════════════════════════
# 1. validate_pregen_report 纯函数
# ════════════════════════════════════════════════════════════════════════
def _tmpl(required):
    """轻量 template(只需 required_dimensions; code 供日志)。"""
    return SimpleNamespace(required_dimensions=required, code="_test_tmpl")


def test_valid_report_passes():
    ok, errs = validate_pregen_report(_GOOD_MD, _tmpl([]))
    assert ok is True and errs == []


def test_empty_and_none_rejected():
    for md in ("", "   \n\t", None):
        ok, errs = validate_pregen_report(md, _tmpl([]))
        assert ok is False and errs, f"空回答必须拦截: {md!r}"


def test_too_short_rejected():
    ok, errs = validate_pregen_report(_BAD_SHORT, _tmpl([]))
    assert ok is False
    assert any("过短" in e for e in errs)


def test_unclosed_code_fence_rejected():
    md = _GOOD_MD + "\n```python\nprint('截断在代码块里"  # 只有一个围栏
    ok, errs = validate_pregen_report(md, _tmpl([]))
    assert ok is False
    assert any("围栏" in e for e in errs)


def test_closed_code_fence_ok():
    md = _GOOD_MD + "\n```\n片段\n```"  # 两个围栏, 成对
    ok, errs = validate_pregen_report(md, _tmpl([]))
    assert ok is True, errs


@pytest.mark.parametrize("token", ["NaN", "Infinity", "undefined", "null", "None"])
def test_program_placeholders_rejected(token):
    md = _GOOD_MD + f"\n备注：异常值为 {token}，需人工关注。"
    ok, errs = validate_pregen_report(md, _tmpl([]))
    assert ok is False
    assert any("占位符" in e and token in e for e in errs), f"占位符 {token} 必须拦截: {errs}"


def test_negative_percent_is_not_placeholder():
    """环比负百分比是合法内容, 不得被占位符/负号规则误杀。"""
    md = "**退款概览**：退款合计 100.00 元，环比 -12.0%，整体处于正常波动区间之内。"
    ok, errs = validate_pregen_report(md, _tmpl([]))
    assert ok is True, errs


def test_required_dimension_missing_rejected():
    # 模板要求「退款」「投诉」两维, 成稿只有退款锚点 → 缺投诉
    md = "**退款概览**：本周退款 1234.56 元，环比下降 2.3%，文字内容足够通过长度门槛。"
    ok, errs = validate_pregen_report(md, _tmpl(["退款", "投诉"]))
    assert ok is False
    assert any("投诉" in e for e in errs)


def test_required_dimension_written_as_missing_mark_rejected():
    # 锚点存在但内容写「暂无/未提供」→ 视为该维度实质缺失(check_dimensions 语义)
    md = ("**退款概览**：本周退款 1234.56 元。\n"
          "**投诉概览**：暂无投诉数据，文字补足长度以通过最小字数门槛校验。")
    ok, errs = validate_pregen_report(md, _tmpl(["退款", "投诉"]))
    assert ok is False
    assert any("投诉" in e for e in errs)


def test_required_dimensions_all_covered_passes():
    ok, errs = validate_pregen_report(_GOOD_MD, _tmpl(["退款", "投诉"]))
    assert ok is True, errs


def test_no_required_dimensions_skips_dimension_check():
    # required_dimensions 为空 → 不做维度检查, 只跑结构/占位护栏
    ok, errs = validate_pregen_report(_GOOD_MD, None)
    assert ok is True


# ════════════════════════════════════════════════════════════════════════
# 2. generate_for_template × 写库前闸门
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture()
def worker_mocks(monkeypatch):
    """隔离 generate_for_template 的外部依赖; 收集事件与 save 调用。"""
    monkeypatch.setattr(W, "build_messages",
                        lambda *a, **k: [{"role": "user", "content": "今日经营?"}])
    monkeypatch.setattr(W, "compute_pregen_window", lambda period: None)  # daily 路径

    events: list[dict] = []

    def _record(db, *, category, action, **kw):
        events.append({"category": category, "action": action, **kw})

    monkeypatch.setattr(W, "record_system_event", _record)

    saved: list[dict] = []

    def _save(db, **kw):
        saved.append(kw)
        return SimpleNamespace(id=999)

    monkeypatch.setattr(W, "save_report", _save)
    return events, saved


def _gen_template():
    return SimpleNamespace(
        period="daily", question_template="今日经营?", code="_test_b23",
        name_cn="测试-B23", id=1, max_tokens=2000, llm_provider_code=None,
        required_dimensions=[], scope=None, format=None,
    )


def test_good_first_attempt_saves_once(monkeypatch, worker_mocks):
    events, saved = worker_mocks
    monkeypatch.setattr(W, "_invoke_pregen_once",
                        lambda *a, **k: _GOOD_MD)

    out = W.generate_for_template(db=SimpleNamespace(), template=_gen_template(), level=1)

    assert out is not None and out["report_id"] == 999
    assert len(saved) == 1, "首发合法应直接落库一次"
    assert not [e for e in events if e["action"] == "pregen_validation_failed"]


def test_invalid_then_valid_retries_and_saves(monkeypatch, worker_mocks):
    events, saved = worker_mocks
    answers = iter([_BAD_SHORT, _GOOD_MD])  # 首发过短, 重试合法
    monkeypatch.setattr(W, "_invoke_pregen_once", lambda *a, **k: next(answers))

    out = W.generate_for_template(db=SimpleNamespace(), template=_gen_template(), level=1)

    assert out is not None
    assert len(saved) == 1, "重试合法后应落库一次"
    assert [e["action"] for e in events].count("pregen_validation_retry") == 1
    assert not [e for e in events if e["action"] == "pregen_validation_failed"]


def test_both_attempts_invalid_does_not_save(monkeypatch, worker_mocks):
    events, saved = worker_mocks
    monkeypatch.setattr(W, "_invoke_pregen_once", lambda *a, **k: _BAD_SHORT)

    out = W.generate_for_template(db=SimpleNamespace(), template=_gen_template(), level=1)

    assert out is None
    assert saved == [], "两次校验均非法时禁止落库"
    actions = [e["action"] for e in events]
    assert "pregen_validation_failed" in actions, "最终失败必须留 error 事件"
    assert actions.count("pregen_validation_retry") == 1, "首发失败应记一次重试 warning"


def test_cancelled_skips_without_retry(monkeypatch, worker_mocks):
    events, saved = worker_mocks
    calls = {"n": 0}

    def _raise_cancel(*a, **k):
        calls["n"] += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr(W, "_invoke_pregen_once", _raise_cancel)

    out = W.generate_for_template(db=SimpleNamespace(), template=_gen_template(), level=0)

    assert out is None
    assert calls["n"] == 1, "抢占首次即跳过, 不做校验重试"
    assert saved == []
    assert not [e for e in events if "validation" in e["action"]]
