# -*- coding: utf-8 -*-
"""AI 报告完整性校验器测试 (工单 #152) — 结构检查(提示词约定锚点行)。

原则(2026-09-19 用户拍板): 报告结构由提示词确定(生成侧【维度输出格式】约定
`**<维度>**：<数据>` 锚点行), 校验器只做结构匹配, 不做语义猜测。

覆盖:
- 锚点行含维度 + 内容非空 → 覆盖
- 锚点行内容为空/「暂无」/缺失标记 → 段落块检查(D9, 2026-10-12):
  * 块内(最多 8 非空行, 跳过空行, 遇标题/下一锚点行即停)有数字 → 覆盖
  * 块内无数字 → 缺失(触发补算)
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


# ── 锚点内容为空/缺失 → 段落块检查(D9) ───────────────────
def test_anchor_empty_body_with_data_below_covered():
    """D9: 空锚点行 + 数据列表在后续行 → 覆盖(生产 rolling 断供主失败形态)。"""
    md = (
        "### 退款分析\n"
        "**四路分布**：\n"
        "- A 线上直退：993 笔，金额 135,697.46 元，占比 58.5%。\n"
        "- B 线下：442 笔，金额 65,551.30 元，占比 28.3%。\n"
    )
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True
    assert out["missing"] == []


def test_anchor_empty_body_with_table_below_covered():
    """D9: 空锚点行 + 空行 + markdown 表格(数字) → 覆盖(空行不截断块)。"""
    md = (
        "**四路分布**：\n"
        "\n"
        "| 路 | 笔数 | 金额 |\n"
        "|---|---|---|\n"
        "| A | 993 | 135697.46 |\n"
    )
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


def test_anchor_empty_body_no_data_below_missing():
    """空锚点行 + 后续块无数字(纯文字/暂无) → 缺失(触发补算)。"""
    md = "## 退款分析\n**四路分布**：\n暂无。\n"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_anchor_empty_body_block_stops_at_next_anchor():
    """D9 边界: 空锚点行后紧跟下一个锚点行 → 块结束, 他维数据不归属本维。"""
    md = "**四路分布**：\n**涉诉率**：61.9%，环比下降。\n"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_anchor_empty_body_block_stops_at_heading():
    """D9 边界: 空锚点行后遇 `#` 标题 → 块结束, 下一节数据不归属本维。"""
    md = "**四路分布**：\n\n## 涉诉分析\n涉诉 2 笔。\n"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_anchor_empty_body_block_max_lines():
    """D9 边界: 块内前 8 个非空行无数字, 数字出现在更远处 → 不归属, 缺失。"""
    body_lines = "\n".join("无数据说明。" for _ in range(8))
    md = f"**四路分布**：\n{body_lines}\n金额 5000 元。\n"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_anchor_missing_marker_with_data_below_covered():
    """D9: 锚点首段含缺失标记但后续块有数字 → 数据存在, 覆盖。"""
    md = "**四路金额（A/B/C/D）**：**暂无**（明细见下）。\n- A 路 993 笔 135,697 元\n"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


def test_anchor_missing_marker_body():
    """锚点内容写"暂无/未提供"且后续块无数据 → 缺失(声明了但无数据)。"""
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


# ── 标题锚点(D9): 模型在铁律 3 "H2 锚点结构"下稳定输出标题式结构 ──
def test_heading_with_data_covered():
    """D9: 标题行含维度关键词且行内带数据 → 覆盖(实测 detail 体裁主形态)。"""
    md = "## 四路分布：A 线上直退 993 笔，金额 135,697.46 元\nB 线下 442 笔。"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


def test_heading_bare_with_block_below_covered():
    """D9: 裸标题(无冒号内容) + 紧随数据列表 → 段落块检查覆盖。"""
    md = "## 退款分析\n### 四路分布\n- A 路线上直退 993 笔、135,697.46 元\n- B 路线下 442 笔\n"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


def test_heading_kw_in_body_covered():
    """D9: 关键词命中标题冒号后内容(如 环比 出现在接待分析标题的环比句)→ 覆盖。"""
    md = "## 接待分析：本周接待量 36,266 笔，环比上周下降 3.8%（利空）\n"
    t = _mk_tmpl(["接待", "环比"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


def test_heading_missing_mark_missing():
    """标题写"暂无"且后续块无数据 → 缺失(诚实缺失仍触发补算)。"""
    md = "## 涉诉分析：暂无\n\n## 投诉分析：投诉 5 件。\n"
    t = _mk_tmpl(["涉诉", "投诉"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["涉诉"]


def test_heading_block_continues_past_sub_anchors():
    """D9 复测取证(27b t3): 标题章节的展开以子锚点行组织 —— 子锚点行不截断
    块扫描, 其后表格数字归属该章节。形态: `### 趋势对比` → `**接待量**：每日
    趋势如下：` → 表格行。"""
    md = ("### 趋势对比\n"
          "**接待量**：每日趋势如下：\n"
          "| 日期 | 接待量 |\n| 10-01 | 3,626 |\n| 10-02 | 3,590 |\n"
          "**退款金额**：每日趋势如下：\n"
          "| 日期 | 金额 |\n| 10-01 | 23,212 元 |\n")
    t = _mk_tmpl(["趋势"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


def test_heading_block_sub_anchors_without_data_missing():
    """D9 边界: 标题锚点 + 子锚点行均无数字(纯定性叙述) → 仍缺失。"""
    md = "### 趋势对比\n**接待量**：每日趋势如下：\n整体呈波动走势。\n"
    t = _mk_tmpl(["趋势"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["趋势"]


def test_heading_block_stops_at_next_heading():
    """D9 边界: 标题锚点的段落块遇下一个标题即终止, 后节数据不归属本节。"""
    md = "### 趋势对比\n整体走势平稳。\n\n## 投诉分析\n投诉 1,182 件。\n"
    t = _mk_tmpl(["趋势"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["趋势"]


def test_h1_not_anchor():
    """H1 是文档标题, 不作锚点(防标题党误判)。"""
    md = "# 四路分布\n今日天气晴朗。\n"
    t = _mk_tmpl(["四路"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is False
    assert out["missing"] == ["四路"]


def test_heading_qualitative_content_covered():
    """定性维度(结论)标题冒号后带非缺失陈述 → 覆盖(无数字但有实质内容)。"""
    md = "## 结论建议：本周整体经营环比改善, 建议关注退款合规操作。\n"
    t = _mk_tmpl(["结论"])
    out = check_dimensions(db=None, report=_mk_report(md), template=t)
    assert out["complete"] is True


def test_bold_body_kw_still_strict():
    """粗体锚点保持标签严格匹配: 关键词只出现在行内正文(旁述)→ 不算覆盖。"""
    md = "- **对账基准 E**：0 笔 / 0.00 元，与四路合计差异 100%。"
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
