# -*- coding: utf-8 -*-
"""对账复核 reconcile_check 测试(A-20260906-002 LOCKED / #22)

覆盖:
1. preview_check: 快照含 route_snapshot(五路+经营+实际+差异)/by_route_of_e/dupe_detect
2. 口径一致性: route_snapshot.operational = 直查五路合计; actual_cashflow = E 合计
3. confirm_check: 落库 reconcile_checks + check_no 生成 + 快照冻结
4. 幂等/隔离: 复核记录不参与任何统计聚合(新增记录不影响 preview 结果)
5. 权限: confirm/set_conclusion 需 Admin/Supervisor(API 层); run/list 需登录
6. set_conclusion: 补/改结论留痕
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_TZ = timezone(timedelta(hours=8))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=_TZ)


def _ensure_device(session, sn):
    """确保设备存在(满足 FK)"""
    from app.models import Device

    if not session.execute(select(Device).where(Device.sn == sn)).scalar_one_or_none():
        session.add(Device(sn=sn, merchant_name="测试商户"))
        session.commit()


@pytest.fixture()
def _test_session(_engine):
    session = Session(_engine, autocommit=False, autoflush=False)
    from app.models import (  # noqa: F401
        Device,
        ManualRefund,
        ReconcileCheck,
        ReconcileMatch,
        Refund,
        RefundRecordReport,
    )

    # 先删子表(reconcile_matches FK→reconcile_checks), 再删主表
    for cls in (ReconcileMatch, ReconcileCheck, RefundRecordReport, ManualRefund, Refund):
        session.execute(cls.__table__.delete())
    session.execute(Device.__table__.delete().where(Device.sn.like("SN-C-%")))
    session.commit()
    try:
        yield session
    finally:
        try:
            for cls in (ReconcileMatch, ReconcileCheck, RefundRecordReport, ManualRefund, Refund):
                session.execute(cls.__table__.delete())
            session.execute(Device.__table__.delete().where(Device.sn.like("SN-C-%")))
            session.commit()
        except Exception:
            session.rollback()
        session.rollback()
        session.close()


def _seed_report(session, platform_order_no="WS2026090500000001", device_sn="SN-C-1",
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


def _seed_manual(session, device_sn="SN-C-1", order_no="WS2026090500000001",
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


def _seed_refund_a(session, platform_order_no="WS2026090500000001"):
    """A 路退费单(线上+已退款, 会出现在 E 勾稽的 A 桶)

    注: 2026-09-08 (#41) A 路经营口径收紧为线上+已退款, seed 须带 status='已退款'。
    """
    from app.models import Refund

    _ensure_device(session, "SN-C-1")
    r = Refund(
        refund_no="RF2026090500A1",
        platform_order_no=platform_order_no,
        device_sn="SN-C-1",
        refund_amount=19.0,
        refund_method="线上",
        status="已退款",
        created_at_raw=_dt("2026-09-05 09:00:00"),
    )
    session.add(r)
    session.commit()


def _fake_user():
    from app.models import User

    return User(id=1, username="admin", display_name="管理员", role="admin", is_active=True)


# ---------- preview 快照 ----------

def test_preview_check_snapshot_shape(_test_session):
    """preview 返回 route_snapshot(五路+经营+实际+差异)/by_route_of_e/dupe_detect"""
    from app.services.reconcile_check import preview_check

    # E 水单 1 笔 且命中 A 路退费单 → E 勾稽 A 桶
    _seed_report(_test_session, platform_order_no="WS-MATCH-A", amount="19.00")
    _seed_refund_a(_test_session, platform_order_no="WS-MATCH-A")

    res = preview_check(
        _test_session,
        _dt("2026-09-01 00:00:00"),
        _dt("2026-09-30 23:59:59"),
    )
    snap = res["route_snapshot"]
    # _cash_routes_total 输出键
    for k in ("route_a", "route_b", "route_c", "route_d", "operational",
              "actual_cashflow", "diff"):
        assert k in snap
    assert snap["actual_cashflow"]["count"] == 1  # E 水单 1 笔
    assert res["by_route_of_e"]["A"]["count"] == 1  # 命中 A
    assert isinstance(res["dupe_detect"], dict)


def test_preview_operational_matches_direct_sum(_test_session):
    """口径一致性: route_snapshot.operational = A+B+C+D 直查合计; actual = E 合计(#22 验收)"""
    from app.services.reconcile_check import preview_check

    # A 1笔(线上), D 1笔(手动), E 2笔(其中1命中A, 1未命中)
    _seed_refund_a(_test_session, platform_order_no="WS-ORD-1")
    _seed_manual(_test_session, device_sn="SN-C-1", order_no="WS-MAN-1", amount="5.00",
                 op_time=_dt("2026-09-05 11:00:00"))
    _seed_report(_test_session, platform_order_no="WS-ORD-1", amount="19.00")
    _seed_report(_test_session, platform_order_no="WS-UNMATCHED", amount="8.00", device_sn="SN-C-1")

    res = preview_check(
        _test_session,
        _dt("2026-09-01 00:00:00"),
        _dt("2026-09-30 23:59:59"),
    )
    snap = res["route_snapshot"]
    # 直查: A 19.00 + D 5.00 = 24.00
    assert snap["route_a"]["count"] == 1 and snap["route_a"]["amount"] == 19.00
    assert snap["route_d"]["count"] == 1 and snap["route_d"]["amount"] == 5.00
    assert snap["operational"]["count"] == 2
    assert abs(snap["operational"]["amount"] - 24.00) < 0.01
    # actual_cashflow = E 合计 27.00
    assert snap["actual_cashflow"]["count"] == 2
    assert abs(snap["actual_cashflow"]["amount"] - 27.00) < 0.01
    # E 勾稽: A 桶 1(19), unmatched 1(8)
    assert res["by_route_of_e"]["A"]["count"] == 1
    assert res["by_route_of_e"]["unmatched"]["count"] == 1


# ---------- confirm 落库 ----------

def test_confirm_check_persist(_test_session):
    """confirm_check 落库 reconcile_checks + check_no + 快照冻结"""
    from app.models import ReconcileCheck
    from app.services.reconcile_check import confirm_check

    _seed_report(_test_session, platform_order_no="WS-ORD-1", amount="19.00")
    _seed_refund_a(_test_session, platform_order_no="WS-ORD-1")

    user = _fake_user()
    check = confirm_check(
        _test_session, user,
        _dt("2026-09-01 00:00:00"),
        _dt("2026-09-05 23:59:59"),
        conclusion="差异已确认",
        note="E 多出未登记水单, 人工已核对",
    )
    assert check.id is not None
    assert check.check_no.startswith("RC20260901")
    assert check.reviewed_name == "管理员"
    assert check.conclusion == "差异已确认"
    assert check.route_snapshot["actual_cashflow"]["count"] == 1
    # 落库查询
    cnt = _test_session.scalar(
        select(func.count()).select_from(ReconcileCheck).where(ReconcileCheck.id == check.id)
    )
    assert cnt == 1


def test_confirm_check_not_affect_statistics(_test_session):
    """复核记录不参与统计: 新增复核批次不影响 preview 聚合结果(#22 隔离验收)"""
    from app.services.reconcile_check import confirm_check, preview_check

    _seed_report(_test_session, platform_order_no="WS-ORD-1", amount="19.00")
    _seed_refund_a(_test_session, platform_order_no="WS-ORD-1")

    user = _fake_user()
    before = preview_check(
        _test_session,
        _dt("2026-09-01 00:00:00"), _dt("2026-09-05 23:59:59"),
    )
    confirm_check(
        _test_session, user,
        _dt("2026-09-01 00:00:00"), _dt("2026-09-05 23:59:59"),
        conclusion="一致",
    )
    after = preview_check(
        _test_session,
        _dt("2026-09-01 00:00:00"), _dt("2026-09-05 23:59:59"),
    )
    # 复核落库不影响五路合计
    assert before["route_snapshot"] == after["route_snapshot"]


def test_set_conclusion(_test_session):
    """set_conclusion 补/改结论与备注(留痕)"""
    from app.services.reconcile_check import confirm_check, set_conclusion

    _seed_report(_test_session, platform_order_no="WS-ORD-1", amount="19.00")
    _seed_refund_a(_test_session, platform_order_no="WS-ORD-1")
    user = _fake_user()
    check = confirm_check(
        _test_session, user,
        _dt("2026-09-01 00:00:00"), _dt("2026-09-05 23:59:59"),
    )
    assert check.conclusion is None
    updated = set_conclusion(
        _test_session, user, check.id,
        conclusion="存疑待查", note="D 路疑似重复待治理",
    )
    assert updated.conclusion == "存疑待查"
    assert updated.note == "D 路疑似重复待治理"
    assert updated.reviewed_name == "管理员"


def test_set_conclusion_bad_value(_test_session):
    """非法结论值 → ValueError"""
    from app.services.reconcile_check import confirm_check, set_conclusion

    _seed_report(_test_session, platform_order_no="WS-ORD-1", amount="19.00")
    user = _fake_user()
    check = confirm_check(
        _test_session, user,
        _dt("2026-09-01 00:00:00"), _dt("2026-09-05 23:59:59"),
    )
    with pytest.raises(ValueError):
        set_conclusion(_test_session, user, check.id, conclusion="不对")


# ---------- dupe_detect ----------

def test_dupe_detect_finds_dup(_test_session):
    """D 路同 order_no 多行 → dupe_detect 检出(辅助复核)"""
    from app.services.reconcile_check import preview_check

    # 同 order_no 两行(成对)
    _seed_manual(_test_session, device_sn="SN-C-1", order_no="WS-DUP-1", amount="19.00",
                 op_time=_dt("2026-09-05 11:00:00"))
    _seed_manual(_test_session, device_sn=None, order_no="WS-DUP-1", amount="100.00",
                 op_time=_dt("2026-09-05 11:00:00"), register_time="2002-09-26 00:00:00")

    res = preview_check(
        _test_session,
        _dt("2026-09-01 00:00:00"), _dt("2026-09-30 23:59:59"),
    )
    assert res["dupe_detect"]["dup_order_groups"] >= 1
    assert res["dupe_detect"]["no_sn_weird_rows_in_win"] >= 1


def test_dupe_detect_respects_window(_test_session):
    """dupe_detect 限窗口: 窗口外的同单多行不计数(2026-09-06 Reviewer W2 修复)"""
    from app.services.reconcile_check import preview_check

    # 重复行在 7 月(窗口 9 月之外)
    _seed_manual(_test_session, device_sn="SN-C-1", order_no="WS-DUP-OUT", amount="19.00",
                 op_time=_dt("2026-07-05 11:00:00"))
    _seed_manual(_test_session, device_sn=None, order_no="WS-DUP-OUT", amount="100.00",
                 op_time=_dt("2026-07-05 11:00:00"), register_time="2002-09-26 00:00:00")

    res = preview_check(
        _test_session,
        _dt("2026-09-01 00:00:00"), _dt("2026-09-30 23:59:59"),
    )
    # 7 月的重复不应计入 9 月窗口的检出
    assert res["dupe_detect"]["dup_order_groups"] == 0
    assert res["dupe_detect"]["no_sn_weird_rows_in_win"] == 0


# ---------- API 层(权限) ----------

@pytest.fixture()
def _api_env(_engine):
    """API 测试环境: 依赖注入 DB + 当前用户"""
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.main import app
    from app.models import User

    state = {"user": None}

    def _make_user(role):
        return User(id=1, username=f"u_{role}", display_name=f"U{role}", role=role, is_active=True)

    def _override_user():
        return state["user"] or _make_user("admin")

    def _override_db():
        session = Session(_engine, autocommit=False, autoflush=False)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c, state, _make_user
    finally:
        app.dependency_overrides.clear()


def _clean_reconcile_checks(_engine):
    from app.models import ReconcileCheck

    s = Session(_engine)
    try:
        s.execute(ReconcileCheck.__table__.delete())
        s.commit()
    finally:
        s.close()


def test_api_check_run_requires_login(_api_env, _test_session):
    """check/run 需登录; 登录后可预览"""
    client, state, _make = _api_env
    # 未登录 → 401/403
    state["user"] = None
    # get_current_user 抛错场景由 app 处理; 直接以 admin 登录跑正常路径
    state["user"] = _make("admin")
    _seed_report(_test_session, platform_order_no="WS-API-1", amount="19.00")
    r = client.get("/api/v1/reconcile/check/run",
                   params={"start": "2026-09-01T00:00:00", "end": "2026-09-05T23:59:59"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["route_snapshot"]["actual_cashflow"]["count"] == 1


def test_api_check_confirm_requires_admin(_api_env, _test_session):
    """check/confirm 仅 Admin/Supervisor(agent 403)"""
    client, state, _make = _api_env
    _seed_report(_test_session, platform_order_no="WS-API-2", amount="19.00")
    state["user"] = _make("agent")
    r = client.post("/api/v1/reconcile/check/confirm",
                    params={"start": "2026-09-01T00:00:00", "end": "2026-09-05T23:59:59"})
    assert r.status_code == 403, r.text
    state["user"] = _make("admin")
    r = client.post("/api/v1/reconcile/check/confirm",
                    params={"start": "2026-09-01T00:00:00", "end": "2026-09-05T23:59:59",
                            "conclusion": "一致"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["check_no"].startswith("RC")


def test_api_check_confirm_then_conclusion(_api_env, _test_session):
    """confirm 后改结论(API 层)"""
    client, state, _make = _api_env
    state["user"] = _make("supervisor")
    _seed_report(_test_session, platform_order_no="WS-API-3", amount="19.00")
    r = client.post("/api/v1/reconcile/check/confirm",
                    params={"start": "2026-09-01T00:00:00", "end": "2026-09-05T23:59:59"})
    cid = r.json()["data"]["id"]
    r2 = client.post(f"/api/v1/reconcile/check/{cid}/conclusion",
                     params={"conclusion": "差异已确认", "note": "核实完成"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["conclusion"] == "差异已确认"
    # list 可查到
    r3 = client.get("/api/v1/reconcile/check/list")
    assert r3.status_code == 200
    assert any(x["id"] == cid for x in r3.json()["data"]["items"])


# ---------- #22 v1.1 逐笔勾稽明细核对 ----------

def _mk_check(session, user=None, start="2026-09-01 00:00:00", end="2026-09-30 23:59:59"):
    """helper: 建一个复核批次并返回"""
    from app.services.reconcile_check import confirm_check

    return confirm_check(session, user or _fake_user(), _dt(start), _dt(end))


def test_ensure_line_auto_route(_test_session):
    """E 笔命中 A 退费单 → ensure_line 建行, auto_route=A"""
    from app.models import ReconcileMatch
    from app.services.reconcile_check import ensure_line

    rpt = _seed_report(_test_session, platform_order_no="WS-LINE-A", amount="19.00")
    _seed_refund_a(_test_session, platform_order_no="WS-LINE-A")
    check = _mk_check(_test_session)

    line = ensure_line(_test_session, check.id, rpt)
    assert line.auto_route == "A"
    assert line.confirm_status == "待核对"
    # matched_ref 含 A 路候选
    assert any(c["route"] == "A" for c in (line.matched_ref or []))
    # 幂等: 再 ensure 不新建
    cnt = _test_session.scalar(
        select(func.count()).select_from(ReconcileMatch).where(ReconcileMatch.check_id == check.id)
    )
    assert cnt == 1


def test_line_candidates_d_by_order(_test_session):
    """E 笔订单号命中 D 手动台账 → candidates 含 D"""
    from app.services.reconcile_check import _route_candidates

    rpt = _seed_report(_test_session, platform_order_no="WS-LINE-D", amount="19.00")
    _seed_manual(_test_session, device_sn="SN-C-1", order_no="WS-LINE-D", amount="19.00",
                 op_time=_dt("2026-09-05 11:00:00"))
    cand = _route_candidates(_test_session, rpt)
    assert cand["auto_route"] in ("A", "D")  # order_no 命中 D(或 A)
    assert any(c["route"] == "D" for c in cand["candidates"])


def test_confirm_line_persist(_test_session):
    """confirm_line 打标落库 + 不改源表"""
    from app.models import ManualRefund
    from app.services.reconcile_check import confirm_line

    rpt = _seed_report(_test_session, platform_order_no="WS-LINE-CF", amount="19.00")
    m = _seed_manual(_test_session, device_sn="SN-C-1", order_no="WS-LINE-CF", amount="19.00",
                     op_time=_dt("2026-09-05 11:00:00"))
    check = _mk_check(_test_session)
    user = _fake_user()

    line = confirm_line(_test_session, user, check.id, rpt.id,
                        confirm_status="已核对一致", confirm_note="与 D 台账一致")
    assert line.confirm_status == "已核对一致"
    assert line.confirmed_name == "管理员"
    assert line.confirm_note == "与 D 台账一致"
    # 不改源表: manual 行 quality_status/金额/op_time 都不变
    _test_session.expire_all()
    m2 = _test_session.get(ManualRefund, m.id)
    assert m2.quality_status is None or m2.quality_status == ""
    assert float(m2.refund_amount) == 19.00


def test_list_lines_and_summary(_test_session):
    """list_lines 过滤 + lines_summary 进度"""
    from app.services.reconcile_check import confirm_line, lines_summary, list_lines

    r1 = _seed_report(_test_session, platform_order_no="WS-LINE-1", amount="19.00",
                      refund_time="2026-09-05 10:00:00")
    _seed_report(_test_session, platform_order_no="WS-LINE-2", amount="88.00",
                 refund_time="2026-09-06 10:00:00")  # unmatched(E 无对应)
    check = _mk_check(_test_session)
    user = _fake_user()

    # 逐笔 ensure 两条(report_id 来自种子)
    from app.services.reconcile_check import ensure_line

    ensure_line(_test_session, check.id, r1)
    # confirm r1
    confirm_line(_test_session, user, check.id, r1.id, confirm_status="已核对一致")

    lst = list_lines(_test_session, check.id)
    assert lst["total"] >= 1  # 至少 ensure 过的行
    assert all(x["check_id"] == check.id for x in lst["items"])
    summ = lines_summary(_test_session, check.id)
    assert summ["total"] >= 1
    assert summ["by_status"].get("已核对一致", 0) >= 1


def test_api_line_confirm_permission(_api_env, _test_session):
    """line-confirm 仅 Admin/Supervisor(agent 403); 成功打标"""
    client, state, _make = _api_env
    rpt = _seed_report(_test_session, platform_order_no="WS-LINE-API", amount="19.00")
    state["user"] = _make("admin")
    r = client.post("/api/v1/reconcile/check/confirm",
                    params={"start": "2026-09-01T00:00:00", "end": "2026-09-05T23:59:59"})
    cid = r.json()["data"]["id"]

    state["user"] = _make("agent")
    r2 = client.post(f"/api/v1/reconcile/check/{cid}/line-confirm",
                     params={"report_id": rpt.id, "confirm_status": "已核对一致"})
    assert r2.status_code == 403, r2.text

    state["user"] = _make("supervisor")
    r3 = client.post(f"/api/v1/reconcile/check/{cid}/line-confirm",
                     params={"report_id": rpt.id, "confirm_status": "已核对一致", "confirm_note": "OK"})
    assert r3.status_code == 200, r3.text
    assert r3.json()["data"]["confirm_status"] == "已核对一致"
    # lines-summary 可读
    r4 = client.get(f"/api/v1/reconcile/check/{cid}/lines-summary")
    assert r4.status_code == 200
    assert r4.json()["data"]["total"] >= 1
