# -*- coding: utf-8 -*-
"""#204 AI 预生成「无新事件跳过」新鲜度判定测试

对应工单 #193/#204 验收标准 4:
  ① 有/无新事件两分支
  ② 五种保底场景(当日首份 / 超 MAX_STALE / 手动触发 / 判定异常 / 近期失败)
  ③ 判定异常 fail-open
  ④ 三档行为: off 不判定 / dry_run 留痕但照常生成 / enforce 真跳过

evaluate 层用例走 fixture 事务(结束回滚, 无残留); worker 层用例因
record_system_event(commit=True) 会落真实数据, 采用「唯一前缀隔离 + finally 清理」
模式(同 test_ingest_incident_203.py)。
"""

import datetime as _dt
import json
import uuid

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.models.ai_report import AiReport
from app.models.ai_report_template import AiReportTemplate
from app.models.system_event import SystemEvent
from app.models.user import User
from app.services import ai_pregen_freshness as fresh
from app.services import ai_report_worker as worker_mod

_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))
_UTC = _dt.timezone.utc

# 唯一前缀隔离: 多次运行/并行会话下不撞唯一键(code unique), 也便于清理
_RUN = f"t204_{uuid.uuid4().hex[:8]}"


def _mk_user(db) -> User:
    u = User(username=f"{_RUN}_u", display_name="t204测试", hashed_password="x",
             role="admin")
    db.add(u)
    db.flush()
    return u


def _mk_template(db, user, *, scope="refund", suffix="a",
                 period="daily") -> AiReportTemplate:
    t = AiReportTemplate(
        code=f"{_RUN}_{suffix}", name_cn="t204 测试模板", question_template="q",
        # #236 B2-1: period 默认 daily 供纯判定类测试(不经过 worker);
        # 经 warm_on_sync_complete 的端到端测试须传非 daily(如 rolling_7d),
        # 否则 B2-1 会先于新鲜度判定跳过。
        period=period, frequency="sync-triggered", refresh_on_sync=True,
        scope=scope, is_active=True, created_by=user.id if user else None,
    )
    db.add(t)
    db.flush()
    return t


def _mk_snapshot(db, user, template: AiReportTemplate, created_at: _dt.datetime) -> AiReport:
    """当日成功快照(worker 节流/判定都以 is_public=TRUE 快照为水位)。

    created_at 由测试显式指定(TimestampMixin 默认 now 不可控), snapshot_date
    按北京时间取 created_at 当日(与 AGENTS.md 时区铁律一致)。
    """
    r = AiReport(
        user_id=user.id, template_id=template.id,
        snapshot_date=created_at.astimezone(_TZ_CN).strftime("%Y-%m-%d"),
        snapshot_period=template.period, is_public=True,
        question="q", answer_md="ok", llm_model="m", llm_ms=1, total_ms=1,
        created_at=created_at,
    )
    db.add(r)
    db.flush()
    return r


def _today(cn_now: _dt.datetime) -> str:
    return cn_now.astimezone(_TZ_CN).strftime("%Y-%m-%d")


# ── ① 有/无新事件两分支 ────────────────────────────────────────────────


def test_no_new_events_marks_skip(db):
    """窗口内零新增 → skip=True(本可跳过), counts 全 0, 证据带窗口。"""
    user = _mk_user(db)
    t = _mk_template(db, user, scope="refund")
    now = _dt.datetime.now(_UTC)
    _mk_snapshot(db, user, t, now - _dt.timedelta(minutes=30))

    d = fresh.evaluate_pregen_skip(
        db, template_id=t.id, template_code=t.code, scope=t.scope,
        snapshot_period=t.period, snapshot_date=_today(now),
        mode="dry_run", now=now)

    assert d["skip"] is True
    assert d["reason"] == "no_new_events"
    assert d["verdict"]["error"] is None
    assert all(v == 0 for v in d["verdict"]["counts"].values())
    assert d["verdict"]["window_start"] is not None


def test_has_new_events_generates(db):
    """窗口内退费单新增(refund scope 的 A 路源) → 判定有新事件, 不跳过。

    refunds.refund_no 是 DB 层 NOT NULL(迁移建, 模型未标), 用 raw SQL 带 refund_no。
    """
    user = _mk_user(db)
    t = _mk_template(db, user, scope="refund")
    now = _dt.datetime.now(_UTC)
    _mk_snapshot(db, user, t, now - _dt.timedelta(minutes=30))
    db.execute(text("INSERT INTO refunds (refund_no, created_at_raw) VALUES (:no, :t)"),
               {"no": f"{_RUN}_r1", "t": now - _dt.timedelta(minutes=5)})
    db.flush()

    d = fresh.evaluate_pregen_skip(
        db, template_id=t.id, template_code=t.code, scope=t.scope,
        snapshot_period=t.period, snapshot_date=_today(now),
        mode="dry_run", now=now)

    assert d["skip"] is False
    assert d["reason"] == "has_new_events"
    assert d["verdict"]["counts"]["refunds"] >= 1


# ── ② 五种保底场景 ─────────────────────────────────────────────────────


def test_fallback_first_snapshot_today(db):
    """保底①: 当日无快照(首次/跨日) → 必生成, 即使源表零新增。"""
    user = _mk_user(db)
    t = _mk_template(db, user)
    now = _dt.datetime.now(_UTC)

    d = fresh.evaluate_pregen_skip(
        db, template_id=t.id, template_code=t.code, scope=t.scope,
        snapshot_period=t.period, snapshot_date=_today(now),
        mode="enforce", now=now)

    assert d["skip"] is False
    assert d["reason"] == "first_snapshot_today"


def test_fallback_stale_over_limit(db):
    """保底②: 距上次快照 > MAX_STALE_MINUTES → 必生成(窗口时刻不落后)。"""
    user = _mk_user(db)
    t = _mk_template(db, user)
    now = _dt.datetime.now(_UTC)
    _mk_snapshot(db, user, t, now - _dt.timedelta(minutes=200))

    d = fresh.evaluate_pregen_skip(
        db, template_id=t.id, template_code=t.code, scope=t.scope,
        snapshot_period=t.period, snapshot_date=_today(now),
        mode="enforce", max_stale_minutes=120, now=now)

    assert d["skip"] is False
    assert d["reason"] == "stale_over_limit"


def test_fallback_manual_trigger(db):
    """保底③: admin 手动触发(level=1) → 必生成("点了没反应"禁则)。"""
    user = _mk_user(db)
    t = _mk_template(db, user)
    now = _dt.datetime.now(_UTC)
    _mk_snapshot(db, user, t, now - _dt.timedelta(minutes=1))

    d = fresh.evaluate_pregen_skip(
        db, template_id=t.id, template_code=t.code, scope=t.scope,
        snapshot_period=t.period, snapshot_date=_today(now),
        mode="enforce", trigger_level=1, now=now)

    assert d["skip"] is False
    assert d["reason"] == "manual_trigger"


def test_fallback_recent_failure(db):
    """保底⑤: 上次成功快照之后有过生成失败 → 必生成(失败≠刚生成过)。

    反例: 失败事件属于**其他模板**时不影响本模板判定(仍可跳过)。
    """
    user = _mk_user(db)
    t = _mk_template(db, user)
    other = _mk_template(db, user, suffix="b")
    now = _dt.datetime.now(_UTC)
    _mk_snapshot(db, user, t, now - _dt.timedelta(minutes=30))
    _mk_snapshot(db, user, other, now - _dt.timedelta(minutes=30))
    db.add(SystemEvent(
        category="ai", action="pregen_llm_failed", status="failed", level="error",
        title="x",
        detail=json.dumps({"template_id": t.id, "code": t.code})))
    db.flush()

    d = fresh.evaluate_pregen_skip(
        db, template_id=t.id, template_code=t.code, scope=t.scope,
        snapshot_period=t.period, snapshot_date=_today(now),
        mode="enforce", now=now)
    assert d["skip"] is False
    assert d["reason"] == "recent_failure"

    # 失败事件归属他模板(other 无自身失败事件) → 不保底, 照常判定(零新增 → 可跳过)
    d2 = fresh.evaluate_pregen_skip(
        db, template_id=other.id, template_code=other.code, scope=other.scope,
        snapshot_period=other.period, snapshot_date=_today(now),
        mode="enforce", now=now)
    assert d2["skip"] is True
    assert d2["reason"] == "no_new_events"


def test_fail_open_on_judge_error(db, monkeypatch):
    """保底④: 判定查询异常 fail-open → 必生成(不因监控故障停刷新)。"""
    user = _mk_user(db)
    t = _mk_template(db, user)
    now = _dt.datetime.now(_UTC)
    _mk_snapshot(db, user, t, now - _dt.timedelta(minutes=30))

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(fresh, "_count_source", _boom)
    d = fresh.evaluate_pregen_skip(
        db, template_id=t.id, template_code=t.code, scope=t.scope,
        snapshot_period=t.period, snapshot_date=_today(now),
        mode="enforce", now=now)

    assert d["skip"] is False
    assert d["reason"] == "judge_error"


# ── 三档开关与灰度 ─────────────────────────────────────────────────────


def test_mode_off_no_judgment(db):
    """mode=off: 不判定(回滚位) —— 即使无快照也不产生判定结论。"""
    user = _mk_user(db)
    t = _mk_template(db, user)
    now = _dt.datetime.now(_UTC)

    d = fresh.evaluate_pregen_skip(
        db, template_id=t.id, template_code=t.code, scope=t.scope,
        snapshot_period=t.period, snapshot_date=_today(now), mode="off", now=now)

    assert d["skip"] is False
    assert d["reason"] == "mode_off"
    assert d["verdict"] is None


def test_scope_disabled_not_judged(db):
    """灰度白名单外(如 reception 未启用且未登记事件源) → 不判定不跳过。"""
    user = _mk_user(db)
    t = _mk_template(db, user, scope="reception")
    now = _dt.datetime.now(_UTC)
    _mk_snapshot(db, user, t, now - _dt.timedelta(minutes=30))

    d = fresh.evaluate_pregen_skip(
        db, template_id=t.id, template_code=t.code, scope=t.scope,
        snapshot_period=t.period, snapshot_date=_today(now),
        mode="enforce", now=now)

    assert d["skip"] is False
    assert d["reason"] == "scope_disabled"


# ── 管理端 4 字段(纯后端口径; 前端列见 AiTemplates.vue 设计说明) ────────


def test_admin_template_list_refresh_stats(db):
    """list_templates 附带刷新健康度 4 字段, 与跳过事件 detail 对账。"""
    from app.api.v1 import admin_ai

    user = _mk_user(db)
    t = _mk_template(db, user)
    now = _dt.datetime.now(_UTC)
    _mk_snapshot(db, user, t, now - _dt.timedelta(minutes=30))

    resp = admin_ai.list_templates(db, None)
    row = next(r for r in resp.data if r["id"] == t.id)
    assert row["last_generated_at"] is not None
    assert row["hit_count_7d"] == 0
    assert row["skip_count_7d"] == 0
    assert row["last_skip_verdict"] is None

    # 写入一条跳过事件(手工构造, 与 record_pregen_skipped_event 同一 detail 契约;
    # 走 fixture 事务不 commit, 避免真实残留) → skip_count/verdict 可查
    db.add(SystemEvent(
        category="ai", action="pregen_skipped", status="info", title="x",
        detail=json.dumps({
            "template": t.code, "template_id": t.id, "scope": "refund",
            "window": [None, None], "counts": {"refunds": 0}, "has_new": False,
            "mode": "dry_run", "reason": "no_new_events", "enforced": False})))
    db.flush()

    resp2 = admin_ai.list_templates(db, None)
    row2 = next(r for r in resp2.data if r["id"] == t.id)
    assert row2["skip_count_7d"] == 1
    v = row2["last_skip_verdict"]
    assert v["reason"] == "no_new_events"
    assert v["has_new"] is False
    assert v["counts"] == {"refunds": 0}


# ── ④ worker 三档端到端(留痕 + 行为) ──────────────────────────────────


def _cleanup(db, user_id: int, template_id: int, code: str) -> None:
    """worker 用例真实落库后的清理(幂等, finally 调用)。"""
    db.execute(text(
        "DELETE FROM system_events WHERE category='ai' AND action='pregen_skipped' "
        "AND detail LIKE :p"), {"p": f"%{code}%"})
    db.execute(text("DELETE FROM ai_reports WHERE template_id = :tid"),
               {"tid": template_id})
    db.execute(text("DELETE FROM ai_report_templates WHERE id = :tid"),
               {"tid": template_id})
    db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    db.commit()


@pytest.mark.parametrize("mode,expect_generate", [("off", True), ("dry_run", True),
                                                  ("enforce", False)])
def test_worker_three_modes(db, monkeypatch, mode, expect_generate):
    """三档端到端: off/dry_run 照常生成, enforce 跳过; dry_run/enforce 都留痕。

    - monkeypatch worker.SessionLocal → fixture session(worker 内部自建会话,
      复用同一事务便于断言与清理)
    - generate_for_template / classify_after_sync 打桩(不真调 LLM)
    - 快照 30min 前: > 20min 不节流, < 120min 不触发 stale 保底
    """
    user = _mk_user(db)
    # #236 B2-1: 端到端走 warm_on_sync_complete, 模板须非 daily(否则 B2-1 先跳过,
    # 测不到三档新鲜度判定)。用 rolling_7d + refresh_on_sync=True 模拟随同步刷新。
    t = _mk_template(db, user, period="rolling_7d")
    now = _dt.datetime.now(_UTC)
    _mk_snapshot(db, user, t, now - _dt.timedelta(minutes=30))
    # commit 会使 ORM 实例过期, worker 内部还会 close() 会话 → 先取标量
    uid, tid, code = user.id, t.id, t.code
    db.commit()

    calls: list[int] = []
    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker_mod, "generate_for_template",
                        lambda _db, tmpl, **kw: calls.append(tmpl.id))
    monkeypatch.setattr(worker_mod, "classify_after_sync", lambda: None)
    monkeypatch.setattr(settings, "AI_PREGEN_SKIP_MODE", mode)

    try:
        worker_mod.warm_on_sync_complete()

        ev = db.execute(text(
            "SELECT detail FROM system_events WHERE category='ai' "
            "AND action='pregen_skipped' AND detail LIKE :p"),
            {"p": f"%{code}%"}).all()
        if mode == "off":
            # off: 行为与改造前一致, 无任何判定留痕
            # (成员断言: 测试库里若残留其他 refresh_on_sync 模板不影响判定)
            assert tid in calls
            assert ev == []
            return

        if expect_generate:
            assert tid in calls
        else:
            assert tid not in calls
        assert len(ev) == 1
        detail = json.loads(ev[0][0])
        # 证据 JSON 契约(check_ai_issue_gates ③ / 管理端 4 列都按此对账)
        assert detail["template"] == code
        assert detail["scope"] == "refund"
        assert detail["reason"] == "no_new_events"
        assert detail["has_new"] is False
        assert detail["mode"] == mode
        assert detail["enforced"] is (mode == "enforce")
        assert set(detail["counts"].keys()) == {
            "refunds", "wallet_withdrawals", "shanmao_refunds", "manual_refunds"}
    finally:
        _cleanup(db, uid, tid, code)
