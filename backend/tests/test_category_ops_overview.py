# -*- coding: utf-8 -*-
"""B3-10 (#254) 分品类运营看板: 设备类型/套餐/商户号聚合运营 KPI"""

import uuid as _uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.combo_dict import ComboDict, ComboNameAlias
from app.models.device import Device, DimDeviceType
from app.models.device_combo import DeviceCombo
from app.models.device_flow_usage import DeviceFlowUsage

# from app.models.merchant import DeviceMerchantLink, Merchant  # #271: 不再使用
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.user import RoleEnum, User
from app.services.analytics.category import category_ops_overview


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
    return f"{prefix}-{_uuid.uuid4().hex[:10]}"


def _mk_device(db, sn: str, **kw) -> Device:
    dev = db.get(Device, sn)
    if dev:
        return dev
    dev = Device(sn=sn, **kw)
    db.add(dev)
    db.flush()
    return dev


def _mk_recharge(db, sn: str, amount: Decimal, days_ago: int = 0, **kw) -> DeviceCombo:
    c = DeviceCombo(
        recharge_order_no=_uniq("RO"), device_sn=sn,
        package_name=kw.pop("package_name", _uniq("套餐")), actual_amount=amount,
        created_at_raw=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(c)
    db.flush()
    return c


def _mk_flow(db, sn: str, month: str = "2026-10") -> DeviceFlowUsage:
    f = DeviceFlowUsage(
        device_sn=sn, stat_month=month, flow_gb=Decimal("10.5"),
        month_complete=True,
    )
    db.add(f)
    db.flush()
    return f


def _mk_refund(db, sn: str, days_ago: int = 0) -> Refund:
    # #332 T8: 退款指标取数 std_refund_online, seed 须满足 A 路采纳规则
    # (已退款 且 方式=线上/NULL), 否则采纳作业盖 A_EX_STATUS_NULL 不进 std。
    r = Refund(
        refund_no=_uniq("RF"), device_sn=sn,
        refund_method="线上", status="已退款",
        created_at_raw=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(r)
    db.flush()
    return r


def _mk_complaint(db, sn: str, days_ago: int = 0) -> ServiceTicket:
    t = ServiceTicket(
        ticket_no=_uniq("GD"), device_sn=sn,
        service_category="投诉", status="处理中",
        register_time=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(t)
    db.flush()
    return t


def test_ops_overview_device_type(db):
    """设备类型维: 聚合 + 活跃/充值/退款/投诉四级指标。

    ⚠️ #325 对齐: #293 已把 device_type 维的 key 源从 DimDeviceType.type_code
    改为 devices.device_type(中文名, 因全表 type_code 历史为空不可用) ——
    聚合不再读字典表, 故此处直接给设备挂 device_type 中文名作 key。
    """
    dt_name = _uniq("4G路由器")
    sn1, sn2 = _uniq("SN-T1"), _uniq("SN-T2")
    _mk_device(db, sn1, device_type=dt_name)
    _mk_device(db, sn2, device_type=dt_name)
    _mk_recharge(db, sn1, Decimal("100.00"))
    _mk_flow(db, sn2)
    _mk_refund(db, sn1)
    _mk_complaint(db, sn1)
    db.flush()
    # #332 T8: 退款指标取数 std_refund_online —— 先跑采纳作业
    from app.services.governance.refund_adoption import run_refund_adoption

    run_refund_adoption(db, routes="A", commit=False)

    res = category_ops_overview(db, dim="device_type", limit=10)
    assert res["dim"] == "device_type"
    items = {r["key"]: r for r in res["items"]}
    assert dt_name in items
    it = items[dt_name]
    assert it["device_cnt"] == 2
    assert it["active_cnt"] == 2
    assert it["recharge_amount"] == 100.0
    assert it["refund_cnt"] == 1
    assert it["complaint_cnt"] == 1
    assert it["complaint_rate"] == 50.0


def test_ops_overview_combo_canonical_name(db):
    """套餐维: 归一码命中用标准名, 未命中回落原始名。"""
    cd = ComboDict(combo_code=_uniq("CMB"), canonical_name=_uniq("归一套餐"))
    db.add(cd)
    db.flush()
    raw_hit, raw_miss = _uniq("原始套餐A"), _uniq("原始套餐B")
    db.add(ComboNameAlias(
        combo_code=cd.combo_code, raw_package_name=raw_hit,
        effective_from=date.today() - timedelta(days=1), is_current=True,
    ))
    sn_hit = _uniq("SN-P1")
    _mk_device(db, sn_hit, current_combo_name=raw_hit)
    _mk_recharge(db, sn_hit, Decimal("200.00"))
    sn_miss = _uniq("SN-P2")
    _mk_device(db, sn_miss, current_combo_name=raw_miss)
    _mk_recharge(db, sn_miss, Decimal("50.00"))
    db.flush()

    res = category_ops_overview(db, dim="combo", limit=10)
    names = [r["name"] for r in res["items"]]
    assert cd.canonical_name in names   # 归一命中
    assert raw_miss in names             # 未命中回落原始名


def test_ops_overview_merchant_from_devices(db):
    """#271: 商户维改走 devices.merchant_name（links/merchants 表生产全空）。"""
    m_name = _uniq("商户")
    sn_a, sn_b = _uniq("SN-M1"), _uniq("SN-M2")
    _mk_device(db, sn_a, merchant_name=m_name)
    _mk_device(db, sn_b, merchant_name=m_name)
    _mk_device(db, _uniq("SN-M3"), merchant_name=None)   # 未归一
    _mk_recharge(db, sn_a, Decimal("300.00"))
    _mk_complaint(db, sn_a)
    db.flush()

    res = category_ops_overview(db, dim="merchant", limit=10)
    items = {r["key"]: r for r in res["items"]}
    assert m_name in items
    assert items[m_name]["device_cnt"] == 2
    assert items[m_name]["recharge_amount"] == 300.0
    assert items[m_name]["complaint_cnt"] == 1


def test_ops_overview_limit_and_order(db):
    """limit 截断 + 按充值金额降序。"""
    dt = DimDeviceType(type_code=_uniq("LIM"), type_name=_uniq("限量类型"))
    db.add(dt)
    db.flush()
    for i in range(3):
        sn = _uniq(f"SN-L{i}")
        _mk_device(db, sn, device_type_code=dt.type_code)
        _mk_recharge(db, sn, Decimal(f"{100 + i * 100}.00"))
    db.flush()

    res = category_ops_overview(db, dim="device_type", limit=2)
    assert len(res["items"]) <= 2
    amounts = [r["recharge_amount"] for r in res["items"]]
    assert amounts == sorted(amounts, reverse=True)


def test_ops_overview_combo_no_amplification(db):
    """#270: 设备多笔充值时退款/投诉不重复计数（回归保护）。"""
    raw = _uniq("防放大套餐")
    sn = _uniq("SN-AMP")
    _mk_device(db, sn, current_combo_name=raw)
    # 同一设备 3 笔充值（一对多流水），package_name 与 current_combo_name 对齐
    for _ in range(3):
        _mk_recharge(db, sn, Decimal("10.00"), package_name=raw)
    # 各 1 笔退款/投诉
    _mk_refund(db, sn)
    _mk_complaint(db, sn)
    db.flush()
    # #332 T8: 退款指标取数 std_refund_online —— 先跑采纳作业
    from app.services.governance.refund_adoption import run_refund_adoption

    run_refund_adoption(db, routes="A", commit=False)

    res = category_ops_overview(db, dim="combo", limit=50)
    items = {r["key"]: r for r in res["items"]}
    assert raw in items
    it = items[raw]
    # 放大前: refund_cnt/complaint_cnt 会是 3（按充值流水行数）
    assert it["refund_cnt"] == 1
    assert it["complaint_cnt"] == 1
    # 充值金额不受影响（仍按流水求和）
    assert it["recharge_amount"] == 30.0


def test_ops_overview_endpoint(authed_client, db):
    """端点冒烟: 200 + code=0 + 结构齐全。"""
    dt = DimDeviceType(type_code=_uniq("EP"), type_name=_uniq("端点类型"))
    db.add(dt)
    db.flush()
    sn = _uniq("SN-EP")
    _mk_device(db, sn, device_type_code=dt.type_code)
    _mk_recharge(db, sn, Decimal("88.00"))
    db.flush()

    resp = authed_client.get("/api/v1/analytics/category/ops-overview?dim=device_type&limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["dim"] == "device_type"
    assert "items" in data
    assert "start" in data and "end" in data
    assert data["total_dim_count"] >= 1
    item = data["items"][0]
    for k in ("key", "name", "device_cnt", "active_cnt", "recharge_amount",
              "refund_cnt", "complaint_cnt", "complaint_rate"):
        assert k in item
