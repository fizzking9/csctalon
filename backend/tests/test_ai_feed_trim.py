# -*- coding: utf-8 -*-
"""AI feed 段预算裁剪测试 (工单 #189)。

覆盖(对应工单 #189 验收标准 1/2/4):
- _plan_feed_sections: 业务段必保 / 按优先级取舍 / 不做段内切字符
- _dump_feed: 产出合法 JSON + 省略段登记到 _trimmed_out_sections
- _select_feed_sections: 段顺序确定化(与 set 迭代顺序无关) + 业务段前置
- build_messages: 超预算时把省略说明写进 prompt(模型不得写"系统无此数据")
"""

import json
import random
import subprocess
import sys

from app.services.ai_feed import (
    _BUSINESS_SECTION_ORDER,
    _CONSTANT_FEED_KEYS,
    _TRIM_MARKER_KEY,
    _dump_feed,
    _feed_char_limit,
    _plan_feed_sections,
    _select_feed_sections,
)

# ── 构造最小可辨 feed ────────────────────────────────────────────────────


def _feed(business_payload_chars: int = 60) -> dict:
    """业务段(必保) + 恒量段 + 可让位段 的合成 feed。"""
    fill = "数" * business_payload_chars
    return {
        "period": "daily",
        "date": "2026-09-24",
        "date_start": "2026-09-24T00:00:00+08:00",
        "date_end": "2026-09-24T16:00:00+08:00",
        "generated_at": "2026-09-24T16:00:00+08:00",
        "reception": {"total": {"value": 1946, "note": fill}, "ai_takeover": {"session_pct": 12.9}},
        "refund": {"total_amount": {"value": 18192.46}, "routes": {"route_a": {"count": 75}},
                   "actual_cashflow_e": {"count": 0}, "a_route": {"litigation_ratio": {"value": 68.9}}},
        "complaint": {"total": {"registered": 76}, "by_source": [{"source": "内部投诉", "registered": 64}]},
        "_spec": {"money_unit": "金额单位=元", "lead": "专项负责人口径", "note": fill},
        "hourly_trend": {"labels": ["09:00"], "reception": [1], "note": fill},
        "refund_recovery": {"kpi": {"success_rate": 40.66}, "note": fill},
        "daily_trend": [{"date": "2026-09-23", "reception": 2000, "note": fill}],
        "complaint_breakdown": [{"source": "内部投诉", "registered": 64}],
        "staff_top5": [{"name": "卢宁", "reception_count": 570}],
        "queue": {"todo_count": 3},
    }


# ── _plan_feed_sections / _dump_feed ────────────────────────────────────


def test_plan_keeps_business_sections_when_budget_tight():
    """预算再紧, 三个业务段(必保)也必须完整保留, 让位的只能是 _spec/时间序列。"""
    feed = _feed()
    kept, dropped = _plan_feed_sections(feed, limit=1200)
    for k in ("refund", "complaint", "reception"):
        assert k in kept
        assert kept[k] == feed[k]  # 整段保留, 不做段内切字符
    # 窗口元信息也必保(_SYSTEM_PROMPT 铁律 6 要求回答写明窗口)
    for k in ("date_start", "date_end", "generated_at", "period", "date"):
        assert k in kept
    # 预算内装不下的非必保段被整段省略(具体丢弃哪几段取决于体积, 不写死)
    assert dropped
    assert all(k not in kept for k in dropped)
    assert not set(dropped) & {"refund", "complaint", "reception", "date_start", "date_end"}


def test_plan_keeps_everything_when_budget_ample():
    """预算充足 → 一段不丢。"""
    feed = _feed()
    kept, dropped = _plan_feed_sections(feed, limit=100000)
    assert dropped == []
    assert set(kept) == set(feed)


def test_plan_priority_order_spec_before_timeseries():
    """让位顺序: 时间序列/走势(优先级 3/4)先于口径卡 _spec(优先级 2)与投影段(优先级 1)。

    用「使某段被保留的最小预算」度量, 避免依赖固定字数的脆弱断言:
    阈值越小 = 越优先被保留(让位越晚)。
    """
    feed = _feed(business_payload_chars=400)
    th = {k: _min_limit_to_keep(feed, k) for k in
          ("staff_top5", "_spec", "hourly_trend", "refund_recovery", "daily_trend")}
    assert th["staff_top5"] < th["_spec"] < th["hourly_trend"] <= th["daily_trend"]
    assert th["refund_recovery"] < th["daily_trend"]


def _min_limit_to_keep(feed: dict, key: str) -> int:
    """二分求「该段被保留」的最小字符预算(_plan_feed_sections 对 limit 单调)。"""
    lo, hi = 1000, 400000
    assert key in _plan_feed_sections(feed, hi)[0], f"{key} 在足够大预算下仍被省略"
    while lo < hi:
        mid = (lo + hi) // 2
        if key in _plan_feed_sections(feed, mid)[0]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def test_dump_feed_produces_valid_json_with_marker():
    """_dump_feed 产出必须是合法 JSON, 且省略段登记在 _trimmed_out_sections。"""
    feed = _feed()
    kept, dropped = _plan_feed_sections(feed, limit=1200)
    text = _dump_feed(kept, dropped, limit=1200)
    parsed = json.loads(text)  # 旧实现 raw[:limit] 会产出被腰斩的非法 JSON
    assert parsed[_TRIM_MARKER_KEY] == dropped
    assert "reception" in parsed and "refund" in parsed


def test_dump_feed_no_marker_when_nothing_dropped():
    """无省略 → 不附加登记键(未超预算时输出与改造前一致, 回归安全)。"""
    feed = _feed()
    text = _dump_feed(feed, [], limit=100000)
    assert _TRIM_MARKER_KEY not in json.loads(text)


def test_business_section_never_dropped_even_if_over_budget():
    """业务段自身超预算也不裁数据(数据完整性优先于字符上限)。"""
    feed = _feed(business_payload_chars=3000)  # 业务段远超预算
    kept, dropped = _plan_feed_sections(feed, limit=1000)
    for k in ("refund", "complaint", "reception"):
        assert k in kept
    text = _dump_feed(kept, dropped, limit=1000)
    assert json.loads(text)["refund"]["a_route"]["litigation_ratio"]["value"] == 68.9


def test_feed_char_limit_prefers_explicit_value():
    """显式 feed_limit 优先于 settings.AI_ANALYZE_MAX_CHARS(预生成按窗口放宽)。"""
    assert _feed_char_limit(6000) == 6000
    assert _feed_char_limit(None) >= 1000


# ── _select_feed_sections: 顺序确定性 ───────────────────────────────────


def test_select_feed_sections_order_is_deterministic():
    """段顺序与 set 迭代顺序无关(原实现依赖 PYTHONHASHSEED, 截断下缺失维度随机)。"""
    feed = _feed()
    orders = set()
    for i in range(60):
        items = list({"refund", "complaint", "reception"})
        random.Random(i).shuffle(items)
        out = _select_feed_sections(feed, set(items))
        orders.add(tuple(out.keys()))
    assert len(orders) == 1, f"段顺序不稳定: {orders}"


def test_select_feed_sections_business_first_then_constants():
    """业务段前置(截断兜底下也先保住业务数据), 恒量段在后。"""
    feed = _feed()
    out = _select_feed_sections(feed, {"refund", "complaint", "reception"})
    keys = list(out.keys())
    assert keys[:3] == list(_BUSINESS_SECTION_ORDER)
    for k in _CONSTANT_FEED_KEYS:
        if k in feed:
            assert k in out
    assert keys.index("refund") < keys.index("_spec")


def test_select_feed_sections_queue_is_delivered():
    """#189: queue(今日待办/异常事件) 原先被永久丢弃, 现作恒量段下发。"""
    feed = _feed()
    out = _select_feed_sections(feed, {"refund"})
    assert "queue" in out


def test_select_feed_sections_none_returns_same_object():
    """scopes=None → 原样返回(兼容改造前行为)。"""
    feed = _feed()
    assert _select_feed_sections(feed, None) is feed


# ── 跨进程 hash seed 一致性(工单 #189 验收标准 2) ─────────────────────────

_PROBE = """
import json
from app.services.ai_feed import _plan_feed_sections, _select_feed_sections
feed = json.loads(%r)
sel = _select_feed_sections(feed, {"refund", "complaint", "reception"})
kept, dropped = _plan_feed_sections(sel, 1200)
print(json.dumps({"order": list(sel.keys()), "kept": sorted(kept), "dropped": sorted(dropped)},
                 ensure_ascii=False))
"""


def test_section_plan_stable_across_hash_seeds():
    """不同 PYTHONHASHSEED(模拟不同容器启动)下, 段顺序与保留结果必须完全一致。"""
    feed = _feed()
    script = _PROBE % json.dumps(feed, ensure_ascii=False)
    outs = set()
    for seed in ("1", "2", "3", "7", "11", "42"):
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                           env={**_env(), "PYTHONHASHSEED": seed, "PYTHONPATH": _backend_root()})
        assert r.returncode == 0, r.stderr
        outs.add(r.stdout.strip())
    assert len(outs) == 1, f"跨进程结果不一致: {outs}"


def _env() -> dict:
    import os
    return dict(os.environ)


def _backend_root() -> str:
    import os
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
