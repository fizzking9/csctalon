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
def _make_report_row(rid, template_id, snapshot_period, created_at):
    """构造 SQL row dict (模拟 db.execute().mappings().all() 返回)。"""
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
