"""「负责人」双体系口径守护测试 (#91)

## 为什么需要

用户反馈（2026-09-14）：「明明有很清晰的负责人、组长划分，为什么会出现
唐彩绘是负责人、董梦玲是负责人的说法？」

排查结论：**「负责人」在本系统是两套相互独立的数据体系** ——
  · **专项负责人**（业务）→ `specialties.current_lead_id`
  · **行政组长**（管理）  → `groups.current_leader_id`

同一人可能在某专项是 lead、在行政组只是成员（反之亦然）。而对外/对 AI
的输出**只给了 `{"role": "lead"}` 这个英文枚举**，读者无从区分是哪一套 ——
于是同一批人在不同界面表现为不同的「负责人」，看起来像数据矛盾。

更危险的一条通道：`ai_feed._compact_specialty` 只把 lead 的**名字**喂给模型，
模型在回答「谁是组长」时会直接复述专项目录里的 lead（它是"专项"口径），
从而产出与用户组织架构认知相悖的说法。

## 本测试锁定

1. 对外输出（external brief）必须带**语义标签** `role_label`；
2. AI feed 必须带 `lead_role` 语义，**不能只给名字**；
3. 口径卡（glossary）必须存在「双体系」说明，且问「负责人/组长」时会注入。
"""

from __future__ import annotations

import inspect

import pytest


# ── 对外输出：语义标签 ────────────────────────────────────────────────
def test_external_lead_has_role_label():
    """`_reception_specialty` 的 lead 必须带 `role_label` 与 `scope_note`。

    只给 `{"role": "lead"}` 无法区分"专项负责人"与"行政组长"（#91 根因）。
    """
    from app.api.v1 import external

    src = inspect.getsource(external._reception_specialty)
    assert "role_label" in src, "lead 必须带语义标签 role_label（#91）"
    assert "专项负责人" in src, "role_label 必须是可读中文「专项负责人」（#91）"
    assert "scope_note" in src, (
        "必须带 scope_note 说明它**不是**行政组长（#91：两者最易混淆）"
    )


def test_spec_brief_documents_lead_semantics():
    """`_spec_brief` 必须显式声明 lead 的口径（外部工具据此理解字段）。"""
    from app.api.v1 import external

    spec = external._spec_brief()
    assert "lead" in spec, "_spec_brief 必须声明 lead 字段口径（#91）"
    note = spec["lead"]
    # 必须同时提到两套体系与各自来源表，否则读者仍会混淆
    assert "专项负责人" in note
    assert "行政组长" in note
    assert "current_lead_id" in note
    assert "current_leader_id" in note


# ── AI feed：不能只给名字 ─────────────────────────────────────────────
def test_ai_feed_compaction_carries_lead_role():
    """`_compact_specialty` 必须带 `lead_role`，不能只给 lead 名字。

    回归: 原实现只输出 `"lead": <name>` —— 模型无法得知这是"专项"口径，
    会把它当作"组长"复述，产出与用户认知相悖的说法（#91）。
    """
    from app.services import ai_feed

    src = inspect.getsource(ai_feed._compact_specialty)
    assert "lead_role" in src, "AI feed 必须带 lead_role 语义标签（#91）"
    assert "专项负责人" in src
    assert "lead_note" in src, (
        "必须带 lead_note 显式排除「行政组长」误读（#91）"
    )


def test_ai_feed_compaction_runs():
    """行为验证：真实调用一次，确认字段到位且不抛异常。"""
    from app.services.ai_feed import _compact_specialty

    out = _compact_specialty([
        {
            "specialty": "退款挽回",
            "lead": {"name": "吴晓龙", "role": "lead", "role_label": "专项负责人"},
            "members": [{"name": "a"}, {"name": "b"}],
            "sessions": {"value": 10},
            "devices": {"value": 3},
        },
        {
            # lead 缺失（专项无负责人）→ 不得抛异常
            "specialty": "退款处理",
            "lead": None,
            "members": [],
            "sessions": {"value": 0},
            "devices": {"value": 0},
        },
    ])
    assert out[0]["lead"] == "吴晓龙"
    assert out[0]["lead_role"] == "专项负责人"
    assert out[0]["member_count"] == 2
    assert out[1]["lead"] is None
    assert out[1]["lead_role"] == "专项负责人"   # 标签恒在，值可空


# ── 口径卡：让模型也知道有两套 ─────────────────────────────────────────
def test_glossary_has_lead_systems_card():
    """必须存在「双体系」口径卡，且明确两套体系互不隶属。"""
    from app.services import ai_glossary

    assert "lead_systems" in ai_glossary.GLOSSARY, "缺少双体系口径卡（#91）"
    card = ai_glossary.GLOSSARY["lead_systems"]
    assert "专项负责人" in card
    assert "行政组长" in card
    # 必须给出"可不同人"这一关键事实，否则读者仍会以为矛盾
    assert "不同人" in card


@pytest.mark.parametrize("question", ["谁是组长", "退款挽回的负责人是谁", "各专项的分工"])
def test_lead_question_injects_lead_systems(question: str):
    """问「负责人/组长/专项」时必须注入双体系卡（否则模型会混为一谈）。"""
    from app.services import ai_glossary

    text = ai_glossary.glossary_for_question(question)
    assert "专项负责人" in text and "行政组长" in text, (
        f"问句「{question}」未注入双体系口径卡（#91）"
    )
