# -*- coding: utf-8 -*-
"""对账回填 reconcile 测试(A-20260905-002 LOCKED / #17)

覆盖:
1. backfill_d_op_time: 缺 op_time 的 D 路行被唯一 report 匹配回填 + auto_filled
2. 多候选 → pending 不误写
3. 幂等: 重跑已回填行跳过
4. water_statement: 勾稽分类 matched(A)/unmatched 正确
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
    session = Session(_engine, autocommit=False, autoflush=False)
    from app.models import Device, ManualRefund, Refund, RefundRecordReport

    for cls in (RefundRecordReport, ManualRefund, Refund):
        session.execute(cls.__table__.delete())
    # 清理本文件可能残留的测试设备(_ensure_device commit 落库)
    session.execute(Device.__table__.delete().where(Device.sn.like("SN-D-%")))
    session.commit()
    try:
        yield session
    finally:
        # teardown: 清理本文件写入的共享表数据(避免污染其它测试)
        try:
            for cls in (RefundRecordReport, ManualRefund, Refund):
                session.execute(cls.__table__.delete())
            session.execute(Device.__table__.delete().where(Device.sn.like("SN-D-%")))
            session.commit()
        except Exception:
            session.rollback()
        session.rollback()
        session.close()


def _ensure_device(session, sn):
    """确保设备存在(满足 refund_record_reports.device_sn FK)"""
    from app.models import Device

    if not session.execute(select(Device).where(Device.sn == sn)).scalar_one_or_none():
        session.add(Device(sn=sn, merchant_name="测试商户"))
        session.commit()


def _seed_report(session, platform_order_no="WS2026090500000001", device_sn="SN-D-1",
                 amount="19.00", refund_time="2026-09-05 10:30:00", status="退款成功"):
    from app.models import RefundRecordReport

    _ensure_device(session, device_sn)
    r = RefundRecordReport(
        platform_order_no=platform_order_no,
        device_sn=device_sn,
        merchant_id="1611876437",
        merchant_name="测试商户",
        refund_time=_dt(refund_time),
        refund_amount=amount,
        refund_status=status,
    )
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def _seed_manual(session, device_sn="SN-D-1", order_no="WS2026090500000001",
                 amount="19.00", op_time=None, is_refunded="是", register_time="2026-09-05 09:00:00"):
    from app.models import ManualRefund

    _ensure_device(session, device_sn)
    m = ManualRefund(
        device_sn=device_sn,
        order_no=order_no,
        merchant_id="1611876437",
        refund_amount=amount,
        register_time=_dt(register_time),
        is_refunded=is_refunded,
        op_time=op_time,
        registrant="测试员",
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


# ---------- D 路回填 ----------

def test_backfill_op_time_l2_match(_test_session):
    """L2(设备+金额)唯一匹配 → 回填 op_time + auto_filled"""
    from app.services.reconcile import backfill_d_op_time

    r = _seed_report(_test_session, platform_order_no="WS-OTHER-1")  # 订单号不同
    m = _seed_manual(_test_session, order_no=None, amount="19.00", op_time=None)

    stats = backfill_d_op_time(_test_session)
    assert stats["backfilled"] == 1
    _test_session.refresh(m)
    assert m.op_time is not None
    assert m.op_time == r.refund_time
    assert m.quality_status == "auto_filled"
    assert "refund_record_reports.id" in (m.quality_note or "")


def test_backfill_op_time_l1_order_match(_test_session):
    """L1(订单号)匹配优先"""
    from app.services.reconcile import backfill_d_op_time

    _seed_report(_test_session, platform_order_no="WS-ORD-1", amount="19.00")
    m = _seed_manual(_test_session, order_no="WS-ORD-1", amount="19.00", op_time=None)
    stats = backfill_d_op_time(_test_session)
    assert stats["backfilled"] == 1
    _test_session.refresh(m)
    assert m.op_time is not None


def test_backfill_no_candidate_skip(_test_session):
    """无候选 → 跳过不写"""
    from app.services.reconcile import backfill_d_op_time

    m = _seed_manual(_test_session, order_no="WS-NOEXIST", device_sn="SN-NONE", amount="99.99", op_time=None)
    stats = backfill_d_op_time(_test_session)
    assert stats["backfilled"] == 0
    assert stats["skipped_no_candidate"] >= 1
    _test_session.refresh(m)
    assert m.op_time is None


def test_backfill_multiple_candidate_pending(_test_session):
    """同设备+金额 两笔 report → 多候选 → pending 不误写"""
    from app.services.reconcile import backfill_d_op_time

    _seed_report(_test_session, platform_order_no="WS-A1", device_sn="SN-DUP", amount="19.00", refund_time="2026-09-05 10:00:00")
    _seed_report(_test_session, platform_order_no="WS-A2", device_sn="SN-DUP", amount="19.00", refund_time="2026-09-05 11:00:00")
    m = _seed_manual(_test_session, device_sn="SN-DUP", order_no=None, amount="19.00", op_time=None)
    stats = backfill_d_op_time(_test_session)
    assert stats["backfilled"] == 0
    assert stats["pending"] >= 1
    _test_session.refresh(m)
    assert m.op_time is None
    assert m.quality_status == "pending"


def test_backfill_idempotent(_test_session):
    """已 auto_filled 的行重跑跳过"""
    from app.services.reconcile import backfill_d_op_time

    _seed_report(_test_session, platform_order_no="WS-ORD-1")
    m = _seed_manual(_test_session, order_no="WS-ORD-1", op_time=None)
    s1 = backfill_d_op_time(_test_session)
    assert s1["backfilled"] == 1
    s2 = backfill_d_op_time(_test_session)
    assert s2["backfilled"] == 0  # 已回填跳过
    _test_session.refresh(m)
    assert m.quality_status == "auto_filled"  # 保持已回填状态


# ---------- 水单勾稽 ----------

def _seed_refund_a(session, platform_order_no="WS2026090500000001"):
    """A 路退费单(线上)"""
    from app.models import Refund

    _ensure_device(session, "SN-D-1")
    r = Refund(
        refund_no="RF202609050001",
        platform_order_no=platform_order_no,
        device_sn="SN-D-1",
        refund_amount=19.0,
        refund_method="线上",
        created_at_raw=_dt("2026-09-05 09:00:00"),
    )
    session.add(r)
    session.commit()


def test_water_statement_classify(_test_session):
    """勾稽: 命中 A 路订单号 → matched A; 无匹配 → unmatched"""
    from app.services.reconcile import water_statement

    _seed_report(_test_session, platform_order_no="WS-MATCH-A", amount="19.00", refund_time="2026-09-05 10:00:00")
    _seed_report(_test_session, platform_order_no="WS-NOMATCH", amount="88.00", refund_time="2026-09-05 11:00:00")
    _seed_refund_a(_test_session, platform_order_no="WS-MATCH-A")

    res = water_statement(
        _test_session,
        _dt("2026-09-05 00:00:00"),
        _dt("2026-09-05 23:59:59"),
    )
    assert res["total_report_count"] == 2
    assert res["by_route"]["A"] == 1
    assert res["by_route"]["unmatched"] == 1
    assert res["matched_count"] == 1
    assert res["unmatched_count"] == 1


def test_water_statement_filter_status(_test_session):
    """status=unmatched 过滤只返回未登记行"""
    from app.services.reconcile import water_statement

    _seed_report(_test_session, platform_order_no="WS-MATCH-A", amount="19.00", refund_time="2026-09-05 10:00:00")
    _seed_report(_test_session, platform_order_no="WS-NOMATCH", amount="88.00", refund_time="2026-09-05 11:00:00")
    _seed_refund_a(_test_session, platform_order_no="WS-MATCH-A")

    res = water_statement(
        _test_session,
        _dt("2026-09-05 00:00:00"),
        _dt("2026-09-05 23:59:59"),
        status="unmatched",
    )
    assert res["filtered_total"] == 1
    assert len(res["items"]) == 1
    assert res["items"][0]["platform_order_no"] == "WS-NOMATCH"


# ---------- API 层 ----------

@pytest.fixture()
def admin_client(_engine):
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.main import app
    from app.models.user import RoleEnum, User

    fake_user = User(id=1, username="admin", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake_user

    def _override_db():
        from sqlalchemy.orm import Session

        session = Session(_engine, autocommit=False, autoflush=False)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def test_api_backfill_endpoint(admin_client):
    """POST /reconcile/backfill-d-op-time 返回统计结构"""
    resp = admin_client.post("/api/v1/reconcile/backfill-d-op-time")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert "backfilled" in body["data"]
    assert "scanned" in body["data"]


def test_api_water_statement_bad_range(admin_client):
    """start >= end → 400"""
    resp = admin_client.get(
        "/api/v1/reconcile/water-statement",
        params={"start": "2026-09-05T23:59:59", "end": "2026-09-01T00:00:00"},
    )
    assert resp.status_code == 400


def test_api_water_statement_ok(admin_client, _test_session):
    """认证下 water-statement 正常返回"""

    _seed_report(_test_session, platform_order_no="WS-MATCH-A", amount="19.00", refund_time="2026-09-05 10:00:00")
    _seed_refund_a(_test_session, platform_order_no="WS-MATCH-A")
    resp = admin_client.get(
        "/api/v1/reconcile/water-statement",
        params={"start": "2026-09-05T00:00:00", "end": "2026-09-05T23:59:59"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["total_report_count"] == 1
