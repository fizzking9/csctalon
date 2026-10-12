# -*- coding: utf-8 -*-
"""#307 AI 问答质量治理: feed 预计算 + 提示词双版拆分测试。

覆盖:
- _verdict_from_yoy: 方向基准(退款少=好/接待多=好)、持平阈值(|yoy|<0.05)、None
- _add_verdicts: refund.total_amount/total_count、a_route.litigation_ratio、reception.total
- _enrich_complaint_breakdown: 三源 prev 前值补齐 + backlog_prev_pct/backlog_verdict
- _format_win_times: ISO 时间戳格式化到分钟(去微秒)
- build_messages call_mode: live→gen_system_live(DB 优先/常量兜底) + 裁 routes_prev;
  pregen→gen_system(DB 优先/常量兜底) + 保留 routes_prev
"""

import pytest

from app.services import ai_feed


# ---- _verdict_from_yoy ----
def test_verdict_from_yoy_directions():
    # 退款(越少越好): 上升=利空, 下降=利好
    assert ai_feed._verdict_from_yoy(2.4, more_is_better=False) == "利空"
    assert ai_feed._verdict_from_yoy(-2.4, more_is_better=False) == "利好"
    # 接待(越多越好): 上升=利好, 下降=利空
    assert ai_feed._verdict_from_yoy(25.0, more_is_better=True) == "利好"
    assert ai_feed._verdict_from_yoy(-25.0, more_is_better=True) == "利空"


def test_verdict_from_yoy_flat_and_none():
    # |yoy| < 0.05 → 持平(0.0/-0.0 不算涨跌, 与一位小数精度一致)
    assert ai_feed._verdict_from_yoy(0.0, more_is_better=False) == "持平"
    assert ai_feed._verdict_from_yoy(0.04, more_is_better=True) == "持平"
    assert ai_feed._verdict_from_yoy(None, more_is_better=True) is None
    assert ai_feed._verdict_from_yoy("abc", more_is_better=True) is None


# ---- _add_verdicts ----
def test_add_verdicts_refund_and_reception():
    feed = {
        "refund": {
            "total_amount": {"value": 100, "yoy_value": 90, "yoy_pct": 11.1},
            "total_count": {"value": 5, "yoy_value": 5, "yoy_pct": 0.0},
            "a_route": {"litigation_ratio": {"value": 37.4, "yoy_pct": -3.2,
                                             "delta_pct_points": -2.1}},
        },
        "reception": {"total": {"value": 50, "yoy_value": 40, "yoy_pct": 25.0}},
    }
    ai_feed._add_verdicts(feed)
    assert feed["refund"]["total_amount"]["verdict"] == "利空"
    assert feed["refund"]["total_count"]["verdict"] == "持平"
    # 涉诉率: 按百分点差方向? 不 — 按 yoy_pct 相对值方向, 下降=利好
    assert feed["refund"]["a_route"]["litigation_ratio"]["verdict"] == "利好"
    assert feed["reception"]["total"]["verdict"] == "利好"


def test_add_verdicts_skips_missing_yoy():
    """无 yoy_pct 键的指标不加 verdict(live 铁律 2: 无 verdict 只报数字)。"""
    feed = {"refund": {"total_amount": {"value": 100}}}
    ai_feed._add_verdicts(feed)
    assert "verdict" not in feed["refund"]["total_amount"]


# ---- _enrich_complaint_breakdown ----
def test_enrich_complaint_breakdown_prev_and_verdict():
    feed = {
        "complaint": {
            "total": {"registered": 10, "handled": 8, "backlog": 3,
                      "backlog_prev_pct": 50.0},
            "by_source": [
                {"source": "内部投诉", "registered": 4, "handled": 3, "backlog": 1,
                 "backlog_prev_pct": -25.0},
                {"source": "外部投诉", "registered": 3, "handled": 2, "backlog": 2},
            ],
            "prev": {
                "total": {"registered": 8, "handled": 6, "backlog": 2},
                "by_source": [
                    {"source": "内部投诉", "registered": 5, "handled": 4, "backlog": 1},
                    {"source": "外部投诉", "registered": 2, "handled": 2, "backlog": 1},
                ],
            },
        },
    }
    ai_feed._enrich_complaint_breakdown(feed)
    # total 节点: 前值 + 代码算环比 + verdict(登记少=好, 处理多=好, 积压少=好)
    tot = feed["complaint"]["total"]
    assert tot["registered_prev"] == 8 and tot["handled_prev"] == 6
    assert tot["registered_prev_pct"] == 25.0      # (10-8)/8
    assert tot["handled_prev_pct"] == 33.3         # (8-6)/6
    assert tot["registered_verdict"] == "利空"      # 登记上升=利空
    assert tot["handled_verdict"] == "利好"         # 处理上升=利好(实测判反处)
    assert tot["backlog_verdict"] == "利空"         # 积压 +50%=利空
    # by_source 投影: complaint_breakdown 可达(scope 切片后)
    bd = feed["complaint_breakdown"]
    assert len(bd) == 2
    inner = bd[0]
    assert inner["registered_prev"] == 5 and inner["handled_prev"] == 4 and inner["backlog_prev"] == 1
    assert inner["backlog_prev_pct"] == -25.0
    assert inner["backlog_verdict"] == "利好"
    # 外部投诉: prev 有值 → 补前值; 基期 0/None 时 pct 不算
    ext = bd[1]
    assert ext["registered_prev"] == 2 and "backlog_verdict" not in ext


def test_enrich_complaint_breakdown_no_complaint_section():
    feed = {"reception": {"total": {"value": 1}}}
    ai_feed._enrich_complaint_breakdown(feed)  # 不抛异常即可
    assert "complaint_breakdown" not in feed


# ---- _format_win_times ----
def test_format_win_times_strips_microseconds():
    feed = {
        "date_start": "2026-10-12T00:00:00+08:00",
        "date_end": "2026-10-12T20:20:06.543534+08:00",
        "generated_at": "2026-10-12T20:20:06.543534+08:00",
    }
    ai_feed._format_win_times(feed)
    assert feed["date_start"] == "2026-10-12 00:00"
    assert feed["date_end"] == "2026-10-12 20:20"
    assert feed["generated_at"] == "2026-10-12 20:20"


def test_format_win_times_ignores_non_iso():
    feed = {"date_start": "2026-10-12", "date_end": None, "generated_at": 123}
    ai_feed._format_win_times(feed)
    assert feed["date_start"] == "2026-10-12"  # 非 ISO 串不动
    assert feed["date_end"] is None


# ---- build_messages call_mode ----
_FAKE_FEED = {
    "date_start": "2026-10-12T00:00:00+08:00",
    "date_end": "2026-10-12T20:20:06.543534+08:00",
    "generated_at": "2026-10-12T20:20:06.543534+08:00",
    "refund": {
        "total_amount": {"value": 100, "yoy_value": 90, "yoy_pct": 11.1},
        "routes": {"route_a": {"amount": 60.0, "count": 3, "amount_pct": 60.0, "count_pct": 60.0}},
        # 历史四路前值: live 应被裁掉, pregen 保留
        "routes_prev": {"route_a": {"amount": 55.0}},
    },
    "complaint": {
        "total": {"registered": 10, "handled": 8, "backlog": 3},
        "by_source": [{"source": "内部投诉", "registered": 4, "handled": 3, "backlog": 1}],
        "prev": {"by_source": [{"source": "内部投诉", "registered": 5, "handled": 4, "backlog": 1}]},
    },
    "_spec": {},
}


@pytest.fixture()
def fake_feed(monkeypatch):
    """绕开 _build_feed 的真实取数, 用构造 feed 精确断言投影行为。

    注: _build_feed 返回前已接线 _format_win_times(真实链路), 此处模拟同接线。"""
    def _fake_build(*a, **kw):
        fd = json_copy(_FAKE_FEED)
        ai_feed._format_win_times(fd)
        return fd

    monkeypatch.setattr(ai_feed, "_build_feed", _fake_build)
    return _FAKE_FEED


def json_copy(d):
    import copy
    return copy.deepcopy(d)


def test_build_messages_live_uses_live_prompt_and_drops_routes_prev(db, fake_feed):
    """live(缺省): system 用 gen_system_live(DB 无行→常量兜底); feed 裁 routes_prev。"""
    msgs = ai_feed.build_messages(db, "daily", None, "今日退款情况如何?")
    assert "简洁回答实时问句" in msgs[0]["content"]          # live 版特征
    assert "数据分析助手" not in msgs[0]["content"]          # 预生成版特征不出现
    assert "routes_prev" not in msgs[1]["content"]
    # 窗口时间已格式化到分钟(无微秒)
    assert "2026-10-12 20:20" in msgs[1]["content"]


def test_build_messages_pregen_uses_pregen_prompt_keeps_routes_prev(db, fake_feed):
    """pregen: system 用 gen_system(DB 无行→常量兜底); feed 保留 routes_prev。"""
    msgs = ai_feed.build_messages(db, "daily", None, "经营周报",
                                  call_mode="pregen", window_label="预生成(template=daily)")
    assert "数据分析助手" in msgs[0]["content"]
    assert "简洁回答实时问句" not in msgs[0]["content"]
    assert "routes_prev" in msgs[1]["content"]


def test_build_messages_live_db_prompt_precedence(db, fake_feed):
    """DB 生效版优先于代码常量(#178 契约), gen_system_live 码路由正确。"""
    from app.models.prompt_config import PromptConfig
    db.add(PromptConfig(code="gen_system_live", name_cn="测试实时版",
                        content="LIVE_MARKER_PROMPT", version=1, is_active=True))
    db.flush()
    msgs = ai_feed.build_messages(db, "daily", None, "今日退款情况如何?")
    assert "LIVE_MARKER_PROMPT" in msgs[0]["content"]
    # pregen 仍走 gen_system 码, 不吃 gen_system_live 行
    msgs2 = ai_feed.build_messages(db, "daily", None, "周报", call_mode="pregen")
    assert "LIVE_MARKER_PROMPT" not in msgs2[0]["content"]
