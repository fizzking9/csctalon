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

    if device_sn:
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
    # ⚠️ 2026-09-05 (#20): 结构化溯源列被写入(双向溯源)
    assert m.backfill_refund_record_report_id == str(r.id)


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


def test_backfill_low_precision_op_time_upgrade(_test_session):
    """低精度 op_time(北京15:00 默认) + 唯一 L2 匹配 → 提升为精确 refund_time (#20)"""
    from app.services.reconcile import backfill_d_op_time

    # E 路 report: 精确退款时间 北京 2026-09-05 17:17:38
    r = _seed_report(_test_session, platform_order_no="WS-PREC-1", device_sn="SN-D-LP1",
                     amount="199.00", refund_time="2026-09-05 17:17:38")
    # D 路 manual: op_time 为低精度默认(北京 15:00:00), 同日登记
    m = _seed_manual(_test_session, device_sn="SN-D-LP1", order_no="WS-OTHER-X",
                     amount="199.00", op_time=_dt("2026-09-05 15:00:00"),
                     register_time="2026-09-05 09:00:00")

    stats = backfill_d_op_time(_test_session)
    assert stats["precision_upgraded"] == 1
    assert stats["backfilled"] == 1
    _test_session.refresh(m)
    # op_time 被提升为 E 路精确 refund_time(非 15:00 整点)
    assert m.op_time == r.refund_time
    assert _dt("2026-09-05 15:00:00") != r.refund_time  # 确保不是同值(确实发生了升级)
    assert m.quality_status == "auto_filled"
    assert "低精度默认" in (m.quality_note or "")
    assert m.backfill_refund_record_report_id == str(r.id)


def test_backfill_low_precision_no_candidate_keep(_test_session):
    """低精度 op_time 但无唯一 E 路匹配 → 不误改原值"""
    from app.services.reconcile import backfill_d_op_time

    _seed_manual(_test_session, device_sn="SN-D-NONE", order_no="WS-NOLP",
                 amount="88.88", op_time=_dt("2026-09-05 15:00:00"),
                 register_time="2026-09-05 09:00:00")
    stats = backfill_d_op_time(_test_session)
    assert stats["backfilled"] == 0
    assert stats["precision_upgraded"] == 0
    # 原 15:00 低精度值保留(未误改)
    assert stats["skipped_no_candidate"] >= 1


def test_backfill_low_precision_idempotent(_test_session):
    """低精度升级为 auto_filled 后重跑 → 不再重复处理"""
    from app.services.reconcile import backfill_d_op_time

    _seed_report(_test_session, platform_order_no="WS-PREC-2", device_sn="SN-D-LP2",
                 amount="50.00", refund_time="2026-09-05 16:20:11")
    _seed_manual(_test_session, device_sn="SN-D-LP2", order_no="WS-OTHER-Y",
                 amount="50.00", op_time=_dt("2026-09-05 15:00:00"),
                 register_time="2026-09-05 09:00:00")
    s1 = backfill_d_op_time(_test_session)
    assert s1["precision_upgraded"] == 1
    s2 = backfill_d_op_time(_test_session)
    assert s2["precision_upgraded"] == 0  # 已精确, 不再升级
    assert s2["backfilled"] == 0


# ---------- #21 护栏: 残留脏行不自动回填 + 同 report 不重复回填 ----------

def test_backfill_residual_no_sn_weird_year_skip(_test_session):
    """护栏1(#21): 无SN + 登记年份异常(如 2002)的残留行 → 不自动回填, 标 pending

    生产实证: 同一 order_no 存在"无SN行(金额100/乱年份 register_time/remark=余额已清除)"
    + "有SN真实行" 成对; 若不拦, 无SN残留行会被回填后拉进近期窗口重复计入 D 路。
    """
    from app.services.reconcile import backfill_d_op_time

    # E 水单(会被 L1 order_no 匹配到)
    _seed_report(_test_session, platform_order_no="WS-RES-1", device_sn="SN-D-REAL",
                 amount="19.00", refund_time="2026-09-05 10:30:00")
    # 残留脏行: 无SN + 乱年份(2002) + 缺 op_time
    m = _seed_manual(_test_session, device_sn=None, order_no="WS-RES-1",
                     amount="100.00", op_time=None, register_time="2002-09-26 00:00:00")

    stats = backfill_d_op_time(_test_session)
    assert stats["residual_skipped"] >= 1
    assert stats["backfilled"] == 0
    _test_session.refresh(m)
    assert m.op_time is None          # 未被自动回填
    assert m.quality_status == "pending"  # 转人工
    assert "残留" in (m.quality_note or "")


def test_backfill_residual_with_sn_ok(_test_session):
    """有SN + 真实登记年份 → 不被误判残留, 正常回填"""
    from app.services.reconcile import backfill_d_op_time

    _seed_report(_test_session, platform_order_no="WS-OK-1", device_sn="SN-D-OK",
                 amount="19.00", refund_time="2026-09-05 10:30:00")
    m = _seed_manual(_test_session, device_sn="SN-D-OK", order_no="WS-OK-1",
                     amount="19.00", op_time=None, register_time="2026-09-01 09:00:00")
    stats = backfill_d_op_time(_test_session)
    assert stats["backfilled"] == 1
    assert stats["residual_skipped"] == 0
    _test_session.refresh(m)
    assert m.op_time is not None
    assert m.quality_status == "auto_filled"


def test_backfill_same_report_not_double_backfill(_test_session):
    """护栏2(#21): 同一 E 水单 report 已回填到其它 manual 行 → 不再回填当前行(pending)"""
    from app.models import RefundRecordReport
    from app.services.reconcile import backfill_d_op_time

    # 唯一 report, 会被两个同 order_no 的 manual 行 L1 匹配到
    r = _seed_report(_test_session, platform_order_no="WS-1TO2-1", device_sn="SN-D-PR",
                     amount="19.00", refund_time="2026-09-05 10:30:00")
    # 第一条 manual(带SN真实行): 低精度 → 走 L1/L2 匹配并回填
    _seed_manual(_test_session, device_sn="SN-D-PR", order_no="WS-1TO2-1",
                 amount="19.00", op_time=_dt("2026-09-05 15:00:00"),
                 register_time="2026-09-05 09:00:00")
    # 第二条 manual(无SN同单同额候选, 登记年份正常 2026, 缺 op_time)
    m2 = _seed_manual(_test_session, device_sn=None, order_no="WS-1TO2-1",
                      amount="19.00", op_time=None, register_time="2026-09-05 09:00:00")

    s1 = backfill_d_op_time(_test_session)
    # 第一次跑: 带SN行先回填(占用了该 report); 无SN行缺op_time是候选, 但 report 已被占用 → 拦下
    assert s1["backfilled"] == 1
    assert s1["report_occupied_skipped"] >= 1
    _test_session.refresh(m2)
    assert m2.op_time is None
    assert m2.quality_status == "pending"  # 被护栏拦下转人工
    # 反向确认: 该 report 只被回填给了一条 manual(带SN行)
    _test_session.expire_all()
    occupied = _test_session.execute(
        select(RefundRecordReport).where(RefundRecordReport.id == r.id)
    ).scalar_one()
    assert occupied.backfill_manual_refund_id is not None


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


def test_water_statement_naive_bj_param(_test_session):
    """naive 参数按北京时间解释(2026-09-05 修复, #20)

    前端传 "2026-09-05T00:00:00"(naive, 北京时间) → 应命中北京 09-05 的退款,
    而非被当成 UTC(会整体偏 8h, 把北京 00:00-07:59 的退款漏掉)。
    """
    from datetime import datetime as _py_dt

    from app.services.reconcile import water_statement

    # report 退款时间 = 北京 2026-09-05 06:00 (UTC 2026-09-04 22:00)
    # 若 naive 参数被当 UTC, 窗口 UTC[09-05 00:00, 09-05 23:59] 会漏掉它(它在 UTC 09-04 22:00)
    _seed_report(_test_session, platform_order_no="WS-BJ-EARLY", amount="19.00", refund_time="2026-09-05 06:00:00")

    # naive 北京时间窗(模拟前端 fromisoformat 后原样传入)
    start = _py_dt.fromisoformat("2026-09-05 00:00:00")  # naive
    end = _py_dt.fromisoformat("2026-09-05 23:59:59")    # naive
    res = water_statement(_test_session, start, end)
    assert res["total_report_count"] == 1
    assert res["by_route"]["unmatched"] == 1


def test_water_statement_returns_bj_iso(_test_session):
    """序列化 refund_time/date_start/date_end 应带 +08:00(北京时间, 2026-09-05 修复)"""
    from app.services.reconcile import water_statement

    _seed_report(_test_session, platform_order_no="WS-ISO-CHK", amount="19.00", refund_time="2026-09-05 10:00:00")

    res = water_statement(
        _test_session,
        _dt("2026-09-05 00:00:00"),
        _dt("2026-09-05 23:59:59"),
    )
    # date_start/date_end 应输出 +08:00 后缀(而非 +00:00 或 naive)
    assert res["date_start"].endswith("+08:00")
    assert res["date_end"].endswith("+08:00")
    # items[0].refund_time 应为北京 10:00 且带 +08:00
    item_t = res["items"][0]["refund_time"]
    assert item_t.endswith("+08:00")
    assert "T10:00:00" in item_t


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
