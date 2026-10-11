# -*- coding: utf-8 -*-
"""工单 #294: 今日值班看板排除采集类事件 + 补跑成功按窗口平息采集事件。

覆盖:
- 修复A: analytics.today.today_queue 不返回 category='数据采集' 的系统告警,
  普通业务异常事件照常返回(与时序轴 markers/timeline 口径一致)。
- 修复B: sync_task_manager.calm_ingest_incidents_after_sync
  · 按补跑窗口覆盖的业务日平息事件(熔断键 <dt> 与告警键 data_arrival_<dt> 都覆盖)
  · 窗口外业务日的事件不动
  · exact_title 精确匹配: refund 不误关 shanmao_refund 等子串源
  · 二次执行 0 条(幂等); 无成功源/无窗口直接返回 0

隔离: 业务日 2099-01 + 唯一 data_type 前缀, 独立 session 真实提交, finally 清理。
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

_BIZ_DAY = "2099-01-01"
_TZ_CN = _dt.timezone(_dt.timedelta(hours=8))
_DT_PREFIX = "ut294_"


def _code() -> str:
    return f"{_DT_PREFIX}{uuid.uuid4().hex[:8]}"


def _cleanup(engine):
    """清理本工单测试产生的 Incident 及其通知(独立连接, 真实提交)。"""
    s = Session(engine)
    try:
        inc_ids = s.scalars(
            select(Incident.id).where(
                Incident.created_by == "system-alert",
                Incident.title.ilike(f"%{_DT_PREFIX}%"),
            )
        ).all()
        # 直接插入的手工事件也按 title 前缀清
        manual_ids = s.scalars(
            select(Incident.id).where(Incident.title.like("UT294-%"))
        ).all()
        ids = list(set(inc_ids) | set(manual_ids))
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


# ── 修复 A: today_queue 排除数据采集类事件 ─────────────────────────────
def test_today_queue_excludes_ingest_category(_engine, _clean):
    """业务看板队列只显示业务事件; category='数据采集' 的运维告警不返回。"""
    from app.services.analytics.today._queue import today_queue

    s = Session(_engine)
    try:
        # 1) 系统自动登记的采集类事件(未闭环) —— 应被排除
        s.add(Incident(
            title="UT294-数据采集异常: refund 2099-01-01",
            category="数据采集",
            department="数据采集",
            status="registered",
            is_closed=False,
            is_recurring=False,
            severity="高",
            event_type="异常事故",
            created_by="system-alert",
            occurred_at=_dt.datetime(2099, 1, 1, 9, 0, tzinfo=_TZ_CN),
        ))
        # 2) 普通业务异常事件(未闭环) —— 应照常返回
        s.add(Incident(
            title="UT294-业务侧突发投诉激增",
            category="运营",
            status="registered",
            is_closed=False,
            is_recurring=False,
            severity="中",
            event_type="异常事故",
            created_by="tester",
            occurred_at=_dt.datetime(2099, 1, 1, 10, 0, tzinfo=_TZ_CN),
        ))
        # 3) category=NULL 的历史事件 —— 兼容保留, 应返回
        s.add(Incident(
            title="UT294-历史无分类事件",
            category=None,
            status="registered",
            is_closed=False,
            is_recurring=False,
            severity="低",
            event_type="异常事故",
            created_by="tester",
            occurred_at=_dt.datetime(2099, 1, 1, 11, 0, tzinfo=_TZ_CN),
        ))
        s.commit()
    finally:
        s.close()

    qs = Session(_engine)
    try:
        queue = today_queue(qs, date=_BIZ_DAY)
    finally:
        qs.close()

    titles = [i["title"] for i in queue["incidents"]]
    assert "UT294-数据采集异常: refund 2099-01-01" not in titles
    assert "UT294-业务侧突发投诉激增" in titles
    assert "UT294-历史无分类事件" in titles


# ── 修复 B: 补跑成功按窗口平息 ────────────────────────────────────────
def test_calm_after_sync_by_window_with_exact_title(_engine, _clean):
    from app.services.sync_task_manager import calm_ingest_incidents_after_sync

    refund = _code()                 # 如 ut294_ab12cd34
    shanmao = f"shanmao_{_code()}"   # 名字含 refund 子串的另一源

    s = Session(_engine)
    try:
        # 熔断链路登记键: <data_type>
        assert register_ingest_incident(
            s, metric_code=refund, period_key=_BIZ_DAY,
            detail=f"熔断登记 业务日:{_BIZ_DAY}")
        # 告警链路登记键: data_arrival_<data_type>
        assert register_ingest_incident(
            s, metric_code=f"data_arrival_{refund}", period_key=_BIZ_DAY,
            detail=f"采集健康告警 周期:{_BIZ_DAY}")
        # 窗口外(2099-01-05)的同源事件 —— 不应被本次平息
        assert register_ingest_incident(
            s, metric_code=refund, period_key="2099-01-05",
            detail="窗口外业务日:2099-01-05")
        # 子串源同日事件 —— exact_title 下不应被 refund 连带平息
        assert register_ingest_incident(
            s, metric_code=shanmao, period_key=_BIZ_DAY,
            detail=f"另一子串源 业务日:{_BIZ_DAY}")
        s.commit()
    finally:
        s.close()

    # 补跑窗口: 仅 2099-01-01 当天(枚举会前后各扩 1 天 → 12-31/01-01/01-02)
    start = _dt.datetime(2099, 1, 1, 8, 0, tzinfo=_TZ_CN)
    end = _dt.datetime(2099, 1, 1, 18, 0, tzinfo=_TZ_CN)
    n = calm_ingest_incidents_after_sync([refund], {refund: (start, end)})
    assert n == 2, f"应平息熔断键+告警键共 2 条, 实际 {n}"

    s2 = Session(_engine)
    try:
        def _row(code, day):
            return s2.scalars(select(Incident).where(
                Incident.title == f"数据采集异常: {code} {day}")).one()

        calmed = _row(refund, _BIZ_DAY)
        assert calmed.status == "calmed" and calmed.is_closed
        assert "补跑同步成功自动平息(#294)" in (calmed.review_note or "")

        calmed_alert = _row(f"data_arrival_{refund}", _BIZ_DAY)
        assert calmed_alert.status == "calmed" and calmed_alert.is_closed

        # 窗口外事件保持未闭环
        out_of_window = _row(refund, "2099-01-05")
        assert not out_of_window.is_closed and out_of_window.status == "registered"

        # 子串源事件不被误关
        sibling = _row(shanmao, _BIZ_DAY)
        assert not sibling.is_closed and sibling.status == "registered"
    finally:
        s2.close()

    # 幂等: 二次执行(已闭环的不动) → 0
    assert calm_ingest_incidents_after_sync([refund], {refund: (start, end)}) == 0


def test_calm_after_sync_no_success_types_is_noop(_engine, _clean):
    from app.services.sync_task_manager import calm_ingest_incidents_after_sync

    assert calm_ingest_incidents_after_sync([], None) == 0
    # 有成功源但无窗口映射 → 不平息
    assert calm_ingest_incidents_after_sync(["whatever"], {}) == 0


def test_close_exact_title_distinguishes_substring_sources(db):
    """exact_title=True 时等值匹配, 子串源互不关联; 默认 ilike 行为保持不变。"""
    code = _code()
    sibling = f"shanmao_{code}"
    register_ingest_incident(
        db, metric_code=code, period_key=_BIZ_DAY, detail=f"精确匹配测试 {_BIZ_DAY}")
    register_ingest_incident(
        db, metric_code=sibling, period_key=_BIZ_DAY, detail=f"子串源测试 {_BIZ_DAY}")
    db.flush()

    now = _dt.datetime(2099, 1, 1, 12, 0, tzinfo=_dt.timezone.utc)
    # 精确模式: 只关 code 自己 1 条
    n_exact = close_ingest_incidents(
        db, metric_code=code, period_key=_BIZ_DAY, now=now,
        note="精确模式", exact_title=True)
    assert n_exact == 1
    sibling_row = db.scalars(select(Incident).where(
        Incident.title == f"数据采集异常: {sibling} {_BIZ_DAY}")).one()
    assert not sibling_row.is_closed
