# -*- coding: utf-8 -*-
"""E 路现金流数据源切换测试(2026-09-05, #20)

背景: E 路对账基准从 merchant_refunds(旧 Excel 快照) 切换到 refund_record_reports
(BMS 退款记录报表, 每天采集)。旧表保留(供 matcher/wide 关联), 但 E 路统计不再读它。

覆盖:
1. _cash_routes_total 的 actual_cashflow 从 refund_record_reports 取数(不再读 merchant_refunds)
2. E 路只统计 refund_status ∈ ('退款成功','成功','SUCCESS'); 其它状态(退款中/失败)不计入
3. 时间窗过滤生效(refund_time 落在窗口内才计入)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

_TZ = timezone(timedelta(hours=8))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=_TZ)


@pytest.fixture()
def _test_session(_engine):
    """独立 session, 清理 E 路相关表(避免污染其它共享表测试)"""
    from app.models import Device

    session = Session(_engine, autocommit=False, autoflush=False)
    from app.models import Refund, RefundRecordReport

    for cls in (RefundRecordReport, Refund):
        session.execute(cls.__table__.delete())
    session.execute(Device.__table__.delete().where(Device.sn.like("SN-E-%")))
    session.commit()
    try:
        yield session
    finally:
        try:
            for cls in (RefundRecordReport, Refund):
                session.execute(cls.__table__.delete())
            session.execute(Device.__table__.delete().where(Device.sn.like("SN-E-%")))
            session.commit()
        except Exception:
            session.rollback()
        session.rollback()
        session.close()


def _ensure_device(session, sn):
    from app.models import Device

    if not session.execute(select(Device).where(Device.sn == sn)).scalar_one_or_none():
        session.add(Device(sn=sn, merchant_name="测试商户"))
        session.commit()


def _seed_report(session, sn, amount, refund_time, status="退款成功", merchant_id="1611876437"):
    from app.models import RefundRecordReport

    _ensure_device(session, sn)
    r = RefundRecordReport(
        platform_order_no=f"WS-E-{sn}-{amount}",
        device_sn=sn,
        merchant_id=merchant_id,
        merchant_name="测试商户",
        refund_time=_dt(refund_time),
        refund_amount=amount,
        refund_status=status,
    )
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def test_route_e_reads_refund_record_reports(_test_session):
    """E 路(actual_cashflow)从 refund_record_reports 取数"""
    from app.services.analytics import _cash_routes_total

    # 窗口: 2026-09-01 ~ 2026-09-10
    s = _dt("2026-09-01 00:00:00")
    e = _dt("2026-09-10 23:59:59")
    # 2 笔退款成功
    _seed_report(_test_session, "SN-E-1", "19.00", "2026-09-05 10:00:00")
    _seed_report(_test_session, "SN-E-2", "31.50", "2026-09-06 14:00:00")

    res = _cash_routes_total(_test_session, start=s, end=e)
    ac = res["actual_cashflow"]
    assert ac["count"] == 2
    assert abs(ac["amount"] - 50.50) < 0.01


def test_route_e_filters_non_success_status(_test_session):
    """E 路只统计退款成功; 退款中/失败不计入"""
    from app.services.analytics import _cash_routes_total

    s = _dt("2026-09-01 00:00:00")
    e = _dt("2026-09-10 23:59:59")
    _seed_report(_test_session, "SN-E-3", "19.00", "2026-09-05 10:00:00", status="退款成功")
    _seed_report(_test_session, "SN-E-4", "50.00", "2026-09-05 11:00:00", status="退款中")  # 不计
    _seed_report(_test_session, "SN-E-5", "80.00", "2026-09-05 12:00:00", status="失败")     # 不计

    res = _cash_routes_total(_test_session, start=s, end=e)
    ac = res["actual_cashflow"]
    assert ac["count"] == 1
    assert abs(ac["amount"] - 19.00) < 0.01


def test_route_e_time_window(_test_session):
    """E 路按 refund_time 时间窗过滤"""
    from app.services.analytics import _cash_routes_total

    s = _dt("2026-09-01 00:00:00")
    e = _dt("2026-09-05 23:59:59")
    _seed_report(_test_session, "SN-E-6", "19.00", "2026-09-04 10:00:00")  # 窗口内
    _seed_report(_test_session, "SN-E-7", "99.00", "2026-09-06 10:00:00")  # 窗口外

    res = _cash_routes_total(_test_session, start=s, end=e)
    ac = res["actual_cashflow"]
    assert ac["count"] == 1
    assert abs(ac["amount"] - 19.00) < 0.01
