# -*- coding: utf-8 -*-
"""AI 报告完整性校验器测试 (工单 #152) — 结构检查(提示词约定锚点行)。

原则(2026-09-19 用户拍板): 报告结构由提示词确定(生成侧【维度输出格式】约定
`**<维度>**：<数据>` 锚点行), 校验器只做结构匹配, 不做语义猜测。

覆盖:
- 锚点行含维度 + 内容非空 → 覆盖
- 锚点行内容为空/「暂无」/缺失标记 → 缺失(触发补算)
- 旁述/比较/建议句(无锚点结构)→ 不算覆盖(即使含关键词/数字/渠道字母)
- 同义词数组条目
- required_dimensions 为空/None → complete=True
- 空 answer_md / 大小写不敏感
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


# ── 锚点行覆盖 ───────────────────────────────────────────
def test_anchor_with_data_covered():
    md = "## 退款分析\n**四路分布**：A 线上直退 23 笔、B 线下 12 笔、C 平台 5 笔。"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True
    assert out["missing"] == []


def test_anchor_label_contains_dimension_kw():
    """标签含维度关键词即可(如 维度"接待" 命中 "**接待量**：100 人次")。"""
    md = "**接待量**：100 人次\n**涉诉**：2 笔\n**挽回**：5 笔"
    t = _mk_tmpl(["接待", "涉诉", "挽回"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


# ── 锚点内容为空/缺失 → 缺失 ─────────────────────────────
def test_anchor_empty_body():
    md = "## 退款分析\n**四路分布**：\n金额 5000 元。"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_anchor_missing_marker_body():
    """锚点内容写"暂无/未提供" → 缺失(声明了但无数据)。"""
    md = "**四路金额（A/B/C/D）**：**暂无**（给定数据未提供金额字段及四路拆分）。"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_anchor_missing_data_context():
    """锚点内容首段含"缺失"语境 → 缺失。"""
    md = "**四路分布**：⚠️ **数据缺失**——给定 JSON 中未提供 A/B/C/D 四路具体金额。"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_partial_covered_with_data():
    """锚点内容首段有数据(即使行内后面有"未提供")→ 覆盖。"""
    md = "**四路分布**：线上直退 23 笔、线下未提供"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


# ── 旁述/比较/建议句 → 不算覆盖 ──────────────────────────
def test_side_sentence_not_covered():
    """无锚点结构的旁述句, 即使含关键词+数字+渠道字母 → 不算覆盖。

    实测 09-18 快照: 对账基准比较句 / 下钻建议句 均不是四路数据。
    """
    md = (
        "- **对账基准 E**：0 笔 / 0.00 元，与四路合计差异 100%，口径原因：B 路钱包提现。\n"
        "- **异动提醒**：因缺少金额与四路结构数据，建议下钻至\"退款四路明细看板\"确认 A 路占比。"
    )
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_bare_keyword_line_not_covered():
    """无粗体标签结构的普通句(即使含关键词)→ 不算覆盖(结构由提示词保证)。"""
    md = "退款四路金额、涉诉率等详细金额数据未在给定 JSON 中提供。"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


# ── 同义词数组条目 ───────────────────────────────────────
def test_synonym_entry_any_hit_covered():
    """条目为同义词数组时, 任一词命中且非空即覆盖。"""
    md = "**环比**：上周 +5%。"
    t = _mk_tmpl([["环比", "同比"]])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


# ── 无维度配置 → 直接完整 ────────────────────────────────
def test_no_required_dimensions():
    """required_dimensions 为空/None → complete=True(简报类不校验, 不阻塞秒回)。"""
    md = "## 退款分析\n**四路**：暂无"
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


# ── 大小写不敏感(英文维度) ───────────────────────────────
def test_case_insensitive_label():
    md = "**YOY 同比**：上周 +5%."
    t = _mk_tmpl(["yoy"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True
