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
    from app.models import Device, ManualRefund

    session = Session(_engine, autocommit=False, autoflush=False)
    from app.models import Refund, RefundRecordReport

    for cls in (RefundRecordReport, Refund):
        session.execute(cls.__table__.delete())
    # 本文件 D 路回归测试建的 manual(SN-D-*) 也清理, 防污染
    session.execute(ManualRefund.__table__.delete().where(ManualRefund.device_sn.like("SN-D-%")))
    session.execute(Device.__table__.delete().where(Device.sn.like("SN-E-%")))
    session.execute(Device.__table__.delete().where(Device.sn.like("SN-D-%")))
    session.commit()
    try:
        yield session
    finally:
        try:
            for cls in (RefundRecordReport, Refund):
                session.execute(cls.__table__.delete())
            session.execute(ManualRefund.__table__.delete().where(ManualRefund.device_sn.like("SN-D-%")))
            session.execute(Device.__table__.delete().where(Device.sn.like("SN-E-%")))
            session.execute(Device.__table__.delete().where(Device.sn.like("SN-D-%")))
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


# ============================================================
# 2026-09-06 缺陷回归: D 路统计必须排除 quality_status='dup_residual' 重复行
# 根因: #21 治理把确凿重复标 dup_residual(不物理删), 但 _route_d_other_agg / trend
#       未排除 → 已治理重复仍计入 D 路经营(生产 9月窗口差 ¥4.8万)。
# ============================================================
def _seed_manual(session, sn, amount, op_time, is_refunded="是", quality_status=None):
    from app.models import ManualRefund

    if sn:
        _ensure_device(session, sn)
    m = ManualRefund(
        device_sn=sn,
        order_no=f"WS-D-{sn}-{amount}",
        merchant_id="1611876437",
        refund_amount=amount,
        register_time=_dt("2026-09-03 09:00:00"),
        op_time=_dt(op_time),
        is_refunded=is_refunded,
        registrant="测试员",
        quality_status=quality_status,
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


def test_route_d_excludes_dup_residual(_test_session):
    """D 路: quality_status='dup_residual' 的已治理重复行不计入 route_d 经营。"""
    from app.services.analytics import _cash_routes_total

    s = _dt("2026-09-01 00:00:00")
    e = _dt("2026-09-10 23:59:59")
    # 1 笔正常行 + 1 笔 dup_residual 重复行(同金额同单)
    _seed_manual(_test_session, "SN-D-1", "19.00", "2026-09-05 10:00:00", quality_status=None)
    _seed_manual(_test_session, "SN-D-1", "19.00", "2026-09-05 10:00:00", quality_status="dup_residual")

    res = _cash_routes_total(_test_session, start=s, end=e)
    rd = res["route_d"]
    assert rd["count"] == 1, f"route_d.count={rd['count']}, 期望 1(dup_residual 应排除)"
    assert abs(rd["amount"] - 19.00) < 0.01, f"route_d.amount={rd['amount']}, 期望 19.00"


def test_route_d_keeps_normal_and_pending(_test_session):
    """D 路: 正常行 + quality_status='pending'(待人工, 非确凿重复) 仍计入。"""
    from app.services.analytics import _cash_routes_total

    s = _dt("2026-09-01 00:00:00")
    e = _dt("2026-09-10 23:59:59")
    _seed_manual(_test_session, "SN-D-2", "19.00", "2026-09-05 10:00:00", quality_status=None)
    _seed_manual(_test_session, "SN-D-3", "5.00", "2026-09-05 11:00:00", quality_status="pending")

    res = _cash_routes_total(_test_session, start=s, end=e)
    rd = res["route_d"]
    assert rd["count"] == 2, f"route_d.count={rd['count']}, 期望 2"
    assert abs(rd["amount"] - 24.00) < 0.01, f"route_d.amount={rd['amount']}, 期望 24.00"
