# -*- coding: utf-8 -*-
"""A 路经营口径收紧测试(2026-09-08, #41/A-20260908-004)

背景: A 路经营口径从「仅 refund_method='线上'」收紧为「线上 + status='已退款'」,
避免"无需退款(266)/驳回(15)/空状态(785)"等非成功退款虚高经营出款(¥11.8万)。
全状态分析(状态分布)改用 _route_a_online_any_status_filter(仅 method)。

覆盖:
1. _route_a_online_refund_filter 含 status='已退款'(经营口径)
2. _route_a_online_any_status_filter 仅 method(全状态口径)
3. _cash_routes_total.route_a 只算线上+已退款(不含无需退款/驳回/申请中)
4. operation_status_dist 仍能看到全状态
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
    """独立 session; 只清理本文件建的退款(前缀 RF-RA-), 不碰其它测试数据"""
    from app.models import Device, Refund

    session = Session(_engine, autocommit=False, autoflush=False)
    # 清掉历史残留(本文件前缀), 防跨运行污染
    for cls in (Refund,):
        session.execute(cls.__table__.delete().where(cls.refund_no.like("RF-RA-%")))
    session.execute(Device.__table__.delete().where(Device.sn.like("SN-RA-%")))
    session.commit()
    try:
        yield session
    finally:
        try:
            from app.models import Refund

            session.execute(Refund.__table__.delete().where(Refund.refund_no.like("RF-RA-%")))
            from app.models import Device

            session.execute(Device.__table__.delete().where(Device.sn.like("SN-RA-%")))
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


def _seed_refund(session, rno, sn, amount, method="线上", status="已退款", created="2026-09-05 10:00:00"):
    from app.models import Refund

    _ensure_device(session, sn)
    session.add(Refund(
        refund_no=rno, device_sn=sn, refund_method=method, status=status,
        refund_amount=amount, created_at_raw=_dt(created),
        platform_order_no=f"WS-{rno}",
    ))
    session.commit()


# ---- 口径函数测试 ----

def test_route_a_refund_filter_now_includes_refunded():
    """经营口径 _route_a_online_refund_filter 含 method=线上 + status=已退款。"""
    from app.services.analytics import _route_a_online_refund_filter

    conds = _route_a_online_refund_filter()
    assert len(conds) == 2  # method + status


def test_route_a_any_status_filter_only_method():
    """全状态口径 _route_a_online_any_status_filter 仅 method(不含 status)。"""
    from app.services.analytics import _route_a_online_any_status_filter

    conds = _route_a_online_any_status_filter()
    assert len(conds) == 1  # 仅 method


# ---- _cash_routes_total route_a 只算线上+已退款 ----

def test_cash_routes_route_a_excludes_non_refunded(_test_session):
    """route_a 只算线上+已退款; 无需退款/驳回/申请中 不计入经营出款。"""
    from app.services.analytics import _cash_routes_total

    s = _dt("2026-09-01 00:00:00")
    e = _dt("2026-09-10 23:59:59")
    # 线上+已退款 → 计入
    _seed_refund(_test_session, "RF-RA-0001", "SN-RA-1", "100.00", status="已退款")
    # 线上+无需退款 → 不计
    _seed_refund(_test_session, "RF-RA-0002", "SN-RA-2", "200.00", status="无需退款")
    # 线上+驳回 → 不计
    _seed_refund(_test_session, "RF-RA-0003", "SN-RA-3", "300.00", status="驳回")
    # 线上+申请中 → 不计
    _seed_refund(_test_session, "RF-RA-0004", "SN-RA-4", "400.00", status="申请中")
    # 线下+已退款 → 不计(非 A 路)
    _seed_refund(_test_session, "RF-RA-0005", "SN-RA-5", "500.00", method="线下", status="已退款")

    res = _cash_routes_total(_test_session, start=s, end=e)
    ra = res["route_a"]
    assert ra["count"] == 1, f"route_a 应只算 1 笔(线上+已退款), 实际 {ra['count']}"
    assert abs(ra["amount"] - 100.00) < 0.01, f"route_a 金额应 100, 实际 {ra['amount']}"


# ---- operation_status_dist 仍看全状态 ----

def test_operation_status_dist_shows_all_statuses(_test_session):
    """状态分布仍展示线上退费单全状态(不受经营口径收紧影响)。"""
    from app.services.analytics import operation_status_dist

    _seed_refund(_test_session, "RF-RA-0011", "SN-RA-1", "100.00", status="已退款")
    _seed_refund(_test_session, "RF-RA-0012", "SN-RA-2", "200.00", status="申请中")

    dist = operation_status_dist(_test_session)
    statuses = {d["name"] for d in dist}
    assert "已退款" in statuses
    assert "申请中" in statuses  # 全状态仍可见
