# -*- coding: utf-8 -*-
"""AI 报告调度器凌晨触发测试 (工单 #133 批次 A2) + 启动预热分流测试 (工单 #166)。

覆盖:
- schedule_due_templates 在凌晨 02:01(周一) + trigger_at=1-02:00 能匹配到 weekly 模板
- schedule_due_templates 在凌晨 02:01(每月 1 号) + trigger_at=1-02:00 能匹配到 monthly 模板
- schedule_due_templates 在凌晨 02:01 + daily 模板 trigger_at=09:00 **不**误触发
- warm_idle 函数源码不再含 _DAYTIME_HOURS 时段限制(凌晨可触发)
- #166 warm_on_startup 分流: 未到点的周报/月报不生成; 已到点才生成;
  同一快照窗口刚生成过则节流跳过
- #236 (B2-1, 2026-10-09): daily 报告改实时生成 —— warm_on_startup 不再预生成
  period=daily 模板(改走 L2 实时); 节流测试改用 weekly 模板覆盖
- #306 (2026-10-12): weekly/monthly 触发日当天漏跑当补 —— ±1 分钟容差放宽为
  「触发日当天时刻已达」+ 当日成功公共快照幂等去重; warm_on_startup 同口径

固定 datetime 注入, 避免测试随时钟漂移。
"""

import datetime as _dt
import inspect

import pytest

from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate
from app.models.user import User
from app.services import ai_report_worker
from app.services.ai_report_worker import (
    schedule_due_templates,
    warm_idle,
    warm_on_startup,
)


@pytest.fixture()
def user1(db):
    """创建 user(ai_reports.user_id FK 需要)。

    测试事务内创建, 随 db fixture 回滚。AiReport 显式引用该 user.id。
    """
    u = User(
        username="_test_user1",
        display_name="测试用户1",
        hashed_password="_test_dummy_hash_not_used",
        role="admin",
    )
    db.add(u)
    db.flush()
    return u


# ── fixture: 一个 weekly 模板 trigger_at=1-02:00(每周一凌晨2点) ──────────
@pytest.fixture()
def weekly_midnight_template(db):
    """插入 weekly 模板, trigger_at=1-02:00(每周一凌晨2点)。"""
    t = AiReportTemplate(
        code="_test_weekly_midnight",
        name_cn="测试-周报凌晨",
        question_template="生成上周接待/退款/投诉对比分析",
        question_keywords=["周报", "周对比"],
        period="weekly",
        frequency="weekly",
        trigger_at="1-02:00",   # 每周一凌晨2点(#133 调度)
        priority=100,
        max_tokens=2000,
        is_active=True,
        visibility_scope="public",
    )
    db.add(t)
    db.flush()
    return t


@pytest.fixture()
def monthly_midnight_template(db):
    """插入 monthly 模板, trigger_at=1-02:00(每月1号凌晨2点)。"""
    t = AiReportTemplate(
        code="_test_monthly_midnight",
        name_cn="测试-月报凌晨",
        question_template="生成上月接待/退款/投诉总览",
        question_keywords=["月报", "月度总览"],
        period="monthly",
        frequency="monthly",
        trigger_at="1-02:00",   # 每月1号凌晨2点(#133 调度)
        priority=100,
        max_tokens=2000,
        is_active=True,
        visibility_scope="public",
    )
    db.add(t)
    db.flush()
    return t


@pytest.fixture()
def daily_morning_template(db):
    """插入 daily 模板, trigger_at=09:00(白天, 不应在凌晨误触发)。"""
    t = AiReportTemplate(
        code="_test_daily_morning",
        name_cn="测试-日报白天",
        question_template="生成今日经营总览",
        question_keywords=["日报", "今日"],
        period="daily",
        frequency="daily",
        trigger_at="09:00",
        priority=100,
        max_tokens=2000,
        is_active=True,
        visibility_scope="public",
    )
    db.add(t)
    db.flush()
    return t


def _patch_now(monkeypatch, dt: _dt.datetime):
    """把 ai_report_worker._now_cn() 注入到固定时间。"""
    tz = _dt.timezone(_dt.timedelta(hours=8))
    fixed = dt.replace(tzinfo=tz)
    monkeypatch.setattr(ai_report_worker, "_now_cn", lambda: fixed)


# ── schedule_due_templates: 凌晨匹配 ───────────────────────────────────────
def test_weekly_midnight_due_on_monday_0201(
        db, monkeypatch, weekly_midnight_template):
    """周一凌晨 02:01, trigger_at=1-02:00 → 应被匹配(±1 分钟容差)。"""
    # 2026-09-14 是周一
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 2, 1))
    due = schedule_due_templates(db)
    codes = [t.code for t in due]
    assert "_test_weekly_midnight" in codes, (
        f"周一凌晨 02:01 应匹配到 weekly trigger_at=1-02:00; 实际 due={codes}"
    )


def test_monthly_midnight_due_on_first_day_0201(
        db, monkeypatch, monthly_midnight_template):
    """每月 1 号凌晨 02:01, trigger_at=1-02:00 → 应被匹配。"""
    # 2026-09-01 是 1 号(且是周二, 不与 weekly 冲突)
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 1, 2, 1))
    due = schedule_due_templates(db)
    codes = [t.code for t in due]
    assert "_test_monthly_midnight" in codes, (
        f"每月1号凌晨 02:01 应匹配到 monthly trigger_at=1-02:00; 实际 due={codes}"
    )


def test_daily_morning_not_triggered_at_midnight(
        db, monkeypatch, daily_morning_template):
    """凌晨 02:01, daily trigger_at=09:00 → **不**应被匹配(避免凌晨误触发)。"""
    # 2026-09-16 是周三, 凌晨 02:01
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 16, 2, 1))
    due = schedule_due_templates(db)
    codes = [t.code for t in due]
    assert "_test_daily_morning" not in codes, (
        f"凌晨 02:01 不应误触发 daily trigger_at=09:00; 实际 due={codes}"
    )


# ── warm_idle: 凌晨不再被时段限制挡掉 ─────────────────────────────────────
def test_warm_idle_source_no_daytime_hours_guard():
    """warm_idle 函数源码不得含 _DAYTIME_HOURS 时段限制(#133 移除)。

    若该限制回流(如未来误改), 此用例先于生产暴露。
    """
    src = inspect.getsource(warm_idle)
    assert "_DAYTIME_HOURS" not in src, (
        "warm_idle 仍引用 _DAYTIME_HOURS —— #133 应已移除白天时段限制, "
        "否则凌晨 trigger_at 无法被扫描到。"
    )


def test_warm_idle_runs_at_midnight(monkeypatch):
    """warm_idle 在凌晨 02:01 不会被提前 return(应进入 schedule_due_templates)。

    通过 monkeypatch _now_cn 到凌晨, mock schedule_due_templates 返回空列表
    (避免真实 LLM 调用), 断言 schedule_due_templates 被调用——即 warm_idle 没有
    在时段判断处提前 return。
    """
    from unittest.mock import MagicMock

    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 2, 1))  # 周一凌晨

    called = {"count": 0}

    def _fake_schedule(_db):  # 参数签名匹配 warm_idle 调用, 不需要实际使用
        called["count"] += 1
        return []   # 空列表避免触发生成

    monkeypatch.setattr(ai_report_worker, "schedule_due_templates", _fake_schedule)
    # mock 一个具备 .close() 的假 session, 避免 finally 段抛 AttributeError
    monkeypatch.setattr(ai_report_worker, "SessionLocal", lambda: MagicMock())

    warm_idle()

    assert called["count"] == 1, (
        "warm_idle 在凌晨应进入 schedule_due_templates 扫描, "
        "若被时段限制提前 return 则不会调用 schedule_due_templates"
    )


# ══════════════════════════════════════════════════════════════════════════
# 工单 #166: warm_on_startup 只补「当前该有的报告」
#
# 背景: 原实现遍历全部 active 模板无条件生成 → 每次服务重启就把周报/月报重算一遍
# (生产一天 7~21 次)。修复后: 周报/月报只在 trigger_at 到点时生成 + 快照窗口节流。
# ══════════════════════════════════════════════════════════════════════════

class _SessionProxy:
    """转发测试 session 的全部属性, 但 close() 置空。

    warm_on_startup 走自己的 `SessionLocal()`(独立会话, 看不到 db fixture 的
    未提交数据), 测试需把会话指向 db fixture; 而它的 finally 会 close() —— 不能
    真关掉测试会话, 故用代理吞掉 close。
    """

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):  # noqa: D102  见类 docstring
        pass


@pytest.fixture()
def startup_calls(monkeypatch, db):
    """拦截 warm_on_startup 的会话与生成动作, 返回「实际生成的模板 id 列表」。

    - SessionLocal → 测试 session 代理(能看到 fixture 内未提交的模板/报告行)
    - generate_for_template → 记录调用(避免真实 LLM 与 commit)
    """
    calls: list[int] = []

    def _fake_generate(_db, tmpl, *, level=2):
        calls.append(tmpl.id)
        return {"report_id": -1}

    monkeypatch.setattr(ai_report_worker, "SessionLocal", lambda: _SessionProxy(db))
    monkeypatch.setattr(ai_report_worker, "generate_for_template", _fake_generate)
    return calls


def _add_template(db, *, code, period, frequency, trigger_at,
                  refresh_on_sync=False) -> AiReportTemplate:
    """插入一个 active 模板(测试事务内, fixture 结束回滚)。"""
    t = AiReportTemplate(
        code=code, name_cn=f"测试-{code}", question_template="生成分析",
        period=period, frequency=frequency, trigger_at=trigger_at,
        refresh_on_sync=refresh_on_sync, priority=100, max_tokens=2000,
        is_active=True, visibility_scope="public",
    )
    db.add(t)
    db.flush()
    return t


def test_warm_on_startup_skips_weekly_not_due(db, monkeypatch, startup_calls):
    """#166 验收 1: trigger_at 未到点的 weekly 模板, 启动预热**不生成**。

    2026-09-16 是周三 10:30, 模板 trigger_at=1-02:00(周一凌晨) → 未到点。
    """
    t = _add_template(db, code="_test_166_weekly_nd", period="weekly",
                      frequency="weekly", trigger_at="1-02:00")
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 16, 10, 30))

    warm_on_startup(force=True)

    assert t.id not in startup_calls, (
        "未到点的周报不应在服务启动时生成(原 bug: 每次重启全量重算)"
    )


def test_warm_on_startup_skips_monthly_not_due(db, monkeypatch, startup_calls):
    """#166 验收 1(月报): trigger_at 未到点的 monthly 模板, 启动预热**不生成**。

    2026-09-16 是 16 号, 模板 trigger_at=1-02:00(每月1号) → 未到点。
    """
    t = _add_template(db, code="_test_166_monthly_nd", period="monthly",
                      frequency="monthly", trigger_at="1-02:00")
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 16, 10, 30))

    warm_on_startup(force=True)

    assert t.id not in startup_calls, "未到点的月报不应在服务启动时生成"


def test_warm_on_startup_generates_weekly_when_due(db, monkeypatch, startup_calls):
    """#166 验收 1(正例): trigger_at **到点**时, 启动预热正常生成周报。

    2026-09-14 是周一 02:01, 与 trigger_at=1-02:00 在 ±1 分钟容差内 → 到点。
    (凌晨不在白天时段, 故 force=True —— 与 cache_warmer 的启动调用一致。)
    """
    t = _add_template(db, code="_test_166_weekly_due", period="weekly",
                      frequency="weekly", trigger_at="1-02:00")
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 2, 1))

    warm_on_startup(force=True)

    assert t.id in startup_calls, "到点的周报应被启动预热生成(否则会漏掉这份报告)"


def test_warm_on_startup_skips_daily_after_b2_1(
        db, monkeypatch, startup_calls):
    """#236 (B2-1): daily 报告改实时生成 —— 启动预热不再生成 period=daily 模板。

    旧行为(#166): 日报(refresh_on_sync=True)启动即生成, 保证重启后当日报告新鲜。
    B2-1: daily 改走 L2 实时(用户等几秒), warm_on_startup 一律跳过 period=daily,
    20min 节流代码保留但不再服务于 daily。
    """
    t = _add_template(db, code="_test_b21_daily", period="daily",
                      frequency="daily", trigger_at="09:00", refresh_on_sync=True)
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 16, 14, 0))

    warm_on_startup(force=True)

    assert t.id not in startup_calls, (
        "B2-1: period=daily 模板不应再被启动预热(改走实时 L2), "
        f"实际 startup_calls={startup_calls}"
    )


def test_warm_on_startup_throttles_same_snapshot_window(
        db, monkeypatch, startup_calls, user1):
    """#166 验收 2: 同一快照窗口已生成过 → 节流跳过(不调 LLM)。

    #236 B2-1: daily 已被前置跳过, 本用例改用 weekly 模板(到点)覆盖节流路径 ——
    weekly 模板 trigger_at=1-02:00, 当前周一 02:01 到点; 但今日已有
    (period=weekly, snapshot_date=今天) 的公共报告 → 启动预热应节流跳过。
    """
    t = _add_template(db, code="_test_166_throttle", period="weekly",
                      frequency="weekly", trigger_at="1-02:00")
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 2, 1))  # 周一凌晨02:01, 到点
    # 造一份「同窗口刚生成」的公共报告(created_at 由 server_default=now() 落当前时刻)
    db.add(AiReport(
        task_id=None, user_id=user1.id, question="上周数据如何?", answer_md="md",
        llm_model="test-model", llm_ms=1, total_ms=1,
        template_id=t.id, snapshot_period="weekly", snapshot_date="2026-09-14",
        is_public=True,
    ))
    db.flush()

    warm_on_startup(force=True)

    assert t.id not in startup_calls, (
        "同一快照窗口 20min 内已生成过, 启动预热应节流跳过(防重启风暴)"
    )


# ══════════════════════════════════════════════════════════════════════════
# 工单 #306: weekly/monthly 触发日当天漏跑当补 + 当日快照幂等去重
#
# 背景: 旧判定 trigger_at 仅 ±1 分钟容差, 发布重启错过周一/1号 02:00 即缺整周/
# 整月报告且永不补(生产 10-09~10-11 连续三天夜间未跑成)。放宽为「触发日当天
# 时刻已达即 due」, 再以当日成功公共快照去重: 无快照→补, 已成功→不重算。
# 跨触发日不补(compute_pregen_window 仅触发日当天才是完整上周/上月窗口)。
# ══════════════════════════════════════════════════════════════════════════

def _add_public_snapshot(db, user, template, *, period, snapshot_date):
    """造一份当日成功公共预生成报告(仅 success 才会 is_public=TRUE 落库)。"""
    db.add(AiReport(
        task_id=None, user_id=user.id, question="周期报告?", answer_md="md",
        llm_model="test-model", llm_ms=1, total_ms=1,
        template_id=template.id, snapshot_period=period,
        snapshot_date=snapshot_date, is_public=True,
    ))
    db.flush()


def test_weekly_catchup_due_monday_morning_without_snapshot(
        db, monkeypatch, weekly_midnight_template):
    """#306: 周一上午 10:30(已错过 02:00 ±1 分钟)且当日无快照 → 仍 due(漏跑当补)。"""
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 10, 30))  # 周一
    due = schedule_due_templates(db)
    assert "_test_weekly_midnight" in [t.code for t in due], (
        "触发日当天错过 trigger 时刻后应持续可补(无快照时), 旧 ±1 分钟判定会漏报"
    )


def test_weekly_not_due_before_trigger_time(
        db, monkeypatch, weekly_midnight_template):
    """#306: 周一 01:58(trigger 02:00 未到, 超出 -1min 下界容差) → 不 due。"""
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 1, 58))
    assert "_test_weekly_midnight" not in [t.code for t in schedule_due_templates(db)]


def test_weekly_not_due_off_trigger_day(
        db, monkeypatch, weekly_midnight_template):
    """#306: 周三上午(非触发日)即使无快照也不 due —— 跨日补会导致周报窗口漂移。"""
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 16, 10, 30))
    assert "_test_weekly_midnight" not in [t.code for t in schedule_due_templates(db)]


def test_weekly_catchup_deduped_when_snapshot_exists(
        db, monkeypatch, weekly_midnight_template, user1):
    """#306: 周一上午且当日已有成功公共快照(02:00 跑过/早些时候已补) → 不重复 due。"""
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 10, 30))
    _add_public_snapshot(
        db, user1, weekly_midnight_template,
        period="weekly", snapshot_date="2026-09-14")
    assert "_test_weekly_midnight" not in [t.code for t in schedule_due_templates(db)], (
        "触发日当天快照已存在时必须幂等去重, 否则每次 warm_idle 都重算"
    )


def test_monthly_catchup_due_first_day_without_snapshot(
        db, monkeypatch, monthly_midnight_template):
    """#306: 每月1号上午(错过 02:00)无快照 → due。"""
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 1, 10, 30))  # 1号周二
    assert "_test_monthly_midnight" in [t.code for t in schedule_due_templates(db)]


def test_monthly_not_due_after_first_day(
        db, monkeypatch, monthly_midnight_template):
    """#306: 每月2号不再补月报(非触发日, 月报退化窗口仅1号当天成立)。"""
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 2, 10, 30))
    assert "_test_monthly_midnight" not in [t.code for t in schedule_due_templates(db)]


def test_monthly_catchup_deduped_when_snapshot_exists(
        db, monkeypatch, monthly_midnight_template, user1):
    """#306: 1号上午当月报快照已存在 → 不重复 due。"""
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 1, 10, 30))
    _add_public_snapshot(
        db, user1, monthly_midnight_template,
        period="monthly", snapshot_date="2026-09-01")
    assert "_test_monthly_midnight" not in [t.code for t in schedule_due_templates(db)]


def test_warm_on_startup_catches_up_missed_weekly(
        db, monkeypatch, startup_calls):
    """#306: 触发日白天发布重启、当日无快照 → 启动预热直接补跑周报。"""
    t = _add_template(db, code="_test_306_weekly_catchup", period="weekly",
                      frequency="weekly", trigger_at="1-02:00")
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 10, 30))  # 周一上午

    warm_on_startup(force=True)

    assert t.id in startup_calls, "错过 02:00 后触发日当天重启应补跑, 不能缺整周报告"


def test_warm_on_startup_skips_caught_up_weekly(
        db, monkeypatch, startup_calls, user1):
    """#306: 触发日重启但当日快照已存在(warm_idle 已补/02:00 已跑) → 不重算。"""
    t = _add_template(db, code="_test_306_weekly_done", period="weekly",
                      frequency="weekly", trigger_at="1-02:00")
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 10, 30))
    _add_public_snapshot(db, user1, t, period="weekly", snapshot_date="2026-09-14")

    warm_on_startup(force=True)

    assert t.id not in startup_calls, "当日已成功生成的周报不应因重启重算"


def test_warm_on_startup_throttle_is_period_scoped(
        db, monkeypatch, startup_calls, user1):
    """#166 节流按「快照窗口」判, 不跨 period 误伤。

    模板 period=weekly, 库里只有 (period=daily, 今天) 的报告 → 不构成同窗口,
    启动预热(到点时)仍应生成。
    """
    t = _add_template(db, code="_test_166_period_scope", period="weekly",
                      frequency="weekly", trigger_at="1-02:00")
    _patch_now(monkeypatch, _dt.datetime(2026, 9, 14, 2, 1))
    db.add(AiReport(
        task_id=None, user_id=user1.id, question="今日数据如何?", answer_md="md",
        llm_model="test-model", llm_ms=1, total_ms=1,
        template_id=t.id, snapshot_period="daily", snapshot_date="2026-09-14",
        is_public=True,
    ))
    db.flush()

    warm_on_startup(force=True)

    assert t.id in startup_calls, (
        "节流判据是 (template_id, snapshot_period, snapshot_date) 三元组; "
        "只有 daily 窗口的报告不应挡掉 weekly 报告"
    )
