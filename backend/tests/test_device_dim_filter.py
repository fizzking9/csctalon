# -*- coding: utf-8 -*-
"""#273 退款/投诉明细「设备维度筛选」(设备类型/套餐/商户号/激活时间段) 回归测试。

口径锚点(#292 拍板 2026-10-09; #293 回落扩展 2026-10-10):
  · 设备类型 = bms_orders 最新订单 device_type_name; 该 SN 无 bms_orders 订单时
    回落 devices.device_type(权威序与 _device_dim_attrs 一致)
  · 套餐     = bms_orders 最新订单 combo_name; 回落 devices.current_combo_name
  · 商户号   = devices.merchant_name
  · 激活时间 = bms_orders MIN(active_date) 或 devices.active_time 兜底(并集)
    落在 [active_start, active_end] 北京日期闭区间
"""

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.api.v1.complaints._helpers import _query_backlog_rows
from app.models.bms_order import BmsOrder
from app.models.device import Device
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.services.analytics._core import _device_dim_matched_sns
from app.services.analytics.refund_chain import refund_withdrawal_detail


def _uniq(prefix: str) -> str:
    return f"{prefix}-{_uuid.uuid4().hex[:10]}"


def _mk_device(db, sn: str, **kw) -> Device:
    dev = Device(sn=sn, **kw)
    db.add(dev)
    db.flush()
    return dev


def _mk_bms_order(db, sn: str, *, type_name: str | None = None, combo: str | None = None,
                  create_date: datetime | None = None) -> BmsOrder:
    o = BmsOrder(order_no=_uniq("BO"), device_sn=sn, device_type_name=type_name,
                 combo_name=combo, create_date=create_date or datetime.now(UTC))
    db.add(o)
    db.flush()
    return o


def _mk_b_refund(db, sn: str) -> Refund:
    """造一条 B 路径(线上直退)可入链路明细的退款行。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    r = Refund(
        refund_no=_uniq("RF"), device_sn=sn,
        refund_method="线上", status="已退款",
        refund_amount=Decimal("10.00"), actual_amount=Decimal("10.00"),
        created_at_raw=now - timedelta(days=1), updated_at_raw=now,
    )
    db.add(r)
    db.flush()
    return r


def _mk_backlog_ticket(db, sn: str) -> ServiceTicket:
    """造一条积压投诉工单(类别空白=投诉口径, 状态非完成)。"""
    t = ServiceTicket(
        ticket_no=_uniq("GD"), device_sn=sn,
        service_category=None, status="处理中",
        register_time=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2),
    )
    db.add(t)
    db.flush()
    return t


def test_matched_sns_none_when_no_filter(db):
    """全部条件为空 → 返回 None(不筛选), 不触发 devices 全表扫描。"""
    _mk_device(db, _uniq("SN-A"))
    assert _device_dim_matched_sns(db) is None
    assert _device_dim_matched_sns(db, "", "", "", "", "") is None


def test_matched_sns_by_each_dim(db):
    """设备类型/套餐/商户号/激活时间窗 各自命中与排除(#292/#293 口径)。"""
    type_hit, type_miss = _uniq("4G路由器"), _uniq("MIFI")
    sn_hit = _uniq("SN-HIT")
    _mk_device(db, sn_hit, device_type=type_hit,
               current_combo_name="套餐X", merchant_name="商户甲",
               active_time=datetime(2026, 1, 15, tzinfo=UTC))
    sn_miss = _uniq("SN-MISS")
    _mk_device(db, sn_miss, device_type=type_miss,
               current_combo_name="套餐Y", merchant_name="商户乙",
               active_time=datetime(2026, 3, 1, tzinfo=UTC))

    assert _device_dim_matched_sns(db, device_type=type_hit) == {sn_hit}
    assert _device_dim_matched_sns(db, combo="套餐X") == {sn_hit}
    assert _device_dim_matched_sns(db, merchant="商户乙") == {sn_miss}
    # 激活时间窗(北京日期闭区间): 1月窗只命中 1/15 激活的设备
    assert _device_dim_matched_sns(db, active_start="2026-01-01", active_end="2026-01-31") == {sn_hit}
    assert _device_dim_matched_sns(db, active_start="2026-02-01") == {sn_miss}
    # 条件组合(AND)
    assert _device_dim_matched_sns(db, device_type=type_hit, merchant="商户乙") == set()
    # 非法日期不致命, 视为未传
    assert _device_dim_matched_sns(db, active_start="not-a-date") is None


def test_matched_sns_bms_orders_priority(db):
    """#292/#293 权威序: 有 bms_orders 订单的 SN 按「最新订单类型」匹配,
    devices.device_type 不参与(回落只对无订单 SN 生效)。"""
    type_bms, type_dev = _uniq("TYPE-BMS"), _uniq("TYPE-DEV")
    sn_bo = _uniq("SN-BO")
    _mk_device(db, sn_bo, device_type=type_dev)
    # 两条订单: 较早=type_dev, 最新=create_date 更大=type_bms → 最新订单的类型生效
    _mk_bms_order(db, sn_bo, type_name=type_dev,
                  create_date=datetime.now(UTC) - timedelta(days=2))
    _mk_bms_order(db, sn_bo, type_name=type_bms,
                  create_date=datetime.now(UTC) - timedelta(days=1))
    # 无订单 SN: devices.device_type 回落生效
    sn_fallback = _uniq("SN-FB")
    _mk_device(db, sn_fallback, device_type=type_dev)

    assert _device_dim_matched_sns(db, device_type=type_bms) == {sn_bo}
    # type_dev 只命中回落 SN(有订单的 sn_bo 不吃 devices 回落)
    assert _device_dim_matched_sns(db, device_type=type_dev) == {sn_fallback}


def test_backlog_rows_device_dim_filter(db):
    """积压投诉明细: 商户号筛选只保留命中设备的工单; 未启用时不影响结果。"""
    sn_a, sn_b = _uniq("SN-MA"), _uniq("SN-MB")
    _mk_device(db, sn_a, merchant_name=_uniq("商户A"))
    _mk_device(db, sn_b, merchant_name=None)
    tk_a = _mk_backlog_ticket(db, sn_a)
    tk_b = _mk_backlog_ticket(db, sn_b)

    # 未启用设备维度 → 两条都在
    rows = _query_backlog_rows(db, None, None)
    nos = {r["ticket_no"] for r in rows}
    assert {tk_a.ticket_no, tk_b.ticket_no} <= nos

    # 按商户号筛选 → 只剩 sn_a 的工单
    m_a = db.get(Device, sn_a).merchant_name
    rows = _query_backlog_rows(db, None, None, merchant=m_a)
    nos = {r["ticket_no"] for r in rows}
    assert tk_a.ticket_no in nos
    assert tk_b.ticket_no not in nos

    # 按不存在的商户号 → 全被过滤
    rows = _query_backlog_rows(db, None, None, merchant=_uniq("无此商户"))
    assert rows == []


def test_rw_detail_device_dim_filter(db):
    """退款明细(B 路径): 设备类型筛选只保留命中设备的退款, total 同步收敛。"""
    type_hit, type_other = _uniq("随身WiFi"), _uniq("OTHER")
    sn_hit, sn_other = _uniq("SN-RH"), _uniq("SN-RO")
    _mk_device(db, sn_hit, device_type=type_hit)
    _mk_device(db, sn_other, device_type=type_other)
    rf_hit = _mk_b_refund(db, sn_hit)
    rf_other = _mk_b_refund(db, sn_other)

    res_all = refund_withdrawal_detail(db, path="B")
    nos_all = {r["refund_no"] for r in res_all["list"]}
    assert {rf_hit.refund_no, rf_other.refund_no} <= nos_all

    res = refund_withdrawal_detail(db, path="B", device_type=type_hit)
    nos = {r["refund_no"] for r in res["list"]}
    assert rf_hit.refund_no in nos
    assert rf_other.refund_no not in nos
    assert res["total"] == len([r for r in nos_all if r in nos])
