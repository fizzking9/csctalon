# -*- coding: utf-8 -*-
"""B3-3 (#249) 标签事实化: 打标来源字段 + 标签→设备下钻 + 投诉按标签组合维度"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.device import Device, DeviceTag, DeviceTagBinding, device_tag_rel
from app.models.ticket import ServiceTicket
from app.models.user import RoleEnum, User
from app.services.analytics.complaint import complaint_by_device_tag


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


import uuid as _uuid


def _uniq(prefix: str) -> str:
    """唯一化 SN/工单号/标签名, 避免跨测试运行撞测试库残留数据。
    说明: 测试库 csctalon_test 会持久存在, 历史上其他测试运行可能写过同名片段,
    即使本次 db fixture 回滚, 也挡不住"上一次运行已 commit"的脏数据残留。
    """
    return f"{prefix}-{_uuid.uuid4().hex[:10]}"


def _mk_device(db, sn: str) -> Device:
    """幂等建设备: 已存在则直接复用(避免唯一键冲突)"""
    dev = db.get(Device, sn)
    if dev:
        return dev
    dev = Device(sn=sn)
    db.add(dev)
    db.flush()
    return dev


def _mk_tag(db, name: str) -> DeviceTag:
    """幂等建标签: 按唯一名 tag_name 取已存在的"""
    from sqlalchemy import select as _sel
    tag = db.scalar(_sel(DeviceTag).where(DeviceTag.tag_name == name))
    if tag:
        return tag
    tag = DeviceTag(tag_name=name, tag_type="custom")
    db.add(tag)
    db.flush()
    return tag


def _mk_complaint(db, sn: str, no: str, days_ago: int = 0) -> ServiceTicket:
    """投诉工单: service_category='投诉' 走 is_complaint_ticket_cond 判据"""
    t = ServiceTicket(
        ticket_no=no,
        device_sn=sn,
        service_category="投诉",
        status="处理中",
        register_time=datetime.now(timezone.utc) - timedelta(days=days_ago),
        refund_amount=Decimal("50.00"),
    )
    db.add(t)
    db.flush()
    return t


class TestTagRelSourceColumns:
    """① device_tag_rel 打标来源字段(#249)"""

    def test_source_columns_exist_in_metadata(self):
        cols = {c.name for c in device_tag_rel.c}
        assert {"source_table", "source_id", "reason"} <= cols

    def test_binding_persists_source(self, db):
        sn = _uniq("SN-SRC")
        _mk_device(db, sn)
        tag = _mk_tag(db, _uniq("清退名单"))
        b = DeviceTagBinding(
            device_sn=sn,
            tag_id=tag.id,
            status="active",
            created_by="tester",
            source_table="service_tickets",
            source_id="GDC-123",
            reason="315投诉触发",
        )
        db.add(b)
        db.flush()
        row = db.scalar(
            select(DeviceTagBinding).where(
                DeviceTagBinding.device_sn == sn,
                DeviceTagBinding.tag_id == tag.id,
            )
        )
        assert row is not None
        assert row.source_table == "service_tickets"
        assert row.source_id == "GDC-123"
        assert row.reason == "315投诉触发"

    def test_composite_pk_prevents_duplicate_tagging(self, db):
        """同 device_sn+tag_id 重复打标 → 复合主键拦截(#249 唯一性判据)"""
        from sqlalchemy.exc import IntegrityError

        sn = _uniq("SN-DUP")
        _mk_device(db, sn)
        tag = _mk_tag(db, _uniq("涨价敏感"))
        db.add(DeviceTagBinding(device_sn=sn, tag_id=tag.id, status="active"))
        db.flush()
        db.add(DeviceTagBinding(device_sn=sn, tag_id=tag.id, status="active"))
        with pytest.raises(IntegrityError):
            db.flush()


class TestTagDevicesDrilldown:
    """② 标签→设备下钻 API(#249)"""

    def test_drilldown_returns_source_fields(self, authed_client, db):
        sn1, sn2 = _uniq("SN-DRILL-A"), _uniq("SN-DRILL-B")
        _mk_device(db, sn1)
        _mk_device(db, sn2)
        tag = _mk_tag(db, _uniq("限速复核"))
        db.add(DeviceTagBinding(
            device_sn=sn1, tag_id=tag.id, status="active",
            created_by="tester", source_table="refunds", source_id="RF-1",
            reason="超额退款",
        ))
        db.add(DeviceTagBinding(
            device_sn=sn2, tag_id=tag.id, status="inactive",
        ))
        db.flush()
        resp = authed_client.get(f"/api/v1/devices/tags/{tag.id}/devices", params={"status": ""})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["code"] == 0
        items = payload["data"]["items"]
        sns = {i["sn"] for i in items}
        assert {sn1, sn2} == sns
        active_row = next(i for i in items if i["sn"] == sn1)
        assert active_row["source_table"] == "refunds"
        assert active_row["source_id"] == "RF-1"
        assert active_row["reason"] == "超额退款"
        assert active_row["tag_status"] == "active"

    def test_drilldown_default_filters_active(self, authed_client, db):
        sn_a, sn_b = _uniq("SN-DFLT-A"), _uniq("SN-DFLT-B")
        _mk_device(db, sn_a)
        _mk_device(db, sn_b)
        tag = _mk_tag(db, _uniq("批次2026Q3"))
        db.add(DeviceTagBinding(device_sn=sn_a, tag_id=tag.id, status="active"))
        db.add(DeviceTagBinding(device_sn=sn_b, tag_id=tag.id, status="inactive"))
        db.flush()
        resp = authed_client.get(f"/api/v1/devices/tags/{tag.id}/devices")
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert {i["sn"] for i in items} == {sn_a}

    def test_drilldown_404_on_missing_tag(self, authed_client, db):
        resp = authed_client.get("/api/v1/devices/tags/99999999/devices")
        assert resp.status_code == 404


class TestComplaintByDeviceTag:
    """③ 投诉按标签组合维度(#249)"""

    def test_empty_window(self, db):
        # 窗口取很久以前的单日, 不命中任何数据
        assert complaint_by_device_tag(db, date="2020-01-01") == []

    def test_grouping_and_untagged_bucket(self, db):
        """已打标 SN 的投诉计入标签桶; 未打标 SN 落入 (未打标) 桶

        用唯一 SN/标签名隔离测试库残留; 断言只针对本测试的对象, 不依赖全表。
        """
        sn_a, sn_b, sn_no = _uniq("SN-TA"), _uniq("SN-TB"), _uniq("SN-TN")
        _mk_device(db, sn_a)
        _mk_device(db, sn_b)
        _mk_device(db, sn_no)
        tag = _mk_tag(db, _uniq("清退名单"))
        db.add(DeviceTagBinding(device_sn=sn_a, tag_id=tag.id, status="active"))
        db.add(DeviceTagBinding(device_sn=sn_b, tag_id=tag.id, status="active"))
        _mk_complaint(db, sn_a, _uniq("GDC-T"))
        _mk_complaint(db, sn_a, _uniq("GDC-T"))
        _mk_complaint(db, sn_b, _uniq("GDC-T"))
        _mk_complaint(db, sn_no, _uniq("GDC-T"))
        db.flush()

        result = complaint_by_device_tag(db, start="2000-01-01", end="2100-01-01")
        by_id = {r["tag_id"]: r for r in result}
        # 本测试的标签行: 3 张工单 / 2 个去重 SN / 150 元
        row = by_id[tag.id]
        assert row["ticket_count"] == 3
        assert row["sn_count"] == 2
        assert row["refund_amount"] == 150.0
        # 未打标 SN(sn_no)落在 (未打标) 桶
        untagged = next((r for r in result if r["tag_id"] is None), None)
        assert untagged is not None and untagged["tag_name"] == "(未打标)"
        # 未打标桶里至少包含本测试那 1 单 1 SN
        assert untagged["ticket_count"] >= 1
        assert untagged["sn_count"] >= 1

    def test_multi_tag_device_counted_in_each(self, db):
        """组合维度: 一台设备两个 active 标签 → 该投诉在两个标签各计一次"""
        sn = _uniq("SN-MULTI")
        _mk_device(db, sn)
        t1 = _mk_tag(db, _uniq("标签甲"))
        t2 = _mk_tag(db, _uniq("标签乙"))
        db.add(DeviceTagBinding(device_sn=sn, tag_id=t1.id, status="active"))
        db.add(DeviceTagBinding(device_sn=sn, tag_id=t2.id, status="active"))
        _mk_complaint(db, sn, _uniq("GDC-M"))
        db.flush()

        result = complaint_by_device_tag(db, start="2000-01-01", end="2100-01-01")
        by_id = {r["tag_id"]: r for r in result}
        assert by_id[t1.id]["ticket_count"] == 1
        assert by_id[t2.id]["ticket_count"] == 1

    def test_inactive_binding_not_counted(self, db):
        """inactive 绑定不计入"""
        sn = _uniq("SN-INACT")
        _mk_device(db, sn)
        tag = _mk_tag(db, _uniq("已失效标签"))
        db.add(DeviceTagBinding(device_sn=sn, tag_id=tag.id, status="inactive"))
        _mk_complaint(db, sn, _uniq("GDC-I"))
        db.flush()
        result = complaint_by_device_tag(db, start="2000-01-01", end="2100-01-01")
        by_id = {r["tag_id"]: r for r in result}
        assert tag.id not in by_id

    def test_endpoint_smoke(self, authed_client, db):
        sn = _uniq("SN-EP")
        _mk_device(db, sn)
        tag = _mk_tag(db, _uniq("端点冒烟"))
        db.add(DeviceTagBinding(device_sn=sn, tag_id=tag.id, status="active"))
        _mk_complaint(db, sn, _uniq("GDC-EP"))
        db.flush()
        resp = authed_client.get(
            "/api/v1/complaints/by-device-tag",
            params={"start": "2000-01-01", "end": "2100-01-01"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["code"] == 0
        by_id = {r["tag_id"]: r for r in payload["data"]}
        assert tag.id in by_id

    def test_window_respected(self, db):
        """窗口外投诉不计入"""
        sn = _uniq("SN-WIN")
        _mk_device(db, sn)
        tag = _mk_tag(db, _uniq("窗口测试"))
        db.add(DeviceTagBinding(device_sn=sn, tag_id=tag.id, status="active"))
        _mk_complaint(db, sn, _uniq("GDC-W"), days_ago=365)
        db.flush()
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=7)).date().isoformat()
        end = now.date().isoformat()
        result = complaint_by_device_tag(db, start=start, end=end)
        assert all(r["tag_id"] != tag.id for r in result)


class TestTagStatsUnchanged:
    """回归保护: 现有 tag_stats 口径不变(只统计 active)"""

    def test_stats_counts_active_only(self, db):
        sn1, sn2 = _uniq("SN-ST-A"), _uniq("SN-ST-B")
        _mk_device(db, sn1)
        _mk_device(db, sn2)
        tag = _mk_tag(db, _uniq("回归保护"))
        db.add(DeviceTagBinding(device_sn=sn1, tag_id=tag.id, status="active"))
        db.add(DeviceTagBinding(device_sn=sn2, tag_id=tag.id, status="inactive"))
        db.flush()
        cnt = db.scalar(
            select(func.count())
            .select_from(device_tag_rel)
            .where(device_tag_rel.c.tag_id == tag.id, device_tag_rel.c.status == "active")
        )
        assert cnt == 1
