# -*- coding: utf-8 -*-
"""#291 退款/投诉维度汇总分析: 设备类型/套餐/商户号/激活时间段(四桶) + active_age 桶下钻。

口径(用户拍板 2026-10-09):
  · 激活时间段四桶 ≤7 / 8-30 / 31-180 / >180 + (无激活记录),
    桶名唯一来源 = _core.ACTIVE_AGE_BUCKETS(退款/投诉两侧同源)
  · 激活基准 = bms_orders 最早激活日 MIN(active_date)（与 #251 sale_days_split 同源）
  · 退款锚点 = D1 退款创建日; 投诉锚点 = 工单登记日
  · 未填SN / SN未建档 显式分桶, 不丢数据
  · 下钻自洽: 汇总桶计数 == 明细同条件筛选行数(active_age 参数)
"""

import uuid as _uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.api.v1.complaints._backlog import _device_dim_groups
from app.api.v1.complaints._helpers import _query_backlog_rows
from app.models.bms_order import BmsOrder
from app.models.device import Device, DimDeviceType
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.services.analytics import ACTIVE_AGE_BUCKETS, refund_decision_overview
from app.services.analytics.refund_chain import refund_withdrawal_detail


def _uniq(prefix: str) -> str:
    return f"{prefix}-{_uuid.uuid4().hex[:10]}"


def _now_naive() -> datetime:
    """库里 naive 时间 = 北京时间(项目口径)。"""
    return datetime.now(UTC).replace(tzinfo=None)


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


def _mk_order(db, sn: str, active_days_ago: int) -> BmsOrder:
    """激活基准 = bms_orders 最早 active_date（aware UTC, 与 #251 造数同款）。"""
    o = BmsOrder(
        order_no=_uniq("BO"), device_sn=sn,
        active_date=datetime.now(UTC) - timedelta(days=active_days_ago),
    )
    db.add(o)
    db.flush()
    return o


def _mk_b_refund(db, sn: str) -> Refund:
    """B 路径(线上直退)可入链路明细的退款行: D4=updated_at_raw。"""
    now = _now_naive()
    r = Refund(
        refund_no=_uniq("RF"), device_sn=sn,
        refund_method="线上", status="已退款",
        refund_amount=Decimal("10.00"), actual_amount=Decimal("10.00"),
        created_at_raw=now - timedelta(days=1), updated_at_raw=now,
    )
    db.add(r)
    db.flush()
    return r


def _mk_backlog_ticket(db, sn: str | None) -> ServiceTicket:
    """积压投诉工单(类别空白=投诉口径, 状态非完成), 登记于 2 天前。"""
    t = ServiceTicket(
        ticket_no=_uniq("GD"), device_sn=sn,
        service_category=None, status="处理中",
        register_time=_now_naive() - timedelta(days=2),
    )
    db.add(t)
    db.flush()
    return t


def test_rw_detail_active_age_filter(db):
    """退款明细 active_age 桶下钻: 激活3天→le7; 激活200天→gt180; 无激活→none。"""
    sn_new, sn_old, sn_none = _uniq("SN-NA"), _uniq("SN-OLD"), _uniq("SN-NO")
    _mk_device(db, sn_new)
    _mk_device(db, sn_old)
    _mk_device(db, sn_none)
    _mk_order(db, sn_new, 3)
    _mk_order(db, sn_old, 200)
    rf_new = _mk_b_refund(db, sn_new)
    rf_old = _mk_b_refund(db, sn_old)
    rf_none = _mk_b_refund(db, sn_none)

    # ≤7天桶: 只含新机退款
    nos = {r["refund_no"] for r in refund_withdrawal_detail(db, path="B", active_age="le7")["list"]}
    assert rf_new.refund_no in nos
    assert rf_old.refund_no not in nos
    assert rf_none.refund_no not in nos

    # >180天桶: 只含老机退款
    nos = {r["refund_no"] for r in refund_withdrawal_detail(db, path="B", active_age="gt180")["list"]}
    assert rf_old.refund_no in nos
    assert rf_new.refund_no not in nos

    # (无激活记录)桶: 只含无 bms_orders 激活记录的设备
    nos = {r["refund_no"] for r in refund_withdrawal_detail(db, path="B", active_age="none")["list"]}
    assert rf_none.refund_no in nos
    assert rf_new.refund_no not in nos
    assert rf_old.refund_no not in nos


def test_decision_overview_device_dims(db):
    """退款决策概览 device_dims 三维: 类型/套餐/商户号分组; 未填SN 显式分桶。

    注: refunds.device_sn 有 FK → devices, 「(未建档)」桶在 FK 库无法造数,
    改由 test_backlog_groups_unregistered 用手工 rows 覆盖该分支。
    """
    sn = _uniq("SN-DD")
    _mk_device(db, sn, device_type_code=_uniq("CPE"),
               current_combo_name="套餐Q", merchant_name="商户Q")
    # _mk_device 会先建 type_name=code 的字典行, 直接用 code 查询名
    code = db.get(Device, sn).device_type_code
    # 线上+已退款: 设备齐全 1 笔 + 无SN 1 笔
    for s, amt in [(sn, "30")]:
        db.add(Refund(
            refund_no=_uniq("RF"), device_sn=s, refund_amount=Decimal(amt),
            refund_method="线上", status="已退款",
            created_at_raw=_now_naive() - timedelta(hours=2),
        ))
    db.add(Refund(
        refund_no=_uniq("RF"), device_sn=None, refund_amount=Decimal("5"),
        refund_method="线上", status="已退款",
        created_at_raw=_now_naive() - timedelta(hours=1),
    ))
    db.flush()

    ov = refund_decision_overview(
        db, start=str(date.today() - timedelta(days=1)), end=str(date.today()), grain="day",
    )
    dd = ov["device_dims"]
    types = {r["name"]: r["count"] for r in dd["by_device_type"]}
    combos = {r["name"]: r["count"] for r in dd["by_combo"]}
    merch = {r["name"]: r["count"] for r in dd["by_merchant"]}
    # 有建档设备 → 类型/套餐/商户号三桶命中
    assert types.get(code, 0) >= 1
    assert combos.get("套餐Q", 0) >= 1
    assert merch.get("商户Q", 0) >= 1
    # 未填SN 显式暴露, 不丢数据
    assert types.get("(未填SN)", 0) >= 1
    # 金额出参 float(JSON number 元)
    row = next(r for r in dd["by_device_type"] if r["name"] == code)
    assert isinstance(row["amount"], float)


def test_backlog_active_age_and_dims(db):
    """投诉积压四维统计与明细同源: 桶计数 == active_age 同条件筛选行数。"""
    sn_a = _uniq("SN-BA")
    _mk_device(db, sn_a, device_type_code=_uniq("CPE"),
               current_combo_name="套餐W", merchant_name="商户W")
    code = db.get(Device, sn_a).device_type_code
    _mk_order(db, sn_a, 300)          # 登记日−激活日 ≈ 298天 → >180天桶
    tk_a = _mk_backlog_ticket(db, sn_a)
    sn_none = _uniq("SN-BNONE")
    _mk_device(db, sn_none)                              # 有建档, 无激活记录
    tk_none = _mk_backlog_ticket(db, sn_none)            # → (无激活记录)
    tk_nosn = _mk_backlog_ticket(db, None)               # 未填SN → (未填SN) + (无激活记录)
    db.flush()

    rows = _query_backlog_rows(db, None, None)
    g = _device_dim_groups(db, rows)

    # 分类三桶: 命中 + 未填SN 显式
    types = {r["name"]: r["count"] for r in g["by_device_type"]}
    assert types.get(code, 0) >= 1
    assert types.get("(未填SN)", 0) >= 1
    combos = {r["name"]: r["count"] for r in g["by_combo"]}
    assert combos.get("套餐W", 0) >= 1
    merch = {r["name"]: r["count"] for r in g["by_merchant"]}
    assert merch.get("商户W", 0) >= 1

    # 激活时间段: 固定桶顺序(ACTIVE_AGE_BUCKETS 单一来源), >180 与 无激活记录 计数正确
    assert [r["name"] for r in g["by_active_age"]] == list(ACTIVE_AGE_BUCKETS)
    ages = {r["name"]: r["count"] for r in g["by_active_age"]}
    assert ages.get(">180天", 0) >= 1
    assert ages.get("(无激活记录)", 0) >= 2  # tk_none + tk_nosn

    # 下钻自洽: active_age=gt180 筛选行 == >180天桶计数
    rows_gt = _query_backlog_rows(db, None, None, active_age="gt180")
    nos = {r["ticket_no"] for r in rows_gt}
    assert tk_a.ticket_no in nos
    assert tk_none.ticket_no not in nos
    assert tk_nosn.ticket_no not in nos
    assert len(rows_gt) == ages[">180天"]


def test_backlog_groups_unregistered(db):
    """SN 不在 devices 表(FK 场景外的防御分支) → (未建档) 桶, 不丢数据。

    FK 约束下无法插数, 直接以手工 rows 调分组函数覆盖该分支。
    """
    sn_real = _uniq("SN-R")
    _mk_device(db, sn_real, merchant_name="商户R")
    rows = [
        {"device_sn": sn_real, "register_time": str(_now_naive())},
        {"device_sn": _uniq("SN-GHOST"), "register_time": str(_now_naive())},  # 未建档
        {"device_sn": None, "register_time": str(_now_naive())},               # 未填SN
    ]
    g = _device_dim_groups(db, rows)
    types = {r["name"]: r["count"] for r in g["by_device_type"]}
    assert types.get("(未建档)", 0) == 1
    assert types.get("(未填SN)", 0) == 1
    assert types.get("(未填)", 0) == 1  # 有建档但 merchant_name 为空 → (未填)
