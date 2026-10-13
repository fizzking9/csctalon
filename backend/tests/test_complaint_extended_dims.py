# -*- coding: utf-8 -*-
"""B3-11 (#252) 投诉分析扩展维度: 店铺/商户号/设备型号/套餐/激活后时长"""

import uuid as _uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.combo_dict import ComboDict, ComboNameAlias
from app.models.device import Device, DimDeviceType
from app.models.merchant import DeviceMerchantLink, Merchant
from app.models.shop import DeviceShopLink, Shop
from app.models.ticket import ServiceTicket
from app.models.user import RoleEnum, User
from app.services.analytics import complaint_extended_dims


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


def _mk_complaint(db, sn: str, days_ago: int = 0) -> ServiceTicket:
    t = ServiceTicket(
        ticket_no=_uniq("GD"), device_sn=sn,
        service_category="投诉", status="处理中",
        register_time=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(t)
    db.flush()
    return t


def _dim(db) -> dict:
    """宽窗口调用服务函数。"""
    today = datetime.now(UTC).date()
    return complaint_extended_dims(
        db, start=str(today - timedelta(days=2)), end=str(today), limit=10,
    )


def _names(rows: list[dict]) -> list[str]:
    return [r["name"] for r in rows]


def test_dims_shop_linked_and_unlinked(db):
    """店铺维: 已建链设备落店铺名桶, 未建链落 (未建链店铺)。"""
    sn_link, sn_free = _uniq("SN-S1"), _uniq("SN-S2")
    _mk_device(db, sn_link)
    _mk_device(db, sn_free)
    shop = Shop(shop_name=_uniq("店铺"), status="active")
    db.add(shop)
    db.flush()
    db.add(DeviceShopLink(
        device_sn=sn_link, shop_pk=shop.id,
        effective_from=date.today(), is_current=True, match_source="manual",
    ))
    _mk_complaint(db, sn_link)
    _mk_complaint(db, sn_free)
    db.flush()

    by_shop = _dim(db)["by_shop"]
    names = _names(by_shop)
    assert shop.shop_name in names
    assert "(未建链店铺)" in names


def test_dims_merchant_linked_and_unlinked(db):
    """商户号维: 已建链设备落商户名桶, 未建链落 (未建链商户号)。"""
    sn_link, sn_free = _uniq("SN-M1"), _uniq("SN-M2")
    _mk_device(db, sn_link)
    _mk_device(db, sn_free)
    m = Merchant(sub_name=_uniq("商户"), merchant_id=_uniq("MCH"))
    db.add(m)
    db.flush()
    db.add(DeviceMerchantLink(
        device_sn=sn_link, merchant_pk=m.id,
        effective_from=date.today(), is_current=True, match_source="manual",
    ))
    _mk_complaint(db, sn_link)
    _mk_complaint(db, sn_free)
    db.flush()

    by_merchant = _dim(db)["by_merchant"]
    names = _names(by_merchant)
    assert m.sub_name in names
    assert "(未建链商户号)" in names


def test_dims_device_type_normalized_and_fallback(db):
    """设备型号维: 归一码命中用 type_name, 无码回落原始 device_type 文本。"""
    dt = DimDeviceType(type_code=_uniq("CPE"), type_name=_uniq("4G路由器"))
    db.add(dt)
    db.flush()
    sn_norm = _uniq("SN-T1")
    _mk_device(db, sn_norm, device_type_code=dt.type_code)
    sn_raw = _uniq("SN-T2")
    _mk_device(db, sn_raw, device_type=_uniq("原始型号"))
    _mk_complaint(db, sn_norm)
    _mk_complaint(db, sn_raw)
    db.flush()

    names = _names(_dim(db)["by_device_type"])
    assert dt.type_name in names
    assert db.get(Device, sn_raw).device_type in names


def test_dims_package_canonical_and_fallback(db):
    """套餐维: 当前别名命中用归一名, 未命中回落原始名。"""
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
    sn_miss = _uniq("SN-P2")
    _mk_device(db, sn_miss, current_combo_name=raw_miss)
    _mk_complaint(db, sn_hit)
    _mk_complaint(db, sn_miss)
    db.flush()

    names = _names(_dim(db)["by_package"])
    assert cd.canonical_name in names   # 归一命中
    assert raw_hit not in names          # 原始名被归一替代
    assert raw_miss in names             # 未命中回落原始名


def test_dims_active_days_buckets(db):
    """激活后时长维(#295 五桶): 10/20/100/200/400 天激活各落对应桶; 无激活记录落 (无激活记录)。

    基准 = bms_orders 最早激活日 MIN(active_date)（#291 收敛, 原 #252 用 devices.active_time）。
    """
    from app.models.bms_order import BmsOrder
    # 10→0-15, 20→16-30, 100→31-180, 200→181-365, 400→>365
    for days, bucket in [(10, "0-15天"), (20, "16-30天"), (100, "31-180天"),
                         (200, "181-365天"), (400, ">365天")]:
        sn = _uniq(f"SN-A{days}")
        _mk_device(db, sn)
        db.add(BmsOrder(order_no=_uniq("OD"), device_sn=sn,
                        active_date=datetime.now(UTC) - timedelta(days=days)))
        _mk_complaint(db, sn)
    sn_no = _uniq("SN-ANONE")
    _mk_device(db, sn_no)  # 无 bms_orders 激活记录 → (无激活记录)
    _mk_complaint(db, sn_no)
    db.flush()

    rows = _dim(db)["by_active_days"]
    # #295 五桶 + (无SN) + 无激活记录, 桶名与 _core.ACTIVE_AGE_BUCKETS 单一来源一致
    # #311 方案D: (无SN) 单独桶(投诉侧 SN 100% 有值, 该桶 count=0, 但桶列表统一)
    assert _names(rows) == ["0-15天", "16-30天", "31-180天", "181-365天", ">365天", "(无SN)", "(无激活记录)"]
    amap = {r["name"]: r["count"] for r in rows}
    assert amap["0-15天"] >= 1
    assert amap["16-30天"] >= 1
    assert amap["31-180天"] >= 1
    assert amap["181-365天"] >= 1
    assert amap[">365天"] >= 1
    assert amap["(无激活记录)"] >= 1


def test_dims_window_exclusion(db):
    """窗口外投诉不计入: 10 天前工单在 2 天窗口内不可见。"""
    sn = _uniq("SN-OLD")
    shop_name = _uniq("老店铺")
    _mk_device(db, sn)
    shop = Shop(shop_name=shop_name, status="active")
    db.add(shop)
    db.flush()
    db.add(DeviceShopLink(
        device_sn=sn, shop_pk=shop.id,
        effective_from=date.today() - timedelta(days=30), is_current=True,
        match_source="manual",
    ))
    _mk_complaint(db, sn, days_ago=10)
    db.flush()

    assert shop_name not in _names(_dim(db)["by_shop"])


def test_dims_non_complaint_excluded(db):
    """非投诉工单(退款类)不计入投诉维度。"""
    sn = _uniq("SN-RF")
    type_name = _uniq("退款类型机")
    _mk_device(db, sn, device_type=type_name)
    t = ServiceTicket(
        ticket_no=_uniq("GD"), device_sn=sn,
        service_category="退款", status="处理中",
        register_time=datetime.now(UTC),
    )
    db.add(t)
    db.flush()

    assert type_name not in _names(_dim(db)["by_device_type"])


def test_extended_dims_endpoint(authed_client, db):
    """端点冒烟: 200 + code=0 + 五维键齐全。"""
    sn = _uniq("SN-EP")
    _mk_device(db, sn)
    _mk_complaint(db, sn)
    resp = authed_client.get("/api/v1/complaints/extended-dims")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    for key in ("by_shop", "by_merchant", "by_device_type", "by_package", "by_active_days"):
        assert key in data
        assert isinstance(data[key], list)
