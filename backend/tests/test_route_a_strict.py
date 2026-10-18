# -*- coding: utf-8 -*-
"""A 路经营口径测试(2026-09-08 #41 收紧 → 2026-10-18 #332 T8 切 std 表)

历史: A 路口径曾从「仅 refund_method='线上'」收紧为「线上 + status='已退款'」;
#332 T8 起采纳口径在 std_refund_online 表层固化(ADOPT_RULE_VER=r20261016_v1),
查询侧 _route_a_online_refund_filter 归零(统一入口形态保留), 全状态诊断读 ods 原始层。

覆盖:
1. _route_a_online_refund_filter 返回空(口径已表层固化)
2. _route_a_online_any_status_filter 仅 method(ods 原始层全状态)
3. _cash_routes_total.route_a 取数 std_refund_online 采纳行(窗口过滤)
4. operation_status_dist 仍能看到全状态
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests._db_guard import safe_wipe

_TZ = timezone(timedelta(hours=8))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=_TZ)


@pytest.fixture()
def _test_session(_engine):
    """独立 session; 只清理本文件建的退款(前缀 RF-RA-), 不碰其它测试数据"""
    from app.models import Device, Refund

    session = Session(_engine, autocommit=False, autoflush=False)
    # 清掉历史残留(本文件前缀), 防跨运行污染
    from app.models.refund_standard import StdRefundOnline

    for cls in (Refund, StdRefundOnline):
        safe_wipe(session, cls, where=cls.refund_no.like("RF-RA-%"))
    safe_wipe(session, Device, where=Device.sn.like("SN-RA-%"))
    session.commit()
    try:
        yield session
    finally:
        try:
            from app.models.refund_standard import StdRefundOnline

            safe_wipe(session, Refund, where=Refund.refund_no.like("RF-RA-%"))
            safe_wipe(session, StdRefundOnline, where=StdRefundOnline.refund_no.like("RF-RA-%"))
            from app.models import Device

            safe_wipe(session, Device, where=Device.sn.like("SN-RA-%"))
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

def test_route_a_refund_filter_now_empty():
    """#332 T8: 采纳口径已在 std_refund_online 表层固化 → 查询侧过滤归零。

    统一入口形态保留(调用点不用改签名), 返回空元组 = 不再散落 WHERE;
    「线上/NULL + 已退款」采纳语义的行为锁见 test_refund_adoption_rules.py。
    """
    from app.services.analytics import _route_a_online_refund_filter

    assert _route_a_online_refund_filter() == ()


def test_route_a_any_status_filter_only_method():
    """全状态口径 _route_a_online_any_status_filter 仅 method(读 ods 原始层 Refund)。"""
    from app.services.analytics import _route_a_online_any_status_filter

    conds = _route_a_online_any_status_filter()
    assert len(conds) == 1  # 仅 method


# ---- _cash_routes_total route_a 取数 std 采纳行 ----

def test_cash_routes_route_a_reads_std(_test_session):
    """#332 T8: route_a 取数 std_refund_online —— 表内即采纳行(线上/NULL+已退款),
    无需退款/驳回/申请中在采纳作业层被排除(行为锁 test_refund_adoption_rules.py);
    此处只锁「查询确实读 std 表 + 窗口过滤生效」。"""
    from app.models.refund_standard import StdRefundOnline
    from app.services.analytics import _cash_routes_total

    s = _dt("2026-09-01 00:00:00")
    e = _dt("2026-09-10 23:59:59")
    # 采纳行(窗口内) → 计入(created_at/updated_at 为 TimestampMixin NOT NULL 列, 须显式给值)
    _test_session.add(StdRefundOnline(
        refund_no="RF-RA-0001", device_sn="SN-RA-1", refund_method="线上",
        status="已退款", refund_amount="100.00", created_at_raw=_dt("2026-09-05 10:00:00"),
        created_at=_dt("2026-09-05 10:00:00"), updated_at=_dt("2026-09-05 10:00:00"),
    ))
    # 采纳行(窗口外) → 不计
    _test_session.add(StdRefundOnline(
        refund_no="RF-RA-0002", device_sn="SN-RA-2", refund_method="线上",
        status="已退款", refund_amount="200.00", created_at_raw=_dt("2026-09-20 10:00:00"),
        created_at=_dt("2026-09-20 10:00:00"), updated_at=_dt("2026-09-20 10:00:00"),
    ))
    _test_session.commit()

    res = _cash_routes_total(_test_session, start=s, end=e)
    ra = res["route_a"]
    assert ra["count"] == 1, f"route_a 应只算窗口内 1 笔, 实际 {ra['count']}"
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
