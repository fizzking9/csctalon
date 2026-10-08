# -*- coding: utf-8 -*-
"""B2-2 (#237) AI 数据供给层测试

覆盖 app/services/ai_data_supply.py 四类聚合 + AI 条件注入 + 区间护栏,
以及 app/api/v1/external.py 新增实体端点的鉴权挂载。使用真实 csctalon_test
(事务回滚隔离), 保证 SQL 聚合(CASE/字节求和/人天集合)被真实执行而非 mock。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.models.device import Device, DeviceTag, DeviceTagBinding
from app.models.device_combo import DeviceCombo
from app.models.person import Person
from app.models.schedule import Attendance, Schedule, ShiftTemplate
from app.services import ai_data_supply as ADS

GB = 1024 ** 3


# ───────────────────────── 充值流量 ─────────────────────────

def test_supply_recharge_aggregation_and_unlimited(db):
    today = date.today()
    in_day = datetime.combine(today - timedelta(days=2), time(10, 0))
    db.add_all([
        Device(sn="SNRC00001", merchant_name="商户甲"),
        DeviceCombo(recharge_order_no="R1", device_sn="SNRC00001",
                    actual_amount=Decimal("39.90"), created_at_raw=in_day,
                    available_traffic=2 * GB, used_traffic=GB, remaining_traffic=GB),
        # 无限包: remaining=-1, 计入 unlimited_count, 不应折成 GB
        DeviceCombo(recharge_order_no="R2", device_sn="SNRC00001",
                    actual_amount=Decimal("60.10"), created_at_raw=in_day,
                    available_traffic=-1, used_traffic=0, remaining_traffic=-1),
    ])
    db.flush()
    res = ADS.supply_recharge(db, sn="SNRC00001",
                              start=(today - timedelta(days=3)).isoformat(),
                              end=today.isoformat())
    assert res["recharge_count"] == 2
    assert res["total_amount"] == 100.00          # Decimal→float 元, 2 位
    assert res["available_traffic"] == 2 * GB - 1  # -1 无限包参与字节求和
    assert res["used_traffic_gb"] == 1.0
    assert res["unlimited_count"] == 1
    assert res["_spec"]["source"].startswith("本地 device_combos")


def test_supply_recharge_window_excludes_out_of_range(db):
    today = date.today()
    db.add(Device(sn="SNRC00002"))
    db.add(DeviceCombo(recharge_order_no="ROLD", device_sn="SNRC00002",
                       actual_amount=Decimal("10.00"),
                       created_at_raw=datetime.combine(today - timedelta(days=30), time(9))))
    db.flush()
    # 只查最近 3 天 → 30 天前的套餐被窗口排除
    res = ADS.supply_recharge(db, sn="SNRC00002",
                              start=(today - timedelta(days=3)).isoformat(),
                              end=today.isoformat())
    assert res["recharge_count"] == 0
    assert res["total_amount"] == 0.0


def test_range_validation_rejects_bad_windows(db):
    today = date.today()
    with pytest.raises(ValueError):  # 只给一个
        ADS.supply_recharge(db, start=today.isoformat(), end=None)
    with pytest.raises(ValueError):  # start 晚于 end
        ADS.supply_recharge(db, start=today.isoformat(), end=(today - timedelta(days=1)).isoformat())
    with pytest.raises(ValueError):  # 跨度超 93 天
        ADS.supply_recharge(db, start=(today - timedelta(days=100)).isoformat(), end=today.isoformat())


# ───────────────────────── 设备标签 ─────────────────────────

def test_supply_device_tags_global_and_by_sn(db):
    db.add_all([Device(sn="SNTAG0001"), Device(sn="SNTAG0002")])
    t1 = DeviceTag(tag_name="标签#237-甲", color="red")
    t2 = DeviceTag(tag_name="标签#237-乙", color="blue")
    db.add_all([t1, t2])
    db.flush()
    db.add_all([
        DeviceTagBinding(device_sn="SNTAG0001", tag_id=t1.id, status="active"),
        DeviceTagBinding(device_sn="SNTAG0002", tag_id=t1.id, status="active"),
        DeviceTagBinding(device_sn="SNTAG0001", tag_id=t2.id, status="inactive"),  # 非生效不计
    ])
    db.flush()

    stats = ADS.supply_device_tags(db)
    by_name = {t["tag_name"]: t["sn_count"] for t in stats["tags"]}
    assert by_name["标签#237-甲"] == 2
    # 乙仅一条 inactive 绑定 → 不进入 status=active 的全局分组
    assert "标签#237-乙" not in by_name

    one = ADS.supply_device_tags(db, sn="SNTAG0001")
    assert one["count"] == 1
    assert one["tags"][0]["tag_name"] == "标签#237-甲"


# ───────────────────────── 单设备行为 ─────────────────────────

def test_supply_device_behavior_missing_returns_none(db):
    assert ADS.supply_device_behavior(db, "SN_NOT_EXIST_999") is None


def test_supply_device_behavior_profile(db):
    db.add_all([
        Device(sn="SNBEH0001", merchant_name="商户乙", status="正常"),
        DeviceCombo(recharge_order_no="RB1", device_sn="SNBEH0001",
                    actual_amount=Decimal("12.34"),
                    remaining_traffic=5 * GB, created_at_raw=datetime(2026, 9, 1)),
    ])
    db.flush()
    res = ADS.supply_device_behavior(db, "SNBEH0001", timeline_days=30, timeline_limit=10)
    assert res is not None
    assert res["profile"]["sn"] == "SNBEH0001"
    assert res["profile"]["kpi"]["combo_count"] == 1
    assert res["profile"]["kpi"]["combo_total_amount"] == 12.34
    assert res["profile"]["kpi"]["combo_remaining_bytes"] == 5 * GB
    assert "items" in res["timeline"] and "counts" in res["timeline"]


# ───────────────────────── 排班考勤 ─────────────────────────

def test_supply_schedule_person_day_aggregation(db):
    today = date.today()
    s, e = today - timedelta(days=6), today
    p_a = Person(display_name="员工甲")
    p_b = Person(display_name="员工乙")
    db.add_all([p_a, p_b])
    db.flush()
    shift = ShiftTemplate(name="测试早班#237", start_time="09:00", end_time="18:00")
    db.add(shift)
    db.flush()
    day_a, day_b = today - timedelta(days=1), today - timedelta(days=2)
    db.add_all([
        Schedule(person_id=p_a.id, schedule_date=day_a, shift_template_id=shift.id, status="正常"),
        Schedule(person_id=p_b.id, schedule_date=day_b, shift_template_id=shift.id, status="正常"),
        # 甲有打卡(出勤); 乙无打卡(缺勤)
        Attendance(person_id=p_a.id, attendance_date=day_a,
                   check_in_at=datetime.combine(day_a, time(9, 5))),
    ])
    db.flush()

    res = ADS.supply_schedule(db, s.isoformat(), e.isoformat())
    summ = res["summary"]
    assert summ["scheduled_person_days"] == 2
    assert summ["present_person_days"] == 1
    assert summ["attendance_rate"] == 0.5
    assert {x["shift_name"]: x["person_days"] for x in res["by_shift"]} == {"测试早班#237": 2}
    assert len(res["daily"]) == 2
    assert res["scheduled_no_present"]["total"] == 1
    assert res["scheduled_no_present"]["items"][0]["person_name"] == "员工乙"
    assert res["present_no_schedule"]["total"] == 0


def test_supply_schedule_requires_range(db):
    with pytest.raises(ValueError):
        ADS.supply_schedule(db, None, None)  # type: ignore[arg-type]


# ───────────────────────── AI 条件注入 ─────────────────────────

def test_entity_context_empty_for_plain_question(db):
    # 无设备号、无排班词 → 空串(普通经营问答零影响); 英文单词经 DB 核对不命中
    assert ADS.build_entity_context(db, "今天经营情况怎么样", start=None, end=None) == ""
    assert ADS.build_entity_context(db, "hello world performance overview",
                                    start=None, end=None) == ""


def test_entity_context_injects_device_when_sn_matches(db):
    db.add(Device(sn="SNENT00001", merchant_name="实体商户"))
    db.flush()
    text = ADS.build_entity_context(db, "帮我看看设备 SNENT00001 最近情况", start=None, end=None)
    assert "SNENT00001" in text
    assert "档案" in text and "充值流量" in text


def test_entity_context_injects_schedule_on_keyword(db):
    today = date.today()
    p = Person(display_name="排班员工")
    db.add(p)
    db.flush()
    db.add(Schedule(person_id=p.id, schedule_date=today, status="正常"))
    db.flush()
    text = ADS.build_entity_context(db, "本周排班和出勤率如何",
                                    start=(today - timedelta(days=6)).isoformat(),
                                    end=today.isoformat())
    assert "排班与考勤" in text
    assert "scheduled_person_days" in text


def test_entity_context_swallows_errors(db, monkeypatch):
    # 供给层抛错时实体注入返回空串, 不阻断主问答链路
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(ADS, "_extract_device_sns", lambda d, q: ["SNX"])
    monkeypatch.setattr(ADS, "supply_device_behavior", _boom)
    assert ADS.build_entity_context(db, "SNX 设备", start=None, end=None) == ""


# ───────────────────────── external 端点鉴权挂载 ─────────────────────────

@pytest.mark.parametrize("path", [
    "/api/v1/external/recharge",
    "/api/v1/external/device-tags",
    "/api/v1/external/device/SNABC12345",
    "/api/v1/external/schedule?start=2026-10-01&end=2026-10-02",
])
def test_external_entity_endpoints_require_auth(client, path):
    # 无 Bearer token → _auth 拒绝(401/403), 证明新路由已挂鉴权而非裸奔
    resp = client.get(path)
    assert resp.status_code in (401, 403)
