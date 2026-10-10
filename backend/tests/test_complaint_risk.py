# -*- coding: utf-8 -*-
"""B3-4 (#250) 夜间投诉风险预警 v1: 确定性四维评分引擎 + 优先清单端点"""

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.complaint_same_source import ComplaintSameSourceGroup, ComplaintSameSourceMember
from app.models.cs_record import CsRecord
from app.models.device import Device
from app.models.device_combo import DeviceCombo
from app.models.device_flow_usage import DeviceFlowUsage
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.models.user import RoleEnum, User
from app.services.complaint_risk_service import (
    _contact_score,
    _flow_score,
    _level,
    _recharge_score,
    _repeat_score,
    compute_complaint_risk_priority_list,
)


@pytest.fixture()
def authed_client(db):
    """带鉴权的 TestClient: 覆盖 get_current_user + get_db, 与 db fixture 共享事务。"""
    fake = User(id=1, username="tester", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _uniq(prefix: str) -> str:
    """唯一化 SN/单号, 避免撞测试库历史残留。"""
    return f"{prefix}-{_uuid.uuid4().hex[:10]}"


def _mk_device(db, sn: str) -> Device:
    dev = db.get(Device, sn)
    if dev:
        return dev
    dev = Device(sn=sn)
    db.add(dev)
    db.flush()
    return dev


def _mk_combo(db, sn: str, days_ago: int, amount: str) -> DeviceCombo:
    c = DeviceCombo(
        recharge_order_no=_uniq("RO"),
        device_sn=sn,
        actual_amount=Decimal(amount),
        created_at_raw=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(c)
    db.flush()
    return c


def _mk_flow(db, sn: str, month: str, gb: str, complete: bool = True) -> DeviceFlowUsage:
    f = DeviceFlowUsage(
        device_sn=sn, stat_month=month, flow_gb=Decimal(gb), month_complete=complete,
        fetched_at=datetime.now(UTC),
    )
    db.add(f)
    db.flush()
    return f


def _mk_cs(db, sn: str, days_ago: int) -> CsRecord:
    now = datetime.now(UTC) - timedelta(days=days_ago)
    r = CsRecord(
        device_sn=sn, operation_time=now, operator="企微-测试",
        problem_category="网络问题", problem_type=_uniq("PT"),
        consult_time=now, problem_create_time=now,
    )
    db.add(r)
    db.flush()
    return r


def _mk_complaint_ticket(db, sn: str, days_ago: int) -> ServiceTicket:
    t = ServiceTicket(
        ticket_no=_uniq("GD"), device_sn=sn,
        service_category="投诉", status="处理中",
        register_time=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(t)
    db.flush()
    return t


def _mk_external(db, sn: str, days_ago: int) -> ExternalComplaint:
    e = ExternalComplaint(
        complaint_no=_uniq("EC"), device_sn=sn,
        complaint_date=datetime.now(UTC) - timedelta(days=days_ago),
        platform="工信部",
    )
    db.add(e)
    db.flush()
    return e


# ── 单元: 各维评分函数边界 ──

def test_recharge_score_boundaries():
    assert _recharge_score(0, Decimal("0"), Decimal("500")) == 0
    assert _recharge_score(1, Decimal("100"), Decimal("500")) == 40
    assert _recharge_score(2, Decimal("100"), Decimal("500")) == 70
    assert _recharge_score(3, Decimal("50"), Decimal("500")) == 100
    # 金额达阈值即使 1 笔也满分(高价值)
    assert _recharge_score(1, Decimal("600"), Decimal("500")) == 100


def test_flow_score_boundaries():
    assert _flow_score(None, 5.0) == 30          # 无记录: 未知中性
    assert _flow_score(3.0, 5.0) == 100          # < 阈值
    assert _flow_score(7.0, 5.0) == 60           # < 2×阈值
    assert _flow_score(20.0, 5.0) == 10          # 正常


def test_contact_score_boundaries():
    assert _contact_score(0) == 0
    assert _contact_score(1) == 50
    assert _contact_score(2) == 75
    assert _contact_score(5) == 100


def test_repeat_score_boundaries():
    assert _repeat_score(0, False, 0) == 0
    assert _repeat_score(1, False, 0) == 25
    assert _repeat_score(3, False, 0) == 75      # 工单封顶 75
    assert _repeat_score(5, False, 0) == 75      # 超出仍封顶
    assert _repeat_score(1, True, 0) == 40       # +同源组 15
    assert _repeat_score(0, False, 2) == 20      # 外部投诉 each 10
    assert _repeat_score(3, True, 5) == 100      # 总封顶 100


def test_level_thresholds():
    assert _level(75) == "critical"
    assert _level(50) == "high"
    assert _level(30) == "medium"
    assert _level(10) == "low"


# ── 集成: 全量评分管线 ──

def test_priority_list_full_pipeline(db):
    """高风险设备(充值多+流量少+接待多+重复投诉)应排在顶部且满分维度正确。"""
    sn_hi = _uniq("SN-HI")
    sn_lo = _uniq("SN-LO")
    _mk_device(db, sn_hi)
    _mk_device(db, sn_lo)

    # 高风险: 3 笔充值 + 流量 2GB + 3 次接待 + 2 投诉工单 + 1 外部投诉
    for d in (5, 15, 40):
        _mk_combo(db, sn_hi, d, "200")
    _mk_flow(db, sn_hi, "2026-09", "2.0")
    for d in (1, 3, 7):
        _mk_cs(db, sn_hi, d)
    for d in (10, 20):
        _mk_complaint_ticket(db, sn_hi, d)
    _mk_external(db, sn_hi, 30)

    # 低风险: 仅 1 笔小额充值, 流量正常
    _mk_combo(db, sn_lo, 10, "50")
    _mk_flow(db, sn_lo, "2026-09", "30.0")

    result = compute_complaint_risk_priority_list(db, limit=500)
    items = {i["device_sn"]: i for i in result["items"]}

    assert sn_hi in items and sn_lo in items
    hi, lo = items[sn_hi], items[sn_lo]
    assert hi["total"] > lo["total"]
    assert hi["scores"]["recharge"] == 100
    assert hi["scores"]["low_flow"] == 100
    assert hi["scores"]["contact"] == 100
    assert hi["scores"]["repeat"] == 60  # 2 工单 50 + 1 外部 10
    assert hi["level"] == "critical"


def test_priority_list_repeat_score_composition(db):
    """同题重复维: 2 工单(50) + 1 外部(10) = 60, 无同源组。"""
    sn = _uniq("SN-RP")
    _mk_device(db, sn)
    _mk_complaint_ticket(db, sn, 5)
    _mk_complaint_ticket(db, sn, 15)
    _mk_external(db, sn, 60)

    result = compute_complaint_risk_priority_list(db, limit=500)
    item = next(i for i in result["items"] if i["device_sn"] == sn)
    assert item["scores"]["repeat"] == 60
    assert item["facts"]["complaint_ticket_cnt_90d"] == 2
    assert item["facts"]["external_complaint_cnt_180d"] == 1
    assert item["facts"]["in_same_source_group"] is False


def test_priority_list_same_source_bonus(db):
    """同源组设备 repeat 维 +15。"""
    sn = _uniq("SN-SS")
    _mk_device(db, sn)
    e1 = _mk_external(db, sn, 10)
    e2 = _mk_external(db, sn, 20)
    grp = ComplaintSameSourceGroup(
        rule_version="v5", device_sn=sn, status="auto", member_count=2,
        first_complaint_date=e2.complaint_date, latest_complaint_date=e1.complaint_date,
    )
    db.add(grp)
    db.flush()
    db.add(ComplaintSameSourceMember(group_id=grp.id, complaint_id=e1.id))
    db.add(ComplaintSameSourceMember(group_id=grp.id, complaint_id=e2.id))
    db.flush()

    result = compute_complaint_risk_priority_list(db, limit=500)
    item = next(i for i in result["items"] if i["device_sn"] == sn)
    # 0 工单 + 同源 15 + 2 外部 20 = 35
    assert item["scores"]["repeat"] == 35
    assert item["facts"]["in_same_source_group"] is True


def test_priority_list_new_device_no_crash(db):
    """全新设备(无任何维度事实)不输出——无信号不构成专员动作对象。"""
    sn = _uniq("SN-NEW")
    _mk_device(db, sn)
    result = compute_complaint_risk_priority_list(db, limit=500)
    assert all(i["device_sn"] != sn for i in result["items"])


def test_priority_list_window_exclusion(db):
    """窗口外事实不计分: 100 天前充值(超 90 天窗) → recharge 维 0。"""
    sn = _uniq("SN-OLD")
    _mk_device(db, sn)
    _mk_combo(db, sn, 100, "1000")
    result = compute_complaint_risk_priority_list(db, limit=500)
    item = next((i for i in result["items"] if i["device_sn"] == sn), None)
    # 无流量记录 flow=30 仍会输出, 但 recharge 维必须为 0
    if item is not None:
        assert item["scores"]["recharge"] == 0
        assert item["facts"]["recharge_cnt"] == 0


def test_priority_list_min_score_filter(db):
    """min_score 过滤: 只输出总分 ≥ 阈值的设备。"""
    sn = _uniq("SN-MS")
    _mk_device(db, sn)
    _mk_cs(db, sn, 1)  # contact=50 → total = 50*20/100 = 10
    result = compute_complaint_risk_priority_list(db, limit=500, min_score=50)
    assert all(i["device_sn"] != sn for i in result["items"])
    result2 = compute_complaint_risk_priority_list(db, limit=500, min_score=5)
    assert any(i["device_sn"] == sn for i in result2["items"])


# ── 端点冒烟 ──

def test_risk_priority_list_endpoint(authed_client, db):
    sn = _uniq("SN-EP")
    _mk_device(db, sn)
    _mk_combo(db, sn, 3, "600")
    resp = authed_client.get("/api/v1/complaints/risk-priority-list", params={"limit": 500})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert "total_scored" in data and "items" in data
    item = next((i for i in data["items"] if i["device_sn"] == sn), None)
    assert item is not None
    assert item["scores"]["recharge"] == 100
    assert item["facts"]["recharge_amount_yuan"] == 600.0


def test_risk_priority_list_endpoint_auth_required(db):
    """未鉴权访问应 401/403。"""
    from fastapi.testclient import TestClient as TC
    with TC(app) as c:
        resp = c.get("/api/v1/complaints/risk-priority-list")
    assert resp.status_code in (401, 403)
