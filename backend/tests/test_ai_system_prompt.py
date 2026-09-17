# -*- coding: utf-8 -*-
"""AI System Prompt 锚点约束测试 (工单 #134 批次 B1)。

覆盖:
- _SYSTEM_PROMPT 含 5 个 H2 锚点定义(## 接待分析 / ## 退款分析 / ## 投诉分析
  / ## 趋势对比 / ## 结论建议)—— #135 摘取层切段依赖此约定, 防回流
- _SYSTEM_PROMPT 仍保留前台实时 500 字约束(实时路径不被 #134 放宽)
- _SYSTEM_PROMPT 明确"预生成"路径可详尽展开(为 #134 放宽 max_tokens 提供语义许可)
"""

from app.services.ai_feed import _SYSTEM_PROMPT

# 5 个 H2 锚点(#135 摘取层切段依赖此约定)
H2_ANCHORS = [
    "## 接待分析",
    "## 退款分析",
    "## 投诉分析",
    "## 趋势对比",
    "## 结论建议",
]


def test_system_prompt_contains_h2_anchors():
    """_SYSTEM_PROMPT 必须含 5 个 H2 锚点定义(防未来误删)。"""
    for anchor in H2_ANCHORS:
        assert anchor in _SYSTEM_PROMPT, (
            f"_SYSTEM_PROMPT 缺少 H2 锚点 {anchor!r} —— #134 应已加入, "
            f"#135 摘取层切段依赖此约定, 缺失会导致切段失败"
        )


def test_system_prompt_keeps_realtime_500_word_constraint():
    """前台实时 500 字约束必须保留(实时路径不被 #134 放宽)。"""
    assert "500" in _SYSTEM_PROMPT, (
        "_SYSTEM_PROMPT 不再含 500 字约束 —— 前台实时问答仍受 500 字限制, "
        "#134 仅放宽后台预生成路径, 实时路径不能跟着放宽"
    )


def test_system_prompt_allows_pregen_detailed_output():
    """预生成路径必须被显式允许详尽展开(语义许可, 配合 max_tokens 放宽)。"""
    # 必须提到"预生成"路径的许可措辞
    assert "预生成" in _SYSTEM_PROMPT, (
        "_SYSTEM_PROMPT 未含'预生成'许可措辞 —— #134 放宽 max_tokens 后, "
        "需在 prompt 里给模型语义许可才能充分利用 token 预算"
    )
    # 必须含"详尽"或类似许可措辞
    assert ("详尽" in _SYSTEM_PROMPT or "不受此限" in _SYSTEM_PROMPT), (
        "_SYSTEM_PROMPT 未含详尽展开许可措辞, 模型可能在预生成路径也保守省字"
    )
