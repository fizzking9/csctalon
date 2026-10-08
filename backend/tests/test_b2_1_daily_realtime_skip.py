# -*- coding: utf-8 -*-
"""B2-1 (#236) AI 报告策略调整: daily 改实时生成的跳过逻辑测试。

不依赖 DB 连接 —— 用 Mock 验证:
- schedule_due_templates 跳过 period=daily 模板, 保留 weekly/monthly
- warm_on_sync_complete 跳过 period=daily 模板(20min 节流代码保留)
- warm_on_startup 跳过 period=daily 模板
- ai_report_matcher 的 SQL 含 `snapshot_period != 'daily'` 过滤
"""

import datetime as _dt
import inspect
from unittest.mock import MagicMock, patch

import pytest

from app.services import ai_report_worker
from app.services.ai_report_worker import schedule_due_templates

# 固定测试时间: 2026-10-09 09:00 北京时间(周五, 当月9号)
#   - isoweekday=5(周五) → weekly trigger_at="5-09:00" 命中
#   - day=9             → monthly trigger_at="9-09:00" 命中
#   - HH:MM=09:00       → daily/rolling trigger_at="09:00" 命中
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))
_FIXED_NOW = _dt.datetime(2026, 10, 9, 9, 0, tzinfo=_TZ_CN)


# ── 测试用模板构造器 ──────────────────────────────────────────────────────
def _mk_tmpl(code, period, frequency="daily", trigger_at="09:00",
             refresh_on_sync=False, is_active=True, priority=100):
    """构造一个轻量 AiReportTemplate-like 对象(MagicMock + 属性)。"""
    t = MagicMock()
    t.code = code
    t.period = period
    t.frequency = frequency
    t.trigger_at = trigger_at
    t.refresh_on_sync = refresh_on_sync
    t.is_active = is_active
    t.priority = priority
    t.max_tokens = 2000
    t.name_cn = f"测试-{code}"
    t.question_template = "测试问句"
    t.question_keywords = ["测试"]
    t.required_dimensions = []
    t.scope = "overview"
    t.format = "brief"
    t.llm_provider_code = None
    t.id = abs(hash(code)) % 10000
    return t


# ── 1. schedule_due_templates 跳过 daily ─────────────────────────────────
def test_schedule_due_skips_daily_period(db_mock_with_templates):
    """schedule_due_templates 不返回 period=daily 的模板(B2-1)。"""
    db = db_mock_with_templates
    with patch.object(ai_report_worker, "_now_cn", return_value=_FIXED_NOW):
        due = schedule_due_templates(db)
    codes = [t.code for t in due]
    # daily 模板不应出现
    assert "_test_daily" not in codes, (
        f"period=daily 模板不应被 schedule_due_templates 触发(B2-1), 实际: {codes}"
    )
    # weekly/monthly 仍应出现
    assert "_test_weekly" in codes, f"weekly 模板应正常触发, 实际: {codes}"
    assert "_test_monthly" in codes, f"monthly 模板应正常触发, 实际: {codes}"


def test_schedule_keeps_rolling_7d_daily_frequency(db_mock_with_templates):
    """frequency=daily 但 period=rolling_7d 的模板仍应触发(rolling 不是 daily 报告)。"""
    db = db_mock_with_templates
    with patch.object(ai_report_worker, "_now_cn", return_value=_FIXED_NOW):
        due = schedule_due_templates(db)
    codes = [t.code for t in due]
    assert "_test_rolling_7d" in codes, (
        f"period=rolling_7d 模板应正常触发(只有 period=daily 才跳过), 实际: {codes}"
    )


# ── 2. warm_on_sync_complete 跳过 daily ──────────────────────────────────
def test_warm_on_sync_complete_skips_daily(db_mock_with_templates):
    """warm_on_sync_complete 跳过 period=daily 模板(不调 generate_for_template)。"""
    db = db_mock_with_templates
    with patch.object(ai_report_worker, "generate_for_template") as gen_mock, \
         patch.object(ai_report_worker, "_window_generated_recently", return_value=False), \
         patch.object(ai_report_worker, "evaluate_pregen_skip",
                      return_value={"skip": False, "mode": "off"}), \
         patch.object(ai_report_worker, "SessionLocal", return_value=db), \
         patch.object(ai_report_worker, "record_system_event"):
        ai_report_worker.warm_on_sync_complete()
    # generate_for_template 不应被 daily 模板调用
    called_codes = [c.args[1].code for c in gen_mock.call_args_list]
    assert "_test_daily_sync" not in called_codes, (
        f"period=daily 的 refresh_on_sync 模板不应被预生成(B2-1), 实际调用: {called_codes}"
    )


# ── 3. warm_on_startup 跳过 daily ────────────────────────────────────────
def test_warm_on_startup_skips_daily(db_mock_with_templates):
    """warm_on_startup 跳过 period=daily 模板。"""
    db = db_mock_with_templates
    with patch.object(ai_report_worker, "generate_for_template") as gen_mock, \
         patch.object(ai_report_worker, "_window_generated_recently", return_value=False), \
         patch.object(ai_report_worker, "_template_due_now", return_value=True), \
         patch.object(ai_report_worker, "SessionLocal", return_value=db), \
         patch.object(ai_report_worker, "_now_cn",
                      return_value=__import__("datetime").datetime(2026, 10, 9, 9, 0, tzinfo=__import__("datetime").timezone(__import__("datetime").timedelta(hours=8)))):
        ai_report_worker.warm_on_startup(force=True)
    called_codes = [c.args[1].code for c in gen_mock.call_args_list]
    assert "_test_daily" not in called_codes, (
        f"period=daily 模板不应被启动预热生成(B2-1), 实际调用: {called_codes}"
    )


# ── 4. matcher SQL 含 daily 过滤 ──────────────────────────────────────────
def test_matcher_sql_filters_daily_period():
    """ai_report_matcher 的 SQL 含 `snapshot_period != 'daily'` 过滤(B2-1)。"""
    from app.services import ai_report_matcher
    src = inspect.getsource(ai_report_matcher)
    assert "snapshot_period != 'daily'" in src, (
        "ai_report_matcher SQL 应含 `snapshot_period != 'daily'` 过滤(B2-1 #236)"
    )


def test_worker_skips_daily_in_schedule():
    """ai_report_worker 源码含 period=daily 跳过逻辑(B2-1)。"""
    src = inspect.getsource(ai_report_worker)
    # schedule_due_templates 内的跳过
    assert 'period or "") == "daily"' in src, (
        "ai_report_worker 应含 period=daily 跳过逻辑(B2-1 #236)"
    )


# ── fixture: 用 Mock 模拟 db.query 链 ─────────────────────────────────────
@pytest.fixture()
def db_mock_with_templates():
    """构造一个 Mock db, query().filter().order_by().all() 返回测试模板集。

    模板集:
      - _test_daily: period=daily, frequency=daily → 应被 schedule 跳过
      - _test_weekly: period=weekly, frequency=weekly → 应触发
      - _test_monthly: period=monthly, frequency=monthly → 应触发
      - _test_rolling_7d: period=rolling_7d, frequency=daily → 应触发(非 daily 报告)
      - _test_daily_sync: period=daily, refresh_on_sync=True → 应被 warm_on_sync 跳过
    """
    templates = [
        _mk_tmpl("_test_daily", period="daily", frequency="daily", trigger_at="09:00"),
        _mk_tmpl("_test_weekly", period="weekly", frequency="weekly",
                 trigger_at="5-09:00"),  # 周五9点(对齐 _FIXED_NOW 周五)
        _mk_tmpl("_test_monthly", period="monthly", frequency="monthly",
                 trigger_at="9-09:00"),  # 每月9号9点(对齐 _FIXED_NOW day=9)
        _mk_tmpl("_test_rolling_7d", period="rolling_7d", frequency="daily",
                 trigger_at="09:00"),
        _mk_tmpl("_test_daily_sync", period="daily", frequency="sync-triggered",
                 refresh_on_sync=True, trigger_at=""),
    ]

    db = MagicMock()
    # query(...).filter(...).all() 链式返回(schedule_due_templates 路径)
    # query(...).filter(...).order_by(...).all() 链式返回(warm_on_* 路径)
    query_mock = MagicMock()
    filter_mock = MagicMock()
    order_mock = MagicMock()

    def _all():
        return templates

    # schedule_due_templates: .filter(...).all() 直接调 all
    filter_mock.all = _all
    # warm_on_*: .filter(...).order_by(...).all()
    order_mock.all = _all
    filter_mock.order_by.return_value = order_mock
    query_mock.filter.return_value = filter_mock
    db.query.return_value = query_mock

    # query().get(id) 用于 warm_on_sync_complete 内按 id 取单个模板
    def _get(tid):
        for t in templates:
            if t.id == tid:
                return t
        return None
    query_mock.get = _get

    # db.execute 用于节流查询(_window_generated_recently 已 mock 为 False)
    db.execute.return_value.scalar.return_value = None
    db.commit = MagicMock()
    db.close = MagicMock()
    db.rollback = MagicMock()
    return db
