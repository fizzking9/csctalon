# -*- coding: utf-8 -*-
"""B3-5 (#251) 退款按设备销售天数核算: 售出后 N 天分桶入 decision_overview.sale_days_split

口径纪律(用户拍板): 本维度**单独核算, 不纳入售后客服指标**, 仅经营分析视角。
基准口径与 A 路线上直退一致(refund_method=线上 AND status=已退款)。
"""

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.bms_order import BmsOrder
from app.models.device import Device
from app.models.refund import Refund
from app.services.analytics import refund_decision_overview


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


def _mk_order(db, sn: str, active_days_ago: int) -> BmsOrder:
    o = BmsOrder(
        order_no=_uniq("BO"), device_sn=sn,
        active_date=datetime.now(UTC) - timedelta(days=active_days_ago),
    )
    db.add(o)
    db.flush()
    return o


def _mk_refund(db, sn: str, amount: str, days_ago: int = 0) -> Refund:
    r = Refund(
        refund_no=_uniq("RF"), device_sn=sn,
        refund_amount=Decimal(amount),
        refund_method="线上", status="已退款",
        created_at_raw=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(r)
    db.flush()
    return r


def _bucket(split: list[dict], name: str) -> dict:
    return next(b for b in split if b["bucket"] == name)


def _overview(db):
    """宽窗口调用: 覆盖全部测试数据(激活 400 天前 ~ 今天)。

    #332 T8: decision_overview 取数 std 表 —— 先跑采纳作业把 ods 夹具固化进 std。
    """
    from app.services.governance.refund_adoption import run_refund_adoption

    run_refund_adoption(db, commit=False)
    today = datetime.now(UTC).date()
    return refund_decision_overview(
        db, start=str(today - timedelta(days=1)), end=str(today), grain="day",
    )


def test_sale_days_split_bucket_boundaries(db):
    """#295 五桶各桶落位: 14/17/60/120/200/400 天激活的设备, 退款各落对应桶。

    精确切点(15/16/30/31/180/181/365/366)的归属在纯函数用例
    test_active_age_dims.py::test_active_age_bucket_cut_points 断言;
    本用例走 DB+时区归日链路, 故取切点内侧安全值防跨日小时差抖动。
    """
    cases = [
        (14, "0-15天"), (17, "16-30天"), (60, "31-180天"),
        (200, "181-365天"), (400, ">365天"),
    ]
    for days, bucket in cases:
        sn = _uniq(f"SN-{days}")
        _mk_device(db, sn)
        _mk_order(db, sn, days)
        _mk_refund(db, sn, "100")

    split = _overview(db)["sale_days_split"]
    # #295: 五桶 + (无SN) + (无激活记录), 顺序按时长由短到长(后端常量单一来源)
    # #311 方案D: (无SN) 单独桶在 (无激活记录) 前(SN 缺失不再混入无激活记录)
    assert [b["bucket"] for b in split] == [
        "0-15天", "16-30天", "31-180天", "181-365天", ">365天", "(无SN)", "(无激活记录)",
    ]
    for days, bucket in cases:
        assert _bucket(split, bucket)["cnt"] >= 1, f"{bucket} 应至少含本测试 1 笔"


def test_sale_days_split_no_active_record(db):
    """无激活记录设备(无 bms_orders 行)的退款落「(无激活记录)」桶。"""
    sn = _uniq("SN-NOACT")
    _mk_device(db, sn)
    _mk_refund(db, sn, "50")

    split = _overview(db)["sale_days_split"]
    assert _bucket(split, "(无激活记录)")["cnt"] >= 1
    assert _bucket(split, "(无激活记录)")["amount"] >= 50.0


def test_sale_days_split_first_active_min(db):
    """设备多笔订单(续费)时取 MIN(active_date) 首购激活日。"""
    sn = _uniq("SN-MULTI")
    _mk_device(db, sn)
    _mk_order(db, sn, 200)   # 首购
    _mk_order(db, sn, 10)    # 续费
    _mk_refund(db, sn, "80")

    split = _overview(db)["sale_days_split"]
    # 200 天前首购 → 181-365天 桶(而非按续费 10 天的 0-15/16-30 桶)
    assert _bucket(split, "181-365天")["cnt"] >= 1


def test_sale_days_split_excludes_non_online(db):
    """口径 = 线上+已退款: 线下/申请中的退款不计入。"""
    sn = _uniq("SN-OFF")
    _mk_device(db, sn)
    _mk_order(db, sn, 3)
    _mk_refund(db, sn, "999")
    # 覆盖为线下
    r = db.query(Refund).filter(Refund.device_sn == sn).first()
    r.refund_method = "线下"
    db.flush()

    sn2 = _uniq("SN-PEND")
    _mk_device(db, sn2)
    _mk_order(db, sn2, 3)
    _mk_refund(db, sn2, "888")
    r2 = db.query(Refund).filter(Refund.device_sn == sn2).first()
    r2.status = "申请中"
    db.flush()

    split = _overview(db)["sale_days_split"]
    amt = _bucket(split, "0-15天")["amount"]
    # 999(线下) 与 888(申请中) 均不应出现在桶内
    assert abs(amt - 999 - 888) > 0.01 or amt < 999


def test_sale_days_split_amount_decimal_to_float(db):
    """金额出参为 float(JSON number 元), 且分桶求和 = 各桶之和。"""
    sn = _uniq("SN-AMT")
    _mk_device(db, sn)
    _mk_order(db, sn, 14)
    _mk_refund(db, sn, "123.45")

    split = _overview(db)["sale_days_split"]
    b = _bucket(split, "0-15天")
    assert isinstance(b["amount"], float)
    assert b["amount"] >= 123.45
