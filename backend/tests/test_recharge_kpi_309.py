# -*- coding: utf-8 -*-
"""#309 充值统计口径测试: 今日看板充值卡 + 退款分析「窗口充值/退款占充值」

口径(用户拍板 2026-10-13):
  · 充值 = device_combos 窗口内 actual_amount 合计(按 created_at_raw 落窗),
    剔除工厂质检专用套餐流水(package_name ILIKE '%质检%') —— 与 #293 同判据;
  · 退款占充值 = 四路经营合计 ÷ 窗口充值 × 100%; 分母为 0 → None(前端显示「—」)。
"""

import uuid as _uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from app.models.device import Device
from app.models.device_combo import DeviceCombo
from app.models.refund import Refund
from app.services.analytics import refund_decision_overview, today_kpi

# 窗口参数按北京业务日(与 _resolve_windows 口径一致), 不能取 UTC 日期
_TZ_CN = timezone(timedelta(hours=8))


def _today_cn():
    return datetime.now(_TZ_CN).date()


def _uniq(prefix: str) -> str:
    return f"{prefix}-{_uuid.uuid4().hex[:10]}"


def _mk_device(db, sn: str) -> Device:
    dev = db.get(Device, sn)
    if not dev:
        dev = Device(sn=sn)
        db.add(dev)
        db.flush()
    return dev


def _mk_combo(db, *, amount: str, package: str, when: datetime) -> DeviceCombo:
    sn = _uniq("SN")
    _mk_device(db, sn)
    dc = DeviceCombo(
        recharge_order_no=_uniq("RO"),
        device_sn=sn,
        package_name=package,
        actual_amount=Decimal(amount),
        created_at_raw=when,
    )
    db.add(dc)
    db.flush()
    return dc


def _mk_refund(db, *, amount: str, when: datetime) -> Refund:
    sn = _uniq("SN")
    _mk_device(db, sn)
    r = Refund(
        refund_no=_uniq("RF"), device_sn=sn,
        refund_amount=Decimal(amount),
        refund_method="线上", status="已退款",
        created_at_raw=when,
    )
    db.add(r)
    db.flush()
    return r


# ── 今日看板 today_kpi.recharge ──

def test_today_kpi_recharge_excludes_qc_and_yoy(db):
    """充值卡: 剔除质检套餐; 环比取上一等长窗口(实时今日 → 昨天同时段)。"""
    now = datetime.now(UTC)
    _mk_combo(db, amount="100.00", package="全球通用30天", when=now)
    _mk_combo(db, amount="200.00", package="全球通用30天", when=now)
    # 质检流水: 金额巨大, 必须被剔除
    _mk_combo(db, amount="9999.00", package="工厂质检专用套餐*30天", when=now)
    # 昨天同时段真实充值 → 环比基准
    _mk_combo(db, amount="150.00", package="全球通用30天", when=now - timedelta(days=1))

    kpi = today_kpi(db, date=None)
    rc = kpi["recharge"]
    assert rc["value"] == 300.0          # 不含 9999 质检
    assert rc["count"] == 2
    assert rc["yoy_value"] == 150.0
    assert rc["count_yoy"] == 1
    assert rc["yoy_pct"] == 100.0        # (300-150)/150
    # 口径说明透出
    assert "质检" in kpi["_spec"]["recharge"]


# ── 退款分析 refund_decision_overview.summary ──

def test_decision_overview_recharge_and_ratio(db):
    """窗口充值剔除质检; 退款占充值 = 四路合计 / 充值 × 100%。"""
    today = _today_cn()
    start, end = str(today - timedelta(days=1)), str(today)
    now = datetime.now(UTC)
    _mk_combo(db, amount="400.00", package="全球通用30天", when=now)
    _mk_combo(db, amount="9999.00", package="闪猫质检30日套餐", when=now)
    _mk_refund(db, amount="100.00", when=now)   # 四路 A 路现金流出 100
    # #332 T8: 四路取数 std 表 —— 先跑采纳作业
    from app.services.governance.refund_adoption import run_refund_adoption

    run_refund_adoption(db, routes="A", commit=False)

    ov = refund_decision_overview(db, start=start, end=end, grain="day")
    s = ov["summary"]
    assert float(s["recharge_amount"]) == 400.0
    assert s["recharge_count"] == 1
    # 占比 = 100 / 400 × 100 = 25.0
    assert s["refund_recharge_ratio"] == 25.0


def test_decision_overview_ratio_none_when_no_recharge(db):
    """窗口内无真实充值(仅质检流水) → 占比 None, 不除零报错。"""
    today = _today_cn()
    start, end = str(today - timedelta(days=1)), str(today)
    now = datetime.now(UTC)
    _mk_combo(db, amount="9999.00", package="工厂质检专用套餐*30天", when=now)
    _mk_refund(db, amount="50.00", when=now)
    # #332 T8: 四路取数 std 表 —— 先跑采纳作业
    from app.services.governance.refund_adoption import run_refund_adoption

    run_refund_adoption(db, routes="A", commit=False)

    ov = refund_decision_overview(db, start=start, end=end, grain="day")
    s = ov["summary"]
    assert float(s["recharge_amount"]) == 0.0
    assert s["recharge_count"] == 0
    assert s["refund_recharge_ratio"] is None
