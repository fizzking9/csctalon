# -*- coding: utf-8 -*-
"""工单 #294: 今日值班看板排除采集类事件 + 补数成功后自动收口采集告警/事件。

覆盖:
- 修复A: analytics.today.today_queue 不返回 category='数据采集' 的系统告警,
  普通业务异常事件照常返回(与时序轴 markers/timeline 口径一致)。
- 修复B: ingest_health_reconcile.recover_arrival_alerts
  · 窗口模式: 仅收口指定业务日的 pending/confirmed 告警, 同时平息
    告警键(data_arrival_<dt>)与熔断键(<dt>)两条 Incident;
  · 快照模式(period_keys=None): 全量快照通道成功 → 收口该源全部历史业务日;
  · 已 recovered / 非 data_arrival 告警不动; 窗口外业务日不动;
  · exact_title 精确匹配: refund 不误关 shanmao_refund 等子串源;
  · 二次执行 0 条(幂等)。
- calm_ingest_incidents_after_sync: 独立 session 集成, 返回计数 dict。

隔离: 业务日 2099-01 + 唯一 data_type 前缀, 独立 session 真实提交, finally 清理。
"""

import datetime as _dt
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.incident import Incident
from app.models.notification import Notification
from app.services.ingest_incident_service import (
    close_ingest_incidents,
    register_ingest_incident,
)

_UTC = _dt.timezone.utc
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))
_DAY1 = "2099-01-01"
_DAY2 = "2099-01-02"
_DAY_OUT = "2099-01-05"
_DT_PREFIX = "ut294_"


def _code() -> str:
    return f"{_DT_PREFIX}{uuid.uuid4().hex[:8]}"


def _period_start(biz_day: str) -> _dt.datetime:
    """上海业务日 00:00 对应的 UTC 时间(上海 00:00 = UTC 前一日 16:00)。"""
    sh = _dt.datetime.fromisoformat(biz_day).replace(tzinfo=_TZ_CN)
    return sh.astimezone(_UTC)


def _cleanup(engine):
    """清理本工单测试产生的 Alert / Incident / 通知(独立连接, 真实提交)。"""
    s = Session(engine)
    try:
        s.execute(delete(Alert).where(Alert.metric_code.like(f"%{_DT_PREFIX}%")))
        inc_ids = s.scalars(
            select(Incident.id).where(Incident.title.like("UT294-%"))
        ).all()
        auto_ids = s.scalars(
            select(Incident.id).where(
                Incident.created_by == "system-alert",
                Incident.title.ilike(f"%{_DT_PREFIX}%"),
            )
        ).all()
        ids = list(set(inc_ids) | set(auto_ids))
        if ids:
            s.execute(delete(Incident).where(Incident.id.in_(ids)))
            s.execute(delete(Notification).where(
                Notification.ref_type == "incident",
                Notification.ref_id.in_([str(i) for i in ids]),
            ))
        s.commit()
    finally:
        s.close()


@pytest.fixture()
def _clean(_engine):
    _cleanup(_engine)
    yield
    _cleanup(_engine)


def _make_alert(s: Session, metric_code: str, biz_day: str,
                status: str = "pending") -> Alert:
    a = Alert(
        title=f"{metric_code} 数据到达健康异常: 该源当日无数据到达",
        level="critical",
        status=status,
        metric_code=metric_code,
        occurred_at=_dt.datetime(2099, 1, 1, 5, 0, tzinfo=_UTC),
        period_start=_period_start(biz_day),
        period_end=_period_start(biz_day) + _dt.timedelta(days=1),
        data_ok=True,
    )
    s.add(a)
    return a


def _make_ingest_incident(s: Session, metric_code: str, biz_day: str) -> Incident:
    """直接插入系统样式采集 Incident(绕过 register 的 ilike 子串撞键:
    告警键 data_arrival_<code> 与裸键 <code> 互为子串, register 无法各登记一条,
    但历史上两种 title 都可能存在, 收口逻辑必须都能处理)。"""
    inc = Incident(
        title=f"数据采集异常: {metric_code} {biz_day}",
        subject=f"{metric_code} 采集异常",
        detail=f"测试登记 业务日:{biz_day} 指标:{metric_code}",
        category="数据采集", department="数据采集",
        status="registered", is_closed=False, is_recurring=False,
        severity="高", event_type="异常事故", created_by="system-alert",
        occurred_at=_period_start(biz_day),
    )
    s.add(inc)
    return inc


# ── 修复 A: today_queue 排除数据采集类事件 ─────────────────────────────
def test_today_queue_excludes_ingest_category(_engine, _clean):
    """业务看板队列只显示业务事件; category='数据采集' 的运维告警不返回。"""
    from app.services.analytics.today._queue import today_queue

    s = Session(_engine)
    try:
        s.add(Incident(
            title="UT294-数据采集异常: refund 2099-01-01",
            category="数据采集", department="数据采集",
            status="registered", is_closed=False, is_recurring=False,
            severity="高", event_type="异常事故", created_by="system-alert",
            occurred_at=_dt.datetime(2099, 1, 1, 9, 0, tzinfo=_TZ_CN),
        ))
        s.add(Incident(
            title="UT294-业务侧突发投诉激增",
            category="运营", status="registered", is_closed=False,
            is_recurring=False, severity="中", event_type="异常事故",
            created_by="tester",
            occurred_at=_dt.datetime(2099, 1, 1, 10, 0, tzinfo=_TZ_CN),
        ))
        s.add(Incident(
            title="UT294-历史无分类事件",
            category=None, status="registered", is_closed=False,
            is_recurring=False, severity="低", event_type="异常事故",
            created_by="tester",
            occurred_at=_dt.datetime(2099, 1, 1, 11, 0, tzinfo=_TZ_CN),
        ))
        s.commit()
    finally:
        s.close()

    qs = Session(_engine)
    try:
        queue = today_queue(qs, date=_DAY1)
    finally:
        qs.close()

    titles = [i["title"] for i in queue["incidents"]]
    assert "UT294-数据采集异常: refund 2099-01-01" not in titles
    assert "UT294-业务侧突发投诉激增" in titles
    assert "UT294-历史无分类事件" in titles


# ── 修复 B: 窗口模式收口 ──────────────────────────────────────────────
def test_recover_by_period_closes_alert_and_both_incident_keys(_engine, _clean):
    from app.services.ingest_health_reconcile import recover_arrival_alerts

    code = _code()
    sibling = f"shanmao_{_code()}"
    now = _dt.datetime(2099, 1, 2, 2, 0, tzinfo=_UTC)

    s = Session(_engine)
    try:
        # 窗口内(DAY1): 一条告警 + 告警链/熔断链两条 Incident
        _make_alert(s, f"data_arrival_{code}", _DAY1)
        _make_ingest_incident(s, f"data_arrival_{code}", _DAY1)
        _make_ingest_incident(s, code, _DAY1)
        # 窗口外业务日: 不应被动
        _make_alert(s, f"data_arrival_{code}", _DAY_OUT)
        _make_ingest_incident(s, code, _DAY_OUT)
        # 子串源同日: exact_title 下不应被连带
        _make_alert(s, f"data_arrival_{sibling}", _DAY1)
        _make_ingest_incident(s, sibling, _DAY1)
        s.commit()
    finally:
        s.close()

    rs = Session(_engine)
    try:
        result = recover_arrival_alerts(
            rs, data_types=[code], period_keys={_DAY1}, now=now,
            note="补数收口测试(#294)")
        rs.commit()
    finally:
        rs.close()

    assert result == {"recovered_alerts": 1, "closed_incidents": 2}, result

    v = Session(_engine)
    try:
        alert_day1 = v.scalars(select(Alert).where(
            Alert.metric_code == f"data_arrival_{code}",
            Alert.period_start == _period_start(_DAY1))).one()
        assert alert_day1.status == "recovered"
        assert alert_day1.recovered_at == now

        for mc in (f"data_arrival_{code}", code):
            inc = v.scalars(select(Incident).where(
                Incident.title == f"数据采集异常: {mc} {_DAY1}")).one()
            assert inc.is_closed and inc.status == "calmed"
            assert "补数收口测试(#294)" in (inc.review_note or "")

        # 窗口外告警/事件不动
        alert_out = v.scalars(select(Alert).where(
            Alert.metric_code == f"data_arrival_{code}",
            Alert.period_start == _period_start(_DAY_OUT))).one()
        assert alert_out.status == "pending" and alert_out.recovered_at is None
        inc_out = v.scalars(select(Incident).where(
            Incident.title == f"数据采集异常: {code} {_DAY_OUT}")).one()
        assert not inc_out.is_closed

        # 子串源不动
        sib_alert = v.scalars(select(Alert).where(
            Alert.metric_code == f"data_arrival_{sibling}")).one()
        assert sib_alert.status == "pending"
        sib_inc = v.scalars(select(Incident).where(
            Incident.title == f"数据采集异常: {sibling} {_DAY1}")).one()
        assert not sib_inc.is_closed
    finally:
        v.close()

    # 幂等: 二次执行 0
    again = Session(_engine)
    try:
        result2 = recover_arrival_alerts(
            again, data_types=[code], period_keys={_DAY1}, now=now,
            note="二次(应为0)")
        again.commit()
    finally:
        again.close()
    assert result2 == {"recovered_alerts": 0, "closed_incidents": 0}


# ── 修复 B: 快照模式收口全部历史业务日 + confirmed 也收 ───────────────
def test_recover_snapshot_mode_all_periods_and_confirmed(_engine, _clean):
    from app.services.ingest_health_reconcile import recover_arrival_alerts

    code = _code()
    s = Session(_engine)
    try:
        _make_alert(s, f"data_arrival_{code}", _DAY1, status="pending")
        _make_alert(s, f"data_arrival_{code}", _DAY2, status="confirmed")
        _make_ingest_incident(s, f"data_arrival_{code}", _DAY1)
        _make_ingest_incident(s, f"data_arrival_{code}", _DAY2)
        # 已 recovered 的历史行: 不应被改动
        done = _make_alert(s, f"data_arrival_{code}", "2099-01-03",
                          status="recovered")
        done.recovered_at = _dt.datetime(2099, 1, 3, 1, 0, tzinfo=_UTC)
        # 非 data_arrival 告警: 不动(带 ut294_ 前缀以便 cleanup 清理)
        _make_alert(s, f"{_DT_PREFIX}other_metric", _DAY1)
        s.commit()
    finally:
        s.close()

    rs = Session(_engine)
    try:
        result = recover_arrival_alerts(
            rs, data_types=[code], period_keys=None,
            now=_dt.datetime(2099, 1, 2, 3, 0, tzinfo=_UTC),
            note="快照收口测试(#294)")
        rs.commit()
    finally:
        rs.close()

    assert result["recovered_alerts"] == 2
    assert result["closed_incidents"] == 2

    v = Session(_engine)
    try:
        statuses = {
            a.period_start.astimezone(_TZ_CN).strftime("%Y-%m-%d"): a.status
            for a in v.scalars(select(Alert).where(
                Alert.metric_code == f"data_arrival_{code}"))
        }
        assert statuses == {_DAY1: "recovered", _DAY2: "recovered",
                            "2099-01-03": "recovered"}
        other = v.scalars(select(Alert).where(
            Alert.metric_code == f"{_DT_PREFIX}other_metric")).one()
        assert other.status == "pending"
    finally:
        v.close()


# ── 修复 B: calm 集成(独立 SessionLocal 提交) ─────────────────────────
def test_calm_after_sync_integration_own_session(_engine, _clean):
    from app.services.sync_task_manager import calm_ingest_incidents_after_sync

    code = _code()
    s = Session(_engine)
    try:
        _make_alert(s, f"data_arrival_{code}", _DAY1)
        register_ingest_incident(
            s, metric_code=f"data_arrival_{code}", period_key=_DAY1,
            detail=f"calm 集成测试 周期:{_DAY1}")
        s.commit()
    finally:
        s.close()

    # 补跑窗口=上海 2099-01-01 当天(枚举前后扩 1 天, 覆盖 DAY1)
    start = _dt.datetime(2099, 1, 1, 8, 0, tzinfo=_TZ_CN)
    end = _dt.datetime(2099, 1, 1, 18, 0, tzinfo=_TZ_CN)
    result = calm_ingest_incidents_after_sync([code], {code: (start, end)})
    assert result["recovered_alerts"] == 1
    assert result["closed_incidents"] >= 1

    # 空入参
    assert calm_ingest_incidents_after_sync([], None) == {
        "recovered_alerts": 0, "closed_incidents": 0}


# ── exact_title 精确匹配单元(默认 ilike 行为保持不变) ─────────────────
def test_close_exact_title_distinguishes_substring_sources(db):
    code = _code()
    sibling = f"shanmao_{code}"
    register_ingest_incident(
        db, metric_code=code, period_key=_DAY1, detail=f"精确匹配测试 {_DAY1}")
    register_ingest_incident(
        db, metric_code=sibling, period_key=_DAY1, detail=f"子串源测试 {_DAY1}")
    db.flush()

    now = _dt.datetime(2099, 1, 1, 12, 0, tzinfo=_UTC)
    n_exact = close_ingest_incidents(
        db, metric_code=code, period_key=_DAY1, now=now,
        note="精确模式", exact_title=True)
    assert n_exact == 1
    sibling_row = db.scalars(select(Incident).where(
        Incident.title == f"数据采集异常: {sibling} {_DAY1}")).one()
    assert not sibling_row.is_closed
