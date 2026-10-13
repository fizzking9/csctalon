# -*- coding: utf-8 -*-
"""#291/#292 退款/投诉维度汇总分析: 设备类型/套餐/商户号/激活时长(#295 五桶) + active_age 桶下钻。

口径(用户拍板 2026-10-09 #291 + #292 修正; 2026-10-11 #295 改桶):
  · 激活时长五桶 0-15 / 16-30 / 31-180 / 181-365 / >365 + (无激活记录),
    切点=上界(30 归 16-30、180 归 31-180、365 归 181-365),
    桶名唯一来源 = _core.ACTIVE_AGE_BUCKETS(退款/投诉两侧同源)
  · 激活基准 = bms_orders 最早激活日 MIN(active_date)（与 #251 sale_days_split 同源）
  · 退款锚点 = D1 退款创建日; 投诉锚点 = 工单登记日
  · 未填SN / SN未建档 显式分桶, 不丢数据
  · 下钻自洽: 汇总桶计数 == 明细同条件筛选行数(active_age 参数)
  · #292: 型号/套餐改取 bms_orders 最新订单(create_date DESC)的 device_type_name/combo_name;
    工厂质检测试套餐(含"工厂质检" 3 变体)不计入分析(combo → None, 候选列表排除);
    激活改并集(bms_orders MIN(active_date) 优先, devices.active_time 兜底);
    商户号维持 devices.merchant_name
"""

import uuid as _uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.api.v1.complaints._backlog import _device_dim_groups
from app.api.v1.complaints._helpers import _query_backlog_rows
from app.models.bms_order import BmsOrder
from app.models.device import Device, DimDeviceType
from app.models.manual_refund import ManualRefund
from app.models.refund import Refund
from app.models.shanmao_refund import ShanmaoRefund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal
from app.services.analytics import (
    ACTIVE_AGE_BUCKETS,
    _active_age_bucket,
    _device_dim_options,
    refund_decision_overview,
)
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


def _mk_order(db, sn: str, active_days_ago: int, *,
              device_type_name: str | None = None,
              combo_name: str | None = None,
              create_days_ago: int = 0) -> BmsOrder:
    """bms_orders 行: 激活基准=MIN(active_date); 最新订单=create_date DESC 取首条(#292)。"""
    o = BmsOrder(
        order_no=_uniq("BO"), device_sn=sn,
        device_type_name=device_type_name, combo_name=combo_name,
        active_date=datetime.now(UTC) - timedelta(days=active_days_ago),
        create_date=datetime.now(UTC) - timedelta(days=create_days_ago),
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


def test_active_age_bucket_cut_points():
    """#295 纯函数切点(用户拍板: 切点=上界)。

    15/30/180/365 各归其左侧桶; 16/31/181/366 进入右侧桶;
    负数(激活晚于事件, 补录/时点差)自然落首桶; None → (无激活记录)。
    """
    cases = {
        -5: "0-15天", 0: "0-15天", 15: "0-15天",
        16: "16-30天", 30: "16-30天",
        31: "31-180天", 180: "31-180天",
        181: "181-365天", 365: "181-365天",
        366: ">365天", 10000: ">365天",
    }
    for days, bucket in cases.items():
        assert _active_age_bucket(days) == bucket, f"days={days}"
    assert _active_age_bucket(None) == "(无激活记录)"
    # 参数映射与桶一一对应(下钻枚举完整)
    from app.services.analytics._core import _AGE_PARAM_TO_BUCKET
    assert set(_AGE_PARAM_TO_BUCKET.values()) == set(ACTIVE_AGE_BUCKETS)


def test_rw_detail_active_age_filter(db):
    """退款明细 active_age 桶下钻(#295): 激活3天→le15; 200天→181-365; 400天→gt365; 无激活→none。"""
    sn_new, sn_mid, sn_old, sn_none = (_uniq("SN-NA"), _uniq("SN-MID"),
                                       _uniq("SN-OLD"), _uniq("SN-NO"))
    for sn in (sn_new, sn_mid, sn_old, sn_none):
        _mk_device(db, sn)
    _mk_order(db, sn_new, 3)
    _mk_order(db, sn_mid, 200)
    _mk_order(db, sn_old, 400)
    rf_new = _mk_b_refund(db, sn_new)
    rf_mid = _mk_b_refund(db, sn_mid)
    rf_old = _mk_b_refund(db, sn_old)
    rf_none = _mk_b_refund(db, sn_none)

    # 0-15天桶: 只含新机退款
    nos = {r["refund_no"] for r in refund_withdrawal_detail(db, path="B", active_age="le15")["list"]}
    assert rf_new.refund_no in nos
    assert rf_mid.refund_no not in nos
    assert rf_old.refund_no not in nos
    assert rf_none.refund_no not in nos

    # 181-365天桶
    nos = {r["refund_no"] for r in refund_withdrawal_detail(db, path="B", active_age="181-365")["list"]}
    assert rf_mid.refund_no in nos
    assert rf_new.refund_no not in nos
    assert rf_old.refund_no not in nos

    # >365天桶: 只含超一年老机退款
    nos = {r["refund_no"] for r in refund_withdrawal_detail(db, path="B", active_age="gt365")["list"]}
    assert rf_old.refund_no in nos
    assert rf_new.refund_no not in nos
    assert rf_mid.refund_no not in nos

    # (无激活记录)桶: 只含无 bms_orders 激活记录的设备
    nos = {r["refund_no"] for r in refund_withdrawal_detail(db, path="B", active_age="none")["list"]}
    assert rf_none.refund_no in nos
    assert rf_new.refund_no not in nos
    assert rf_old.refund_no not in nos


def test_decision_overview_device_dims(db):
    """退款决策概览 device_dims 三维: 型号/套餐/商户号分组; 未填SN 显式分桶。

    #292 口径: 型号/套餐取 bms_orders 最新订单(create_date DESC)的 device_type_name/combo_name,
    商户号取 devices.merchant_name。
    注: refunds.device_sn 有 FK → devices, 「(未建档)」桶在 FK 库无法造数,
    改由 test_backlog_groups_unregistered 用手工 rows 覆盖该分支。
    """
    sn = _uniq("SN-DD")
    _mk_device(db, sn, merchant_name="商户Q")
    _mk_order(db, sn, 10, device_type_name="CPE-DD", combo_name="套餐Q")
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
    # 有建档设备 → 型号(取自 bms_orders)/套餐(取自 bms_orders)/商户号(取自 devices)三桶命中
    assert types.get("CPE-DD", 0) >= 1
    assert combos.get("套餐Q", 0) >= 1
    assert merch.get("商户Q", 0) >= 1
    # 未填SN 显式暴露, 不丢数据
    assert types.get("(未填SN)", 0) >= 1
    # 金额出参 float(JSON number 元)
    row = next(r for r in dd["by_device_type"] if r["name"] == "CPE-DD")
    assert isinstance(row["amount"], float)


def test_decision_overview_device_dims_four_routes(db):
    """#291 口径升级(2026-10-10 用户拍板): 设备维度汇总算全四路 A+B+C+D。

    同一设备四路各造 1 笔不同金额 → 商户/型号桶 count=4 且 routes 分路明细齐全;
    同时回归 #293 状态口径: 线下已退款 / 线上申请中不入桶(A 路只收线上+已退款);
    sale_days_split 同为四路数据面(激活10天 → #295 0-15天 桶含全部 4 笔)。

    #311 方案C(2026-10-13): C 路闪猫退款独立单列, 4 路汇总改 3 路(A+B+D);
    device_dims/sale_days_split 排除 C 路, C 路独立到 c_route_summary。
    """
    sn = _uniq("SN-4R")
    _mk_device(db, sn, merchant_name="商户4R")
    _mk_order(db, sn, 10, device_type_name="CPE-4R", combo_name="套餐4R")
    now = _now_naive()
    # A 线上直退(线上+已退款)
    db.add(Refund(refund_no=_uniq("RF"), device_sn=sn, refund_method="线上", status="已退款",
                  refund_amount=Decimal("10.00"), created_at_raw=now))
    # A 路排除回归: 线下已退款 / 线上申请中(#293 口径: 只管实际退款)
    db.add(Refund(refund_no=_uniq("RF"), device_sn=sn, refund_method="线下", status="已退款",
                  refund_amount=Decimal("999.00"), created_at_raw=now))
    db.add(Refund(refund_no=_uniq("RF"), device_sn=sn, refund_method="线上", status="申请中",
                  refund_amount=Decimal("888.00"), created_at_raw=now))
    # B 钱包提现(用户提现, op_time 归日)
    db.add(WalletWithdrawal(account_id=_uniq("ACC"), device_sn=sn, op_type="用户提现",
                            op_amount=Decimal("20.00"), op_time=datetime.now(UTC),
                            operator="SYS"))
    # C 闪猫退款(已退款; 归日锚点回退 created_at 由 TimestampMixin 填充)
    db.add(ShanmaoRefund(refund_no=_uniq("TF"), device_sn=sn, status="已退款",
                         refund_amount=Decimal("30.00"), actual_amount=Decimal("30.00")))
    # D 手动退款(is_refunded='是', register_time 归日)
    db.add(ManualRefund(device_sn=sn, is_refunded="是", refund_amount=Decimal("40.00"),
                        register_time=now))
    db.flush()

    ov = refund_decision_overview(
        db, start=str(date.today() - timedelta(days=1)), end=str(date.today()), grain="day",
    )
    # #311 方案C: device_dims 是 3 路汇总(A+B+D), C 路独立到 c_route_summary
    merch = {r["name"]: r for r in ov["device_dims"]["by_merchant"]}
    row = merch["商户4R"]
    assert row["count"] == 3                       # 三路 A+B+D(C 路独立, 不混入)
    assert abs(row["amount"] - 70.0) < 0.01        # 10+20+40(排除 C 路 30)
    assert set(row["routes"]) == {"A 线上直退", "B 钱包提现", "D 手动退款"}
    for rk, amt in (("A 线上直退", 10.0), ("B 钱包提现", 20.0), ("D 手动退款", 40.0)):
        assert row["routes"][rk]["count"] == 1
        assert abs(row["routes"][rk]["amount"] - amt) < 0.01
    # 型号桶同为三路口径
    types = {r["name"]: r["count"] for r in ov["device_dims"]["by_device_type"]}
    assert types.get("CPE-4R", 0) == 3
    # sale_days_split 同为三路: 激活10天 → 0-15天 桶含 A+B+D 3 笔(#295 五桶, #311 排除 C 路)
    sb = next(b for b in ov["sale_days_split"] if b["bucket"] == "0-15天")
    assert sb["cnt"] == 3
    assert abs(sb["amount"] - 70.0) < 0.01
    assert set(sb["routes"]) == {"A 线上直退", "B 钱包提现", "D 手动退款"}
    # #311 方案C: c_route_summary 独立展示 C 路(1 笔 30 元)
    assert ov["c_route_summary"]["count"] == 1
    assert abs(ov["c_route_summary"]["amount"] - 30.0) < 0.01
    c_sb = next(b for b in ov["c_route_summary"]["sale_days_split"] if b["bucket"] == "0-15天")
    assert c_sb["cnt"] == 1
    assert abs(c_sb["amount"] - 30.0) < 0.01
    assert set(c_sb["routes"]) == {"C 闪猫退款"}


def test_backlog_active_age_and_dims(db):
    """投诉积压四维统计与明细同源: 桶计数 == active_age 同条件筛选行数。

    #292 口径: 型号/套餐取 bms_orders 最新订单字段, 商户号取 devices.merchant_name。
    """
    sn_a = _uniq("SN-BA")
    _mk_device(db, sn_a, merchant_name="商户W")
    _mk_order(db, sn_a, 300, device_type_name="CPE-BA", combo_name="套餐W")  # 181-365天桶
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
    assert types.get("CPE-BA", 0) >= 1
    assert types.get("(未填SN)", 0) >= 1
    combos = {r["name"]: r["count"] for r in g["by_combo"]}
    assert combos.get("套餐W", 0) >= 1
    merch = {r["name"]: r["count"] for r in g["by_merchant"]}
    assert merch.get("商户W", 0) >= 1

    # 激活时长: 固定桶顺序(ACTIVE_AGE_BUCKETS 单一来源), 181-365 与 无激活记录 计数正确
    assert [r["name"] for r in g["by_active_age"]] == list(ACTIVE_AGE_BUCKETS)
    ages = {r["name"]: r["count"] for r in g["by_active_age"]}
    assert ages.get("181-365天", 0) >= 1
    assert ages.get("(无激活记录)", 0) >= 2  # tk_none + tk_nosn

    # 下钻自洽: active_age=181-365 筛选行 == 181-365天桶计数
    rows_gt = _query_backlog_rows(db, None, None, active_age="181-365")
    nos = {r["ticket_no"] for r in rows_gt}
    assert tk_a.ticket_no in nos
    assert tk_none.ticket_no not in nos
    assert tk_nosn.ticket_no not in nos
    assert len(rows_gt) == ages["181-365天"]


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


def test_factory_combo_excluded(db):
    """#292: 工厂质检测试套餐(3 变体)不计入分析 → combo 显式 (未填); 型号不受影响。

    工厂质检套餐(combo_name 含"工厂质检")在 _device_dim_attrs 中被置 None,
    在 _device_dim_options 候选列表中被排除, 避免测试套餐污染分析口径。
    """
    sn = _uniq("SN-FC")
    _mk_device(db, sn, merchant_name="商户FC")
    _mk_order(db, sn, 10, device_type_name="CPE-FC", combo_name="工厂质检专用套餐")
    _mk_backlog_ticket(db, sn)
    db.flush()

    # 1) 积压四维: combo 被置 None → (未填); type_name 不受影响
    rows = _query_backlog_rows(db, None, None)
    g = _device_dim_groups(db, rows)
    combos = {r["name"]: r["count"] for r in g["by_combo"]}
    assert combos.get("(未填)", 0) >= 1  # combo 被排除 → None → (未填)
    assert "工厂质检专用套餐" not in combos  # 不出现在分桶中
    types = {r["name"]: r["count"] for r in g["by_device_type"]}
    assert types.get("CPE-FC", 0) >= 1  # type_name 不受 combo 排除影响

    # 2) 候选项: 工厂质检套餐不在 combo 候选列表中
    opts = _device_dim_options(db)
    assert "工厂质检专用套餐" not in opts["combos"]
    assert any(t["value"] == "CPE-FC" for t in opts["device_types"])
