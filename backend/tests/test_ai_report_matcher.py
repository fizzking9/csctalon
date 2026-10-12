# -*- coding: utf-8 -*-
"""AI 报告匹配层测试 (工单 #143)。

覆盖 #143 修复后的 match_template_report 排序逻辑:
- _count_keyword_hits: 多 keyword 命中数统计
- match_template_report 候选按「keywords 命中数 DESC + period 匹配 DESC + created_at DESC」排序
- 关键场景: 用户问「本月」被 effective() 强制 period=weekly 时, 仍应命中月报
  (月报 keywords 命中 2 个 '本月'+'同比' > 周报命中 1 个 '环比' = 日报命中 1 个 '整体')
"""

from unittest.mock import MagicMock

from app.services.ai_report_matcher import (
    _count_keyword_hits,
    _question_format_intent,
    _question_matches_keywords,
    _scope_priority,
    _window_jaccard,
    match_template_report,
)


# ── _count_keyword_hits 纯函数 ─────────────────────────────
def test_count_keyword_hits_multiple():
    """月报 keywords 命中 '本月'+'同比' = 2 个"""
    q = "本月经营情况总览, 含同比环比, 关键指标月度走势与下月建议"
    kws = ["本月", "这个月", "月报", "上月", "同比"]
    assert _count_keyword_hits(q, kws) == 2


def test_count_keyword_hits_single():
    """周报 keywords 仅命中 '环比' = 1 个"""
    q = "本月经营情况总览, 含同比环比, 关键指标月度走势与下月建议"
    kws = ["本周", "这周", "周报", "对比", "上周", "环比"]
    assert _count_keyword_hits(q, kws) == 1


def test_count_keyword_hits_zero():
    """无命中"""
    q = "本月经营情况"
    kws = ["今天", "今日"]
    assert _count_keyword_hits(q, kws) == 0


def test_count_keyword_hits_empty_keywords():
    """空 keywords 或 None 返 0"""
    assert _count_keyword_hits("任何问句", None) == 0
    assert _count_keyword_hits("任何问句", []) == 0


def test_count_keyword_hits_case_insensitive():
    """大小写不敏感"""
    q = "Today 报告"
    kws = ["today", "TODAY"]
    assert _count_keyword_hits(q, kws) == 2


# ── _question_matches_keywords 向后兼容 ───────────────────
def test_question_matches_keywords_any():
    """任一命中即 True(向后兼容)"""
    q = "今日经营"
    assert _question_matches_keywords(q, ["今天", "今日"]) is True
    assert _question_matches_keywords(q, ["昨天"]) is False
    assert _question_matches_keywords(q, None) is False
    assert _question_matches_keywords(q, []) is False


# ── match_template_report 排序逻辑(mock db) ────────────────
def _make_report_row(rid, template_id, snapshot_period, created_at, start=None, end=None):
    """构造 SQL row dict (模拟 db.execute().mappings().all() 返回)。

    start/end: 报告内容窗口(纯日期), 区间候选 SQL 以 r.start AS r_start / r.end AS r_end 取出。
    """
    import datetime as _dt
    return {
        "id": rid,
        "task_id": None,
        "user_id": 1,
        "template_id": template_id,
        "question": f"q{rid}",
        "period": snapshot_period,
        "snapshot_date": _dt.date(2026, 9, 17),
        "snapshot_period": snapshot_period,
        "is_public": True,
        "answer_md": f"## 接待分析\nreport {rid}",
        "llm_model": "test-model",
        "llm_ms": 1000,
        "total_ms": 1000,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "window_source": "test",
        "window_label": "test",
        "created_at": created_at,
        "r_start": start,
        "r_end": end,
    }


def _make_template(tid, code, keywords, scope="overview", format="brief"):
    """构造模板 mock 对象。"""
    tmpl = MagicMock()
    tmpl.id = tid
    tmpl.code = code
    tmpl.question_keywords = keywords
    tmpl.scope = scope
    tmpl.format = format
    return tmpl


def test_match_prefers_more_keyword_hits():
    """#143 核心场景: 多 keyword 命中优先于单 keyword 命中。

    用户问「本月经营情况总览, 含同比环比...」:
      - id=95 daily  keywords ['今天','今日','整体','总览','晨会','日报'] → 命中 '整体' (1 个)
      - id=93 monthly keywords ['本月','这个月','月报','上月','同比']    → 命中 '本月'+'同比' (2 个) ← 应命中
      - id=92 weekly  keywords ['本周','这周','周报','对比','上周','环比'] → 命中 '环比' (1 个)

    eff.period=weekly (用户问「本月」被 effective() 强制 weekly), 修复前会误命中 id=95 daily
    (因 SQL ORDER BY snapshot_period=weekly DESC 失效, 全按 created_at DESC, id=95 最新)。
    """
    import datetime as _dt

    q = "本月经营情况总览, 含同比环比, 关键指标月度走势与下月建议"
    # 候选: id=95 daily 最新, id=93 monthly 次新, id=92 weekly 最旧
    rows = [
        _make_report_row(95, 1, "daily", _dt.datetime(2026, 9, 17, 11, 0, tzinfo=_dt.timezone.utc)),
        _make_report_row(93, 3, "monthly", _dt.datetime(2026, 9, 17, 10, 0, tzinfo=_dt.timezone.utc)),
        _make_report_row(92, 2, "weekly", _dt.datetime(2026, 9, 17, 9, 0, tzinfo=_dt.timezone.utc)),
    ]
    templates = {
        1: _make_template(1, "daily_general_morning", ["今天", "今日", "整体", "总览", "晨会", "日报"]),
        3: _make_template(3, "monthly_overview", ["本月", "这个月", "月报", "上月", "同比"]),
        2: _make_template(2, "weekly_compare_last", ["本周", "这周", "周报", "对比", "上周", "环比"]),
    }

    db = MagicMock()
    # db.execute 返回 mappings().all() 链
    mappings_mock = MagicMock()
    mappings_mock.mappings.return_value = mappings_mock  # 链式 .mappings().all()
    mappings_mock.all.return_value = rows
    db.execute.return_value = mappings_mock
    # _get_template 调 db.query(AiReportTemplate).get(tid) → mock
    query_mock = MagicMock()
    query_mock.get.side_effect = lambda tid: templates.get(tid)
    db.query.return_value = query_mock
    # _bump_matched_count 内 db.execute/commit 不影响逻辑, mock 掉
    db.commit = MagicMock()
    db.rollback = MagicMock()
    db.expunge = MagicMock()

    matched, tmpl = match_template_report(db, q, "weekly", "2026-09-17")
    # 应命中 id=93 monthly (2 keyword 命中), 不是 id=95 daily (1 keyword 命中)
    assert matched is not None
    assert matched.id == 93
    assert tmpl.code == "monthly_overview"


def test_match_period_priority_on_tie():
    """同 keyword 命中数下, period 匹配的优先。

    两条月报 id=93 + id=90 都命中 '本月'+'同比' (2 个), period 都匹配 win=monthly → 取 created_at DESC (id=93 最新)。
    """
    import datetime as _dt

    q = "本月经营"
    rows = [
        _make_report_row(93, 3, "monthly", _dt.datetime(2026, 9, 17, 10, 0, tzinfo=_dt.timezone.utc)),
        _make_report_row(90, 3, "monthly", _dt.datetime(2026, 9, 17, 9, 0, tzinfo=_dt.timezone.utc)),
    ]
    templates = {3: _make_template(3, "monthly_overview", ["本月", "这个月", "月报", "上月", "同比"])}

    db = MagicMock()
    mappings_mock = MagicMock()
    mappings_mock.mappings.return_value = mappings_mock
    mappings_mock.all.return_value = rows
    db.execute.return_value = mappings_mock
    query_mock = MagicMock()
    query_mock.get.side_effect = lambda tid: templates.get(tid)
    db.query.return_value = query_mock
    db.commit = MagicMock()
    db.expunge = MagicMock()

    matched, _ = match_template_report(db, q, "monthly", "2026-09-17")
    assert matched.id == 93  # 同命中数+同 period → created_at DESC


def test_match_returns_none_when_no_keyword_hit():
    """无 keyword 命中 → 返 None (走 LLM 兜底)"""
    import datetime as _dt

    q = "完全无关的问句 abc"
    rows = [_make_report_row(95, 1, "daily", _dt.datetime(2026, 9, 17, 11, 0, tzinfo=_dt.timezone.utc))]
    templates = {1: _make_template(1, "daily_general_morning", ["今天", "今日", "整体"])}

    db = MagicMock()
    mappings_mock = MagicMock()
    mappings_mock.mappings.return_value = mappings_mock
    mappings_mock.all.return_value = rows
    db.execute.return_value = mappings_mock
    query_mock = MagicMock()
    query_mock.get.side_effect = lambda tid: templates.get(tid)
    db.query.return_value = query_mock

    matched, tmpl = match_template_report(db, q, "daily", "2026-09-17")
    assert matched is None
    assert tmpl is None


def _select_sql_of(db: MagicMock) -> str:
    """取 match_template_report 内的候选 SELECT SQL(跳过 _bump_matched_count 的 UPDATE)。"""
    for args, _kwargs in db.execute.call_args_list:
        sql = str(args[0])
        if "FROM ai_reports" in sql:
            return sql
    raise AssertionError("未找到候选 SELECT SQL")


# ── #151 窗口对齐: 单日/区间分流 + 重叠匹配 ──────────────────
def test_match_single_day_uses_snapshot_date_sql():
    """单日窗口(win_date 有值)→ SQL 按 snapshot_date 匹配(日报 start/end=None)。"""
    import datetime as _dt

    q = "昨天的退款明细"
    rows = [_make_report_row(88, 1, "daily", _dt.datetime(2026, 9, 17, 11, 0, tzinfo=_dt.timezone.utc))]
    templates = {1: _make_template(1, "daily_general_morning", ["昨天", "昨日", "退款"])}

    db = MagicMock()
    mappings_mock = MagicMock()
    mappings_mock.mappings.return_value = mappings_mock
    mappings_mock.all.return_value = rows
    db.execute.return_value = mappings_mock
    query_mock = MagicMock()
    query_mock.get.side_effect = lambda tid: templates.get(tid)
    db.query.return_value = query_mock

    matched, _ = match_template_report(
        db, q, "daily", "2026-09-17",   # 单日: win_date=昨日
        win_start=None, win_end=None,
    )
    sql = _select_sql_of(db)
    assert "snapshot_date = :sdate" in sql       # 按快照日期
    assert "r.start <= :q_end" not in sql        # 不走重叠
    assert matched is not None and matched.id == 88


def test_match_interval_window_uses_overlap_sql():
    """区间窗口(win_start<win_end)→ SQL 用报告内容窗口重叠(上周/上月等)。"""
    import datetime as _dt

    q = "上周退款对比"
    rows = [_make_report_row(77, 2, "weekly", _dt.datetime(2026, 9, 13, 10, 0, tzinfo=_dt.timezone.utc))]
    templates = {2: _make_template(2, "weekly_compare_last", ["本周", "上周", "对比", "环比"])}

    db = MagicMock()
    mappings_mock = MagicMock()
    mappings_mock.mappings.return_value = mappings_mock
    mappings_mock.all.return_value = rows
    db.execute.return_value = mappings_mock
    query_mock = MagicMock()
    query_mock.get.side_effect = lambda tid: templates.get(tid)
    db.query.return_value = query_mock

    matched, _ = match_template_report(
        db, q, "weekly", None,                # 区间: win_date=None
        win_start=_dt.date(2026, 9, 7), win_end=_dt.date(2026, 9, 13),  # 上周
    )
    sql = _select_sql_of(db)
    assert "r.start <= :q_end AND r.end >= :q_start" in sql  # 重叠匹配
    assert "snapshot_date = :sdate" not in sql
    assert matched is not None and matched.id == 77


def test_match_start_equals_end_falls_back_to_snapshot_date():
    """#151 单日区间(start==end)→ 匹配层应走 snapshot_date 分支(由 ai.py 回退 win_date)。"""
    import datetime as _dt

    q = "昨天的退款明细"
    # 调用方(ai.py)会把 start==end 回退为 win_date; 这里直接验证回退后行为
    rows = [_make_report_row(88, 1, "daily", _dt.datetime(2026, 9, 18, 11, 0, tzinfo=_dt.timezone.utc))]
    templates = {1: _make_template(1, "daily_general_morning", ["昨天", "昨日", "退款"])}

    db = MagicMock()
    mappings_mock = MagicMock()
    mappings_mock.mappings.return_value = mappings_mock
    mappings_mock.all.return_value = rows
    db.execute.return_value = mappings_mock
    query_mock = MagicMock()
    query_mock.get.side_effect = lambda tid: templates.get(tid)
    db.query.return_value = query_mock

    # 模拟 ai.py 的 _win_date 回退: 单日区间 → win_date=start
    _win_date = _dt.date(2026, 9, 18)   # start==end==09-18 回退
    matched, _ = match_template_report(
        db, q, "daily", _win_date,
        win_start=_dt.date(2026, 9, 18), win_end=_dt.date(2026, 9, 18),
    )
    sql = _select_sql_of(db)
    assert "snapshot_date = :sdate" in sql
    assert matched is not None and matched.id == 88


# ── #181 多维匹配: 体裁意图 + 专项路由 ──────────────────────
def test_question_format_intent():
    """问句体裁意图: 明细侧优先于简报侧; 双命中/未命中 → 中性 None。"""
    assert _question_format_intent("今日退款明细") == "detail"
    assert _question_format_intent("四路分布详细情况") == "detail"
    assert _question_format_intent("今日退款简报") == "brief"
    assert _question_format_intent("整体经营概况") == "brief"
    assert _question_format_intent("本月退款情况如何") is None      # 未命中体裁词
    assert _question_format_intent("今日简报明细都要") is None      # 双侧同命中 → 中性


def test_scope_priority():
    """专项问句: 同专项模板(2) > overview 兜底(1) > 其它专项(0); 泛问全 1。"""
    q_scopes = {"refund"}
    assert _scope_priority("refund", q_scopes) == 2
    assert _scope_priority("overview", q_scopes) == 1
    assert _scope_priority("complaint", q_scopes) == 0
    assert _scope_priority(None, q_scopes) == 1      # 缺省 scope 视为 overview
    # 泛问(未命中任何专项)→ 所有模板平等
    assert _scope_priority("refund", set()) == 1
    assert _scope_priority("complaint", set()) == 1


def test_match_prefers_scope_template_on_hits_tie():
    """#181 核心: 问「今日涉诉情况」时 refund 桶(命中 '涉诉')与 overview 日报(命中 '今日')
    各 1 个 keyword 命中 → 同分时按 scope 路由: refund 桶(scope=refund) 优先于 overview 日报。
    根治"退款/涉诉问句命中整体日报 → feed 截断 → 涉诉答暂无"的错配。
    """
    import datetime as _dt

    q = "今日涉诉情况如何?"
    rows = [
        _make_report_row(500, 10, "daily", _dt.datetime(2026, 9, 17, 11, 0, tzinfo=_dt.timezone.utc)),   # 整体日报
        _make_report_row(501, 11, "daily", _dt.datetime(2026, 9, 17, 10, 30, tzinfo=_dt.timezone.utc)),  # 退款桶
    ]
    templates = {
        10: _make_template(10, "daily_general_morning", ["今天", "今日", "整体", "总览"]),
        11: _make_template(11, "daily_refund_brief", ["退款", "涉诉", "四路", "挽回"], scope="refund"),
    }
    db = MagicMock()
    mappings_mock = MagicMock()
    mappings_mock.mappings.return_value = mappings_mock
    mappings_mock.all.return_value = rows
    db.execute.return_value = mappings_mock
    query_mock = MagicMock()
    query_mock.get.side_effect = lambda tid: templates.get(tid)
    db.query.return_value = query_mock
    db.commit = MagicMock()
    db.expunge = MagicMock()

    matched, tmpl = match_template_report(db, q, "daily", "2026-09-17")
    assert matched is not None
    assert matched.id == 501            # 命中退款桶(scope 路由)
    assert tmpl.code == "daily_refund_brief"


def test_match_format_tiebreak():
    """#181 体裁: 同 scope 同 hits 时, 问句「明细」→ detail 模板优先。"""
    import datetime as _dt

    q = "今日退款明细, 逐笔列出"
    rows = [
        _make_report_row(600, 11, "daily", _dt.datetime(2026, 9, 17, 10, 30, tzinfo=_dt.timezone.utc)),  # 简报桶
        _make_report_row(601, 12, "daily", _dt.datetime(2026, 9, 17, 10, 0, tzinfo=_dt.timezone.utc)),   # 明细桶
    ]
    templates = {
        11: _make_template(11, "daily_refund_brief", ["退款", "涉诉"], scope="refund", format="brief"),
        12: _make_template(12, "daily_refund_detail", ["退款", "涉诉"], scope="refund", format="detail"),
    }
    db = MagicMock()
    mappings_mock = MagicMock()
    mappings_mock.mappings.return_value = mappings_mock
    mappings_mock.all.return_value = rows
    db.execute.return_value = mappings_mock
    query_mock = MagicMock()
    query_mock.get.side_effect = lambda tid: templates.get(tid)
    db.query.return_value = query_mock
    db.commit = MagicMock()
    db.expunge = MagicMock()

    matched, tmpl = match_template_report(db, q, "daily", "2026-09-17")
    assert matched is not None
    assert matched.id == 601            # detail 体裁命中优先
    assert tmpl.format == "detail"


# ── 滑动周/月: 窗口贴合度 Jaccard + 滚动命中裁决 (2026-09-23) ────
def test_window_jaccard_exact_overlap_is_one():
    import datetime as _dt
    d = _dt.date
    assert _window_jaccard(d(2026, 9, 16), d(2026, 9, 22),
                           d(2026, 9, 16), d(2026, 9, 22)) == 1.0


def test_window_jaccard_penalizes_large_window():
    """近7天问句: rolling_7d 贴合度 >> rolling_30d(大窗口并集被拉大, 天然低分)。"""
    import datetime as _dt
    d = _dt.date
    qs, qe = d(2026, 9, 17), d(2026, 9, 23)   # 时间解析的「近7天」(含今天)
    fit_7 = _window_jaccard(d(2026, 9, 16), d(2026, 9, 22), qs, qe)   # 交6/并8
    fit_30 = _window_jaccard(d(2026, 8, 24), d(2026, 9, 22), qs, qe)  # 交6/并31
    assert round(fit_7, 3) == 0.75
    assert round(fit_30, 3) == round(6 / 31, 3)
    assert fit_7 > fit_30


def test_window_jaccard_no_boundary_or_disjoint():
    import datetime as _dt
    d = _dt.date
    assert _window_jaccard(None, None, d(2026, 9, 17), d(2026, 9, 23)) == 0.0  # 单日日报无窗口
    assert _window_jaccard(d(2026, 9, 1), d(2026, 9, 5),
                           d(2026, 9, 10), d(2026, 9, 12)) == 0.0              # 不相交


def test_match_rolling_7d_beats_natural_weekly_on_keyword_tie():
    """问「近7天…环比」: rolling_7d 命中'近7天'、自然 weekly 命中'环比' 各 1 个打平,
    且 win_period 被 effective() 强制成 'weekly'(自然周报 period_match 占优)。

    修复前仅按 keyword→period 排序会误选自然周报(本周短窗); 加入窗口贴合度后
    rolling_7d(Jaccard 0.75) 胜过自然 weekly(本周一~昨天, Jaccard 2/7)。
    rolling_30d 虽重叠但关键词 0 命中, 不参与最终选择。
    """
    import datetime as _dt
    d = _dt.date

    q = "近7天退款环比情况"
    rows = [
        # 自然周报: 本周一(09-21)~昨天(09-22), 与近7天问句仅重叠 2 天
        _make_report_row(71, 2, "weekly",
                         _dt.datetime(2026, 9, 23, 2, 0, tzinfo=_dt.timezone.utc),
                         start=d(2026, 9, 21), end=d(2026, 9, 22)),
        # rolling_7d: 09-16~09-22 截止昨天, 与近7天(含今天 09-17~23)重叠 6 天
        _make_report_row(72, 20, "rolling_7d",
                         _dt.datetime(2026, 9, 23, 2, 5, tzinfo=_dt.timezone.utc),
                         start=d(2026, 9, 16), end=d(2026, 9, 22)),
        # rolling_30d: 大窗口完全包住问句但关键词不命中
        _make_report_row(73, 30, "rolling_30d",
                         _dt.datetime(2026, 9, 23, 2, 10, tzinfo=_dt.timezone.utc),
                         start=d(2026, 8, 24), end=d(2026, 9, 22)),
    ]
    templates = {
        2: _make_template(2, "weekly_compare_last",
                          ["本周", "这周", "周报", "对比", "上周", "环比"]),
        20: _make_template(20, "rolling_7d_compare",
                           ["近7天", "近七天", "最近一周", "近一周", "7天环比"]),
        30: _make_template(30, "rolling_30d_compare",
                           ["近30天", "近一个月", "最近一个月", "30天环比"]),
    }
    db = MagicMock()
    mappings_mock = MagicMock()
    mappings_mock.mappings.return_value = mappings_mock
    mappings_mock.all.return_value = rows
    db.execute.return_value = mappings_mock
    query_mock = MagicMock()
    query_mock.get.side_effect = lambda tid: templates.get(tid)
    db.query.return_value = query_mock
    db.commit = MagicMock()
    db.expunge = MagicMock()

    matched, tmpl = match_template_report(
        db, q, "weekly", None,                 # win_period 被强制 weekly
        win_start=d(2026, 9, 17), win_end=d(2026, 9, 23),
    )
    assert matched is not None
    assert matched.id == 72                    # 窗口贴合的 rolling_7d 胜出
    assert tmpl.code == "rolling_7d_compare"


# ── #308 候选池硬过滤: 停用模板排除 + 单日内容窗口对齐 ──────────────────
#
# 生产事故(2026-10-12): 问「今日退款情况如何？」命中 rolling_30d 快照摘取,
# 用户拿到 30 天口径数字但回显窗口是「今日」。根因两条:
#   ① rolling_7d/30d_compare 已停用(#306), 但存量快照仍在 L1/L2 候选池;
#   ② 单日分支仅按 snapshot_date=当日 取候选, 不做内容窗口对齐。
# 修复: _SQL_TMPL_ACTIVE + _SQL_SINGLE_DAY_FIT 注入全部 5 个 SQL 分支。

import datetime as _dt308  # noqa: E402  段内统一别名, 避免与上文局部 import 混淆

import pytest  # noqa: E402

from app.models.ai_report import AiReport  # noqa: E402
from app.models.ai_report_template import AiReportTemplate  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.ai_report_matcher import list_candidates  # noqa: E402

_308_SNAP = _dt308.date(2026, 8, 1)  # 固定历史快照日, 与测试库当日快照隔离
_308_PREFIX = "t308_"


def _308_purge(db):
    """清理本段用例残留(t308_ 前缀模板 + 其快照)。

    与 gate 文件同理: 命中后 matcher 内部 _bump_matched_count 会 commit,
    命中类用例的 fixture 行会落库, 必须在每个用例前清一遍防相互污染。
    """
    ids = [row[0] for row in db.query(AiReportTemplate.id).filter(
        AiReportTemplate.code.like(f"{_308_PREFIX}%")).all()]
    if not ids:
        return
    db.query(AiReport).filter(AiReport.template_id.in_(ids)).delete(synchronize_session=False)
    db.query(AiReportTemplate).filter(AiReportTemplate.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


@pytest.fixture()
def _308_clean(db):
    """真实 DB 用例入口: 前后各清一次残留。

    后清必要: 命中类用例(_bump_matched_count commit)会把 fixture 行落库,
    仅前清会让最后一个命中用例的残留污染后续测试文件(实测 gate 空壳用例被命中)。
    """
    _308_purge(db)
    yield db
    _308_purge(db)


def _308_user(db):
    u = db.query(User).filter(User.username == "_t308_user").first()
    if u is None:
        u = User(username="_t308_user", display_name="t308",
                 hashed_password="_dummy", role="admin")
        db.add(u)
        db.flush()
    return u


def _308_tmpl(db, code, *, is_active=True, keywords=("退款", "四路")):
    t = AiReportTemplate(
        code=code, name_cn=code, period="weekly", frequency="weekly",
        scope="refund", format="brief", required_dimensions=[],   # 空维度: #192 门槛不拦
        question_template=f"{code} 问句模板", question_keywords=list(keywords),
        is_active=is_active,
    )
    db.add(t)
    db.flush()
    return t


def _308_report(db, template_id, user_id, *, start=None, end=None,
                snapshot_period="weekly", code_q="本周退款专项简报"):
    r = AiReport()
    r.user_id = user_id
    r.template_id = template_id
    r.answer_md = "**四路分布**：A 80 笔\n**涉诉情况**：70.7%\n**退款原因**：充值错误 33 笔\n"
    r.question = code_q
    r.period = snapshot_period
    r.snapshot_date = _308_SNAP
    r.snapshot_period = snapshot_period
    r.is_public = True
    r.matched_count = 0
    r.llm_ms = 0
    r.total_ms = 0
    r.created_at = _dt308.datetime.now(_dt308.timezone.utc)
    r.start = start
    r.end = end
    db.add(r)
    db.flush()
    return r


def test_308_single_day_excludes_multi_day_snapshot(_308_clean):
    db = _308_clean
    """单日问句不得命中多日窗口快照(rolling/weekly/monthly) —— 事故场景本身。

    active 模板 + 当日快照 + 内容窗口 07-26~08-01(7 天):
    L1 match_template_report 与 L2 list_candidates 都必须返空 → 交实时 LLM。"""
    u = _308_user(db)
    t = _308_tmpl(db, "t308_weekly_active")
    _308_report(db, t.id, u.id,
                start=_dt308.date(2026, 7, 26), end=_dt308.date(2026, 8, 1))

    hit, tmpl = match_template_report(db, "今日退款情况如何？四路分布怎么样？",
                                      "daily", "2026-08-01")
    assert hit is None and tmpl is None
    assert list_candidates(db, "daily", "2026-08-01", question="今日退款") == []


def test_308_disabled_template_excluded_l1_and_l2(_308_clean):
    db = _308_clean
    """模板 is_active=False 的存量快照不得再服役(L1/L2 均排除, 含区间分支)。"""
    u = _308_user(db)
    t = _308_tmpl(db, "t308_rolling_disabled", is_active=False,
                  keywords=("近30天", "退款"))
    _308_report(db, t.id, u.id,
                start=_dt308.date(2026, 7, 26), end=_dt308.date(2026, 8, 1),
                snapshot_period="rolling_30d")

    # 单日分支
    hit, tmpl = match_template_report(db, "近30天退款情况", "daily", "2026-08-01")
    assert hit is None and tmpl is None
    # 区间分支(窗口与报告完全重叠, 仅因模板停用被排除)
    hit2, tmpl2 = match_template_report(
        db, "近30天退款情况", "monthly", None,
        win_start=_dt308.date(2026, 7, 26), win_end=_dt308.date(2026, 8, 1))
    assert hit2 is None and tmpl2 is None
    assert list_candidates(db, "monthly", None,
                           win_start=_dt308.date(2026, 7, 26),
                           win_end=_dt308.date(2026, 8, 1),
                           question="近30天退款") == []


def test_308_range_question_still_matches_active_overlap(_308_clean):
    db = _308_clean
    """回归: 区间问句命中 active 模板重叠快照不受影响(weekly 正常服役)。"""
    u = _308_user(db)
    t = _308_tmpl(db, "t308_weekly_range")
    r = _308_report(db, t.id, u.id,
                    start=_dt308.date(2026, 7, 26), end=_dt308.date(2026, 8, 1))

    hit, tmpl = match_template_report(
        db, "本周退款四路情况", "weekly", None,
        win_start=_dt308.date(2026, 7, 26), win_end=_dt308.date(2026, 8, 1))
    assert hit is not None and hit.id == r.id
    assert tmpl.code == "t308_weekly_range"
    cands = list_candidates(db, "weekly", None,
                            win_start=_dt308.date(2026, 7, 26),
                            win_end=_dt308.date(2026, 8, 1),
                            question="本周退款四路情况")
    assert any(c.id == r.id for c in cands)


def test_308_null_window_report_still_matches_single_day(_308_clean):
    db = _308_clean
    """回归: start/end 未落(NULL)的历史快照仍可服务单日问句(与 gate 测试同构)。"""
    u = _308_user(db)
    t = _308_tmpl(db, "t308_weekly_nullwin")
    r = _308_report(db, t.id, u.id)   # start/end 均 None

    hit, _tmpl = match_template_report(db, "退款四路情况", "daily", "2026-08-01")
    assert hit is not None and hit.id == r.id


def test_308_sql_fragments_injected_all_branches():
    """SQL 层断言: 单日分支含窗口对齐+模板过滤; 区间分支含模板过滤且不含单日对齐。"""
    import datetime as _dt

    rows = [_make_report_row(88, 1, "weekly",
                             _dt.datetime(2026, 9, 17, 11, 0, tzinfo=_dt.timezone.utc))]
    templates = {1: _make_template(1, "weekly_x", ["退款"])}

    def _mk_db():
        db = MagicMock()
        mm = MagicMock()
        mm.mappings.return_value = mm
        mm.all.return_value = rows
        db.execute.return_value = mm
        qm = MagicMock()
        qm.get.side_effect = lambda tid: templates.get(tid)
        db.query.return_value = qm
        return db

    db1 = _mk_db()
    match_template_report(db1, "今日退款", "daily", "2026-09-17")
    sql_day = _select_sql_of(db1)
    assert "r.start = :sdate AND r.end = :sdate" in sql_day      # 单日窗口对齐
    assert "ai_report_templates t" in sql_day                    # 模板 active 过滤

    db2 = _mk_db()
    match_template_report(db2, "本周退款", "weekly", None,
                          win_start=_dt.date(2026, 9, 7), win_end=_dt.date(2026, 9, 13))
    sql_range = _select_sql_of(db2)
    assert "r.start <= :q_end AND r.end >= :q_start" in sql_range  # 区间重叠保留
    assert "ai_report_templates t" in sql_range
    assert "r.start = :sdate" not in sql_range                     # 区间不套单日对齐
