# -*- coding: utf-8 -*-
"""#273 退款/投诉明细「设备维度筛选」(设备类型/套餐/商户号/激活时间段) 回归测试。

口径锚点（与分品类运营看板 #271 同源，全走 devices 表）:
  · 设备类型 = devices.device_type_code
  · 套餐     = devices.current_combo_name
  · 商户号   = devices.merchant_name
  · 激活时间 = devices.active_time 落在 [active_start, active_end] 北京日期闭区间
"""

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.api.v1.complaints._helpers import _query_backlog_rows
from app.models.device import Device, DimDeviceType
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.services.analytics._core import _device_dim_matched_sns
from app.services.analytics.refund_chain import refund_withdrawal_detail


def _uniq(prefix: str) -> str:
    return f"{prefix}-{_uuid.uuid4().hex[:10]}"


def _mk_device(db, sn: str, **kw) -> Device:
    # device_type_code 有 FK → dim_device_type, 用到的码先建行
    code = kw.get("device_type_code")
    if code and not db.scalar(select(DimDeviceType).where(DimDeviceType.type_code == code)):
        db.add(DimDeviceType(type_code=code, type_name=code))
        db.flush()
    dev = Device(sn=sn, **kw)
    db.add(dev)
    db.flush()
    return dev


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
    """设备类型/套餐/商户号/激活时间窗 各自命中与排除。"""
    dt = DimDeviceType(type_code=_uniq("CPE"), type_name=_uniq("4G路由器"))
    db.add(dt)
    db.flush()
    sn_hit = _uniq("SN-HIT")
    _mk_device(db, sn_hit, device_type_code=dt.type_code,
               current_combo_name="套餐X", merchant_name="商户甲",
               active_time=datetime(2026, 1, 15, tzinfo=UTC))
    sn_miss = _uniq("SN-MISS")
    _mk_device(db, sn_miss, device_type_code=_uniq("MIFI"),
               current_combo_name="套餐Y", merchant_name="商户乙",
               active_time=datetime(2026, 3, 1, tzinfo=UTC))

    assert _device_dim_matched_sns(db, device_type=dt.type_code) == {sn_hit}
    assert _device_dim_matched_sns(db, combo="套餐X") == {sn_hit}
    assert _device_dim_matched_sns(db, merchant="商户乙") == {sn_miss}
    # 激活时间窗(北京日期闭区间): 1月窗只命中 1/15 激活的设备
    assert _device_dim_matched_sns(db, active_start="2026-01-01", active_end="2026-01-31") == {sn_hit}
    assert _device_dim_matched_sns(db, active_start="2026-02-01") == {sn_miss}
    # 条件组合(AND)
    assert _device_dim_matched_sns(db, device_type=dt.type_code, merchant="商户乙") == set()
    # 非法日期不致命, 视为未传
    assert _device_dim_matched_sns(db, active_start="not-a-date") is None


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
    dt = DimDeviceType(type_code=_uniq("CPE2"), type_name=_uniq("随身WiFi"))
    db.add(dt)
    db.flush()
    sn_hit, sn_other = _uniq("SN-RH"), _uniq("SN-RO")
    _mk_device(db, sn_hit, device_type_code=dt.type_code)
    _mk_device(db, sn_other, device_type_code=_uniq("OTHER"))
    rf_hit = _mk_b_refund(db, sn_hit)
    rf_other = _mk_b_refund(db, sn_other)

    res_all = refund_withdrawal_detail(db, path="B")
    nos_all = {r["refund_no"] for r in res_all["list"]}
    assert {rf_hit.refund_no, rf_other.refund_no} <= nos_all

    res = refund_withdrawal_detail(db, path="B", device_type=dt.type_code)
    nos = {r["refund_no"] for r in res["list"]}
    assert rf_hit.refund_no in nos
    assert rf_other.refund_no not in nos
    assert res["total"] == len([r for r in nos_all if r in nos])
