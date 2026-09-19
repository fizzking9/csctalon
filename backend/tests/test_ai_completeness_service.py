# -*- coding: utf-8 -*-
"""AI 报告完整性校验器测试 (工单 #152)。

覆盖 check_dimensions 的:
- 维度全部"有效出现" → complete=True
- 维度缺失(关键词未出现)→ missing 列该维度
- 维度声明但内容为空(未提供/暂无/无数据语境)→ 算缺失(触发补算)
- 同义词数组条目(任一词命中即覆盖)
- required_dimensions 为空/None → complete=True(不阻塞秒回)
- 关键词出现在行中但后续无内容(如 "四路:")→ 算空
"""

from unittest.mock import MagicMock

from app.services.ai_completeness_service import check_dimensions


def _mk_report(answer_md: str):
    r = MagicMock()
    r.id = 1
    r.answer_md = answer_md
    return r


def _mk_tmpl(dims):
    t = MagicMock()
    t.code = "test_template"
    t.required_dimensions = dims
    return t


# ── 维度齐全 ────────────────────────────────────────────
def test_all_dimensions_covered():
    md = """## 退款分析
四路分布: 线上直退 23 笔, 未提供线下。
涉诉 2 笔, 挽回 5 笔。

## 接待分析
接待量 100, 会话数 120。
"""
    t = _mk_tmpl(["四路", "涉诉", "挽回"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True
    assert out["missing"] == []


# ── 维度缺失 ────────────────────────────────────────────
def test_missing_dimension():
    """关键词未出现 → 该维度入 missing。"""
    md = "## 退款分析\n金额 5000 元。"
    t = _mk_tmpl(["四路", "涉诉", "挽回"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert "四路" in out["missing"]
    assert "涉诉" in out["missing"]
    assert "挽回" in out["missing"]


# ── 声明但为空 → 算缺失(核心: 触发补算) ──────────────────
def test_dimension_declared_but_empty():
    """维度行写"未提供/暂无" → 算未覆盖(内容为空, 触发补算器)。"""
    md = "## 退款分析\n四路分布: 未提供\n涉诉: 暂无\n挽回: 无数据"
    t = _mk_tmpl(["四路", "涉诉", "挽回"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert set(out["missing"]) == {"四路", "涉诉", "挽回"}


def test_dimension_anchor_only_empty():
    """关键词行后无内容(如 "四路:")→ 算空。"""
    md = "## 退款分析\n四路:\n金额 5000 元。"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_missing_data_context_counts_as_empty():
    """「数据缺失」语境(实测 09-18 快照: "四路退款金额及涉诉率数据缺失")→ 算未覆盖。

    该行若被误判为覆盖, 会掩盖快照四路实际缺失 → 不触发补算 → 用户仍拿到"数据缺失"。
    """
    md = ("## 退款分析\n"
          "**四路分布**：⚠️ **数据缺失**——给定 JSON 中未提供 A/B/C/D 四路具体金额。\n"
          "4. **数据补全**：四路退款金额及涉诉率数据缺失，需补取后重新评估。\n")
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


# ── 同义词数组条目 ───────────────────────────────────────
def test_synonym_entry_any_hit_covered():
    """条目为同义词数组时, 任一词命中且非空即覆盖。"""
    md = "## 趋势对比\n环比上周 +5%。"
    t = _mk_tmpl([["环比", "同比"]])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


# ── 无维度配置 → 直接完整 ────────────────────────────────
def test_no_required_dimensions():
    """required_dimensions 为空/None → complete=True(简报类不校验, 不阻塞秒回)。"""
    md = "## 退款分析\n四路: 未提供"
    assert check_dimensions(db=None, report=_mk_report(md),
                            template=_mk_tmpl(None))["complete"] is True
    assert check_dimensions(db=None, report=_mk_report(md),
                            template=_mk_tmpl([]))["complete"] is True
    # template 为 None → 不校验
    assert check_dimensions(db=None, report=_mk_report(md), template=None)["complete"] is True


# ── 空 answer_md ─────────────────────────────────────────
def test_empty_answer_md():
    md = ""
    t = _mk_tmpl(["四路", "涉诉"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert set(out["missing"]) == {"四路", "涉诉"}


# ── 大小写不敏感 ─────────────────────────────────────────
def test_case_insensitive():
    md = "## 趋势对比\nYOY 同比上周 +5%."
    t = _mk_tmpl(["同比"])
    assert check_dimensions(db=None, report=_mk_report(md), template=t)["complete"] is True
