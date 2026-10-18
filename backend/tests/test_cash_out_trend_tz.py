# -*- coding: utf-8 -*-
"""现金流趋势按日分桶时区测试(2026-09-05 修复, #20)

背景: cash_out_trend / operation_trend / wallet_vs_withdraw 的 A/B/E 路日分桶
曾用 `to_char(col, 'YYYY-MM-DD')` 无 AT TIME ZONE → 按 UTC 截日; C/D 路用
`timezone('Asia/Shanghai', col)` → 按北京截日。五路日归属基准分裂。

修复: A/B/E 路统一 `to_char(timezone('Asia/Shanghai', col), 'YYYY-MM-DD')`,
与 C/D 路及决策概览 _day 口径一致(北京日)。

覆盖:
1. E 路 refund_record_reports.refund_time 北京 00:30(=UTC 前一日 16:30) 归北京当天
2. A 路 refunds.created_at_raw 北京 00:30 归北京当天(不落 UTC 前一天)
3. 北京 23:30(=UTC 当日 15:30) 仍归北京当天
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

# #332 T8: A/E 路取数 std 表 —— 测试内 seed 原始行后需跑采纳作业固化进 std
from app.services.governance.refund_adoption import run_refund_adoption
from tests._db_guard import safe_wipe

_TZ = timezone(timedelta(hours=8))


def _bj(s: str) -> datetime:
    """北京时间字面 → aware (+08:00)"""
    return datetime.fromisoformat(s).replace(tzinfo=_TZ)


@pytest.fixture()
def _test_session(_engine):
    session = Session(_engine, autocommit=False, autoflush=False)
    from app.models import Device, Refund, RefundRecordReport
    from app.models.refund_standard import StdRefundOnline, StdRefundRecord

    for cls in (RefundRecordReport, Refund):
        safe_wipe(session, cls)
    # #332 T8: std 同步清空(采纳作业全量重建, 防 std 残留污染绝对计数)
    safe_wipe(session, StdRefundOnline)
    safe_wipe(session, StdRefundRecord)
    safe_wipe(session, Device, where=Device.sn.like("SN-TZ-%"))
    session.commit()
    try:
        yield session
    finally:
        try:
            for cls in (RefundRecordReport, Refund):
                safe_wipe(session, cls)
            safe_wipe(session, StdRefundOnline)
            safe_wipe(session, StdRefundRecord)
            safe_wipe(session, Device, where=Device.sn.like("SN-TZ-%"))
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


def _seed_report(session, sn, amount, refund_time, status="退款成功"):
    """refund_time 参数为北京时间 aware(入库存 UTC)"""
    from app.models import RefundRecordReport

    _ensure_device(session, sn)
    r = RefundRecordReport(
        platform_order_no=f"WS-TZ-{sn}-{amount}",
        device_sn=sn,
        merchant_id="1611876437",
        merchant_name="测试商户",
        refund_time=refund_time,  # aware +8 → PG timestamptz 存等价 UTC
        refund_amount=amount,
        refund_status=status,
    )
    session.add(r)
    session.commit()
    return r


def _seed_refund_a(session, sn, amount, created_at_raw):
    """A 路退费单(线上直退)"""
    from app.models import Refund

    _ensure_device(session, sn)
    r = Refund(
        refund_no=f"RF-TZ-{sn}-{amount}",
        device_sn=sn,
        refund_amount=float(amount),
        refund_method="线上",
        status="已退款",
        created_at_raw=created_at_raw,
    )
    session.add(r)
    session.commit()
    return r


def _day_map_of(rows):
    """cash_out_trend 返回 [{date, direct, withdraw, ..., merchant}] → {date: {..}}"""
    return {r["date"]: r for r in rows}


def test_e_route_bj_midnight_buckets_to_bj_day(_test_session):
    """E 路: 北京 2026-09-05 00:30(=UTC 09-04 16:30) 应归 2026-09-05, 不落 09-04"""
    from app.services.analytics import cash_out_trend

    # 北京 09-05 00:30 → aware(+8) 入库即 UTC 09-04 16:30
    bj_midnight = _bj("2026-09-05 00:30:00")
    _seed_report(_test_session, "SN-TZ-E1", "19.00", bj_midnight)
    run_refund_adoption(_test_session, routes="E", commit=False)

    start, end = _bj("2026-09-01 00:00:00"), _bj("2026-09-10 00:00:00")
    rows = cash_out_trend(_test_session, start, end)
    m = _day_map_of(rows)
    # 北京日 09-05 应含该笔; UTC 截日(09-04)不应有
    assert m.get("2026-09-05", {}).get("merchant", 0) == 19.00
    assert m.get("2026-09-04", {}).get("merchant", 0) == 0.0


def test_e_route_bj_evening_stays_bj_day(_test_session):
    """E 路: 北京 09-05 23:30(=UTC 09-05 15:30) 归 09-05 (UTC 与北京同日, 边界仍一致)"""
    from app.services.analytics import cash_out_trend

    bj_evening = _bj("2026-09-05 23:30:00")
    _seed_report(_test_session, "SN-TZ-E2", "31.50", bj_evening)
    run_refund_adoption(_test_session, routes="E", commit=False)

    start, end = _bj("2026-09-01 00:00:00"), _bj("2026-09-10 00:00:00")
    rows = cash_out_trend(_test_session, start, end)
    m = _day_map_of(rows)
    assert m.get("2026-09-05", {}).get("merchant", 0) == 31.50


def test_a_route_bj_midnight_buckets_to_bj_day(_test_session):
    """A 路: 北京 09-05 00:30 退费单归 09-05, 不落 09-04"""
    from app.services.analytics import cash_out_trend

    bj_midnight = _bj("2026-09-05 00:30:00")
    _seed_refund_a(_test_session, "SN-TZ-A1", "50.00", bj_midnight)
    run_refund_adoption(_test_session, routes="A", commit=False)

    start, end = _bj("2026-09-01 00:00:00"), _bj("2026-09-10 00:00:00")
    rows = cash_out_trend(_test_session, start, end)
    m = _day_map_of(rows)
    assert m.get("2026-09-05", {}).get("direct", 0) == 50.00
    assert m.get("2026-09-04", {}).get("direct", 0) == 0.0


def test_naive_param_treated_as_bj_window(_test_session):
    """naive 入参按北京窗口(2026-09-06 修复): 前端传 YYYY-MM-DD(naive),
    北京 9/1 00:30 的退款(UTC 8/31 16:30)必须被包含 —— 若 naive 被当 UTC 窗口
    (UTC 9/1 0:00 起)会漏掉它"""
    from datetime import datetime as _py_dt

    from app.services.analytics import cash_out_trend

    # 北京 2026-09-01 00:30 = UTC 2026-08-31 16:30
    bj_midnight = _bj("2026-09-01 00:30:00")
    _seed_report(_test_session, "SN-TZ-N1", "66.00", bj_midnight)
    run_refund_adoption(_test_session, routes="E", commit=False)

    # naive 入参(模拟前端 dateRange 传 YYYY-MM-DD)
    start = _py_dt.fromisoformat("2026-09-01")   # naive 北京
    end = _py_dt.fromisoformat("2026-09-10")     # naive 北京
    rows = cash_out_trend(_test_session, start, end)
    m = _day_map_of(rows)
    # 修复前: naive 当 UTC → start=UTC 9/1 0:00, 数据 UTC 8/31 16:30 被漏
    # 修复后: naive 按北京 → start=UTC 8/31 16:00, 数据被包含
    assert m.get("2026-09-01", {}).get("merchant", 0) == 66.00
    assert m.get("2026-08-31", {}).get("merchant", 0) == 0.0  # 北京日归属 9/1
