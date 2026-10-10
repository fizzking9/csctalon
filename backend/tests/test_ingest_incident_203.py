# -*- coding: utf-8 -*-
"""工单 #203: 熔断事件 Alert+Incident 自动登记 + 恢复平息 的测试。

覆盖:
- ingest_incident_service.register_ingest_incident: 登记 + 幂等(同源同业务日一次)
- ingest_incident_service.close_ingest_incidents: 平息留痕 + 二次平息幂等(0 条)
- bms_scheduler._register_circuit_alert 接线: 熔断 → Incident 出现(站内信同事务落库)
- bms_scheduler._close_circuit_incident 接线: 恢复 → Incident 平息(全链路
  「熔断→Incident 出现→恢复→平息」用真调度器方法走通)

隔离策略: 用独立 session(被测方法内部即独立 session+commit), 业务日用
2099-xx 占位 + 唯一 data_type 前缀, finally 清理, 不污染其它用例。
"""

import datetime as _dt
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.notification import Notification
from app.services.ingest_incident_service import (
    close_ingest_incidents,
    register_ingest_incident,
)

_BIZ_KEY = "2099-01-01"
_DT_PREFIX = "ut_circuit_"   # 唯一 data_type 前缀, 便于清理


def _dt_code() -> str:
    return f"{_DT_PREFIX}{uuid.uuid4().hex[:8]}"


def _cleanup(engine):
    """按测试前缀清理 Incident 与 Notification(独立连接, 真实提交)。"""
    from sqlalchemy.orm import Session

    s = Session(engine)
    try:
        inc_ids = s.scalars(
            select(Incident.id).where(
                Incident.created_by == "system-alert",
                Incident.detail.ilike(f"%{_BIZ_KEY}%"),
                Incident.title.ilike(f"%{_DT_PREFIX}%"),
            )
        ).all()
        if inc_ids:
            s.execute(delete(Incident).where(Incident.id.in_(inc_ids)))
            s.execute(delete(Notification).where(
                Notification.ref_type == "incident",
                Notification.ref_id.in_([str(i) for i in inc_ids]),
            ))
        s.execute(delete(Notification).where(
            Notification.ref_type == "alert",
            Notification.ref_id.ilike(f"{_DT_PREFIX}%"),
        ))
        s.commit()
    finally:
        s.close()


@pytest.fixture()
def _clean(_engine):
    _cleanup(_engine)
    yield
    _cleanup(_engine)


# ── service 层: 登记 + 幂等 ─────────────────────────────────────────────
def test_register_creates_incident_and_idempotent(db):
    code = _dt_code()
    iid = register_ingest_incident(
        db, metric_code=code, period_key=_BIZ_KEY,
        detail=f"测试登记\n数据源: {code}\n业务日: {_BIZ_KEY}",
        now=_dt.datetime(2099, 1, 1, 8, 0, tzinfo=_dt.timezone.utc),
    )
    assert iid is not None
    row = db.get(Incident, iid)
    assert row.category == "数据采集"
    assert row.created_by == "system-alert"
    assert code in row.title and _BIZ_KEY in row.detail
    assert row.status == "registered" and not row.is_closed
    assert row.severity == "高"          # metric_value=None → 熔断级为高

    # 幂等: 同源同业务日再登记 → None
    again = register_ingest_incident(
        db, metric_code=code, period_key=_BIZ_KEY, detail=f"重复登记 {_BIZ_KEY}")
    assert again is None


# ── service 层: 平息 + 二次平息幂等 ─────────────────────────────────────
def test_close_calms_incident_once(db):
    code = _dt_code()
    iid = register_ingest_incident(
        db, metric_code=code, period_key=_BIZ_KEY, detail=f"待平息 {_BIZ_KEY}")
    assert iid is not None

    now = _dt.datetime(2099, 1, 1, 12, 0, tzinfo=_dt.timezone.utc)
    n = close_ingest_incidents(db, metric_code=code, period_key=_BIZ_KEY,
                               now=now, note="测试平息")
    assert n == 1
    row = db.get(Incident, iid)
    assert row.status == "calmed"
    assert row.is_closed and row.closed_at is not None
    assert "测试平息" in (row.review_note or "")

    # 二次平息: 已闭环的不动 → 0 条
    assert close_ingest_incidents(db, metric_code=code, period_key=_BIZ_KEY,
                                  now=now, note="二次平息") == 0


# ── bms_scheduler 接线: 熔断 → Incident 出现 → 恢复 → 平息(全链路) ───────
def test_circuit_alert_register_then_recovery_calms(_engine, _clean):
    """实测「熔断→Incident 出现→恢复→Incident 平息」全链路(方案 A 验收)。"""
    from sqlalchemy.orm import Session

    from app.services.bms_scheduler import BmsScheduler

    code = _dt_code()
    sched = BmsScheduler.__new__(BmsScheduler)   # 不跑 __init__(只测接线方法)

    # 1) 熔断触发 → Incident 登记 + 站内信(同事务落库)
    sched._register_circuit_alert(_BIZ_KEY, code)
    s = Session(_engine)
    try:
        rows = s.scalars(select(Incident).where(
            Incident.created_by == "system-alert",
            Incident.title.ilike(f"%{code}%"),
            Incident.detail.ilike(f"%{_BIZ_KEY}%"),
        )).all()
        assert len(rows) == 1, f"熔断应登记 1 条 Incident, 实际 {len(rows)}"
        inc = rows[0]
        assert inc.status == "registered" and not inc.is_closed
        # 站内信同事务落库(#203 修复: 原未 commit 即 close 丢失)
        notes = s.scalars(select(Notification).where(
            Notification.ref_type == "alert", Notification.ref_id == code)).all()
        assert notes, "熔断站内信应随同事务落库"
    finally:
        s.close()

    # 2) 幂等: 同源同业务日重复熔断登记不重复
    sched._register_circuit_alert(_BIZ_KEY, code)
    s = Session(_engine)
    try:
        cnt = len(s.scalars(select(Incident).where(
            Incident.created_by == "system-alert",
            Incident.title.ilike(f"%{code}%"))).all())
        assert cnt == 1, f"幂等: 应仍为 1 条, 实际 {cnt}"
    finally:
        s.close()

    # 3) 恢复(半开探测成功路径) → Incident 平息
    sched._close_circuit_incident(_BIZ_KEY, code, note="半开探测恢复自动平息")
    s = Session(_engine)
    try:
        inc2 = s.get(Incident, inc.id)
        assert inc2.status == "calmed" and inc2.is_closed
        assert "半开探测恢复自动平息" in (inc2.review_note or "")
    finally:
        s.close()


def test_reset_halt_calms_circuit_incidents(_engine, _clean, monkeypatch):
    """手动清除熔断(reset_halt) → 平息当日全部已熔断源的 Incident。"""
    from app.services import circuit_breaker as cb
    from app.services.bms_scheduler import BmsScheduler

    code = _dt_code()
    # 时间固定到业务日 _BIZ_KEY(hour>=4 → biz=2099-01-01)
    import app.services.bms_scheduler as bs

    class _FakeDT(_dt.datetime):
        def now(cls, tz=None):  # noqa: N805
            return _dt.datetime(2099, 1, 1, 10, 0, tzinfo=tz or _dt.timezone.utc)

    monkeypatch.setattr(bs, "datetime", _FakeDT)
    # reset_halt 内 reset_circuit(circuit_breaker) 无状态要求, 直接调
    monkeypatch.setattr(cb, "reset_circuit", lambda *a, **k: None)
    # bms_scheduler 导入的是 from ... import reset_circuit(名字绑定), 需替换其模块内引用
    monkeypatch.setattr(bs, "reset_circuit", lambda *a, **k: None)

    sched = BmsScheduler.__new__(BmsScheduler)
    sched._halted_types = {}
    # 先登记(直接调 service, 走独立 session; 用与 reset_halt 相同的 biz_key)
    from app.services.ingest_incident_service import register_ingest_incident
    s0 = Session(_engine)
    try:
        assert register_ingest_incident(
            s0, metric_code=code, period_key=_BIZ_KEY, detail=f"手动恢复场景 {_BIZ_KEY}")
        s0.commit()
    finally:
        s0.close()

    sched._halted_types[_BIZ_KEY] = {code}
    sched.reset_halt()

    s = Session(_engine)
    try:
        rows = s.scalars(select(Incident).where(
            Incident.created_by == "system-alert",
            Incident.title.ilike(f"%{code}%"))).all()
        assert len(rows) == 1
        assert rows[0].status == "calmed"
        assert "手动恢复熔断自动平息" in (rows[0].review_note or "")
        assert sched._halted_types.get(_BIZ_KEY) == set()
    finally:
        s.close()
