"""问句意图识别测试 (工单 #182)

## 为什么需要这个文件

识别逻辑此前散在 5 处、各自用字面包含匹配，**词表漏一个词就静默走错路**，
且无任何提示。本文件把「真实业务说法」编码为断言 —— 漏判在 CI 失败，
而不是等用户发现 AI 答"未提供"。

⚠️ 词表是**按用户实际说法**收集的，不是按设计者书面语。
    实测踩过两次：
      · 「本周专项接待**总计**」 → 原表只有"整体/总览" → 漏判 overview
      · 「谁接待**最多**」       → 原表只有"谁最"      → 漏判 personal
    故下面每个域都用**多种口语变体**验证。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app.services import ai_intent_service as I  # noqa: E402

# ─────────────────────────────────────────────────────────────────────
# 维度 2: 内容域
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,expected", [
    # 退款域（含同义词：客诉 vs 投诉 这类实测踩过的坑）
    ("今天退款四路明细", {"refund"}),
    ("退费情况怎么样", {"refund"}),
    ("退单率多少", {"refund"}),
    ("提现正常吗", {"refund"}),
    # 投诉域 —— "客诉" 是真实说法，原 _KEYWORD_TO_SCOPE 匹配不上
    ("客诉积压情况", {"complaint"}),
    ("投诉处理得怎么样", {"complaint"}),
    ("催单太多了", {"complaint"}),
    ("外部投诉有多少", {"complaint"}),
    # 接待域
    ("接待量多少", {"reception"}),
    ("来电量统计", {"reception"}),
    ("咨询会话数", {"reception"}),
    # 个人绩效域（看板有数据，但此前无此域）
    ("我的绩效怎么样", {"personal"}),
    ("张三的成绩", {"personal"}),
    ("谁接待最多", {"reception", "personal"}),
    ("来电量排行", {"reception", "personal"}),
    ("人均效率", {"personal"}),
    # 全量经营域（含口语变体 —— 实测漏判过"总计"）
    ("今天整体经营怎么样", {"overview"}),
    ("大盘怎么样", {"overview"}),
    ("本周专项接待总计", {"reception", "overview"}),
    ("今天一共接待多少人", {"reception", "overview"}),
    ("全部门投诉情况", {"complaint", "overview"}),
])
def test_domain_recognition(q, expected):
    assert I.classify(q).domains == expected, (
        f"「{q}」识别为 {sorted(I.classify(q).domains)}，期望 {sorted(expected)}\n"
        f"⚠️ 词表按**用户实际说法**收集，补词时请连同本测试一起加"
    )


def test_unknown_question_yields_empty_domains():
    """无任何域命中 → 空集 + unmatched_hint（供调用方决定是否 clarify）"""
    it = I.classify("你好")
    assert it.domains == set()
    assert it.unmatched_hint is True


def test_empty_question_is_safe():
    it = I.classify("")
    assert it.domains == set()
    assert it.unmatched_hint is True


# ─────────────────────────────────────────────────────────────────────
# 维度 3: 粒度
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,expected", [
    ("今天退款明细", "standard"),
    ("退款分布", "standard"),
    ("投诉笔数多少", "standard"),
    ("为什么退款这么多", "deep"),
    ("退款环比变化", "deep"),
    ("给点建议", "deep"),
    ("今天整体经营", "brief"),      # 泛问 → 简报
    ("接待量多少", "brief"),
])
def test_granularity(q, expected):
    assert I.classify(q).granularity == expected


def test_deep_beats_standard():
    """深度意图优先于明细（问句同时含两类词时取 deep）

    如「为什么昨天退款四路这么少, 明细呢」含"为什么"+"明细" → deep。
    """
    it = I.classify("为什么昨天退款四路这么少, 明细呢")
    assert it.granularity == "deep"


# ─────────────────────────────────────────────────────────────────────
# 维度 4: 数据新鲜度（本模块最关键的修正）
# ─────────────────────────────────────────────────────────────────────

def test_explicit_live_request():
    """用户明确说"最新/现在" → 必须实时"""
    for q in ("现在最新退款数据", "实时接待量", "当前投诉情况", "马上给我看看"):
        it = I.classify(q)
        assert it.freshness == "needs_live", f"「{q}」应判 needs_live"


def test_personal_always_needs_live():
    """**个人绩效始终 needs_live**

    理由: 无预生成模板（看板数据随时在变），只能实时聚合。
    这条断言直接对应「实时性判定倒置」的修正 ——
    原实现里它取决于"模板是否命中"，而个人绩效根本没有模板可命中。
    """
    for q in ("我的绩效怎么样", "谁接待最多", "客服排名前10"):
        assert I.classify(q).freshness == "needs_live"


def test_closed_window_is_snapshot_ok():
    """已结束的窗口 → 数据已定，快照即权威"""
    for q in ("昨天退款多少", "上周投诉情况", "上个月经营如何"):
        it = I.classify(q)
        assert it.freshness == "snapshot_ok", f"「{q}」应判 snapshot_ok"


def test_overview_is_snapshot_ok():
    """概览域有预生成日报/周报/月报覆盖"""
    assert I.classify("今天整体经营").freshness == "snapshot_ok"


def test_freshness_defaults_to_snapshot_not_live():
    """**默认取 snapshot_ok 而非 needs_live**

    取舍: 快照路径未命中时会**自动降级**到实时（既有链路已如此），
    反过来（默认实时）会让所有问句都慢 30~50 秒。
    即"漏判的代价是慢，而非答不出"。
    """
    it = I.classify("退款情况")     # 无时间词、无个人域
    assert it.freshness == "snapshot_ok"


def test_freshness_has_reason_for_observability():
    """判定理由必须可读 —— 误判时能据此排查，而不是只看到一个枚举值"""
    it = I.classify("我的绩效")
    assert it.freshness_reason
    assert "绩效" in it.freshness_reason


# ─────────────────────────────────────────────────────────────────────
# 维度 1: 时间提及（只判断有无，解析交给 ai_time_parser）
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,expected", [
    ("今天怎么样", True),
    ("上周退款", True),
    ("9月1日到15日", True),
    ("2026-09-01 的数据", True),
    ("退款情况", False),
    ("我的绩效", False),
])
def test_mentions_time(q, expected):
    assert I.classify(q).mentions_time == expected


def test_time_parsing_is_not_duplicated_here():
    """本模块**不**解析具体时间值 —— 复用既有 ai_time_parser

    理由: 时间解析已有完善实现（正则 + LLM 双解析、显式参数优先、
    防窗口错位）。重复实现必然漂移。
    """
    it = I.classify("今天退款")
    assert not hasattr(it, "start")
    assert not hasattr(it, "end")


# ─────────────────────────────────────────────────────────────────────
# 对外接口
# ─────────────────────────────────────────────────────────────────────

def test_describe_is_json_serializable():
    """describe() 供审计/日志用，必须可直接序列化"""
    import json
    d = I.classify("今天退款明细").describe()
    json.dumps(d)          # 不抛即通过
    assert set(d) >= {"domains", "granularity", "freshness", "mentions_time"}


def test_domain_scopes_maps_to_feed_sections():
    """域 → feed 段名映射（供 ai_focus_service 复用）"""
    it = I.classify("退款和投诉都说说")
    assert I.domain_scopes(it) == {"refund", "complaint"}
    # overview 不产生具体段（表示"全都要"）
    assert I.domain_scopes(I.classify("整体经营")) == set()
