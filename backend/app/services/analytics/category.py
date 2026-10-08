"""分品类运营看板聚合服务 (#254 B3-10)

设计说明（Design Rationale）:
- 目的(替谁/答何决策): 替运营主管回答「哪类设备/哪个套餐/哪个商户号是
  营收主力、谁投诉风险最高」, 在周/月复盘中做资源倾斜与风险管控决策。
- 逻辑(为何这样组织): 三个维度(设备类型/套餐/商户号)各出 top N 聚合,
  指标统一为「规模→活跃→营收→风险」四级, 对标正规商业运营:
  ① 规模: 设备数; ② 活跃: 近90天有充值或流量的设备数;
  ③ 营收: 近90天充值金额(Decimal); ④ 风险: 近30天退款笔数/投诉数/投诉率。
- 口径: 设备类型走 devices.device_type_code(B3-1 归一); 套餐走 combo_dict
  归一码(B3-2, 未命中回落原始名); 商户号走 DeviceMerchantLink is_current
  建链(B3-1); 投诉走 complaint_scope 唯一出口; 充值金额走 device_combos
  actual_amount(Numeric(12,2)元, Decimal 计算, 出参 float)。
- 取舍(不做什么及原因): ① 不做转化漏斗(需埋点/订单状态机, 当前数据面无);
  ② 不做留存分析(需设备生命周期快照, 属 B4 架构演进);
  ③ 不做钻取到单设备(设备 360 已覆盖, 避免重复);
  ④ 默认 limit=10 防宽表; 全量数据用导出功能。
- 工单: #254 · 排期: docs/reports/20261009-下一步开发计划-排期规划.md §六 B3-10
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.combo_dict import ComboDict, ComboNameAlias
from app.models.device import Device, DimDeviceType
from app.models.device_combo import DeviceCombo
from app.models.device_flow_usage import DeviceFlowUsage
from app.models.merchant import DeviceMerchantLink, Merchant
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.services import complaint_scope
from app.services.analytics._core import D


def _date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _fetch_dim_devices(db: Session, dim: str) -> dict[str, dict[str, Any]]:
    """按维度聚合设备数。"""
    if dim == "device_type":
        stmt = (
            select(
                Device.device_type_code.label("key"),
                func.count(Device.sn).label("device_cnt"),
            )
            .group_by(Device.device_type_code)
        )
    elif dim == "combo":
        # 套餐: 设备当前套餐名 → 归一码
        stmt = (
            select(
                Device.current_combo_name.label("key"),
                func.count(Device.sn).label("device_cnt"),
            )
            .group_by(Device.current_combo_name)
        )
    elif dim == "merchant":
        # 商户: 设备→商户号 is_current 建链
        stmt = (
            select(
                Merchant.sub_name.label("key"),
                func.count(Device.sn).label("device_cnt"),
            )
            .select_from(Device)
            .join(DeviceMerchantLink, (DeviceMerchantLink.device_sn == Device.sn) & DeviceMerchantLink.is_current)
            .join(Merchant, Merchant.id == DeviceMerchantLink.merchant_pk)
            .group_by(Merchant.sub_name)
        )
    else:
        raise ValueError(f"unknown dim: {dim}")

    return {r.key or "未归一": {"device_cnt": int(r.device_cnt)} for r in db.execute(stmt)}


def _fetch_active_devices(db: Session, since: datetime) -> set[str]:
    """近 N 天有充值或流量的设备 SN 集合。"""
    # 充值活跃
    rc = {
        r[0]
        for r in db.execute(
            select(distinct(DeviceCombo.device_sn))
            .where(DeviceCombo.device_sn.isnot(None))
            .where(DeviceCombo.created_at_raw >= since)
        )
    }
    # 流量活跃(最近完整月有流量记录)
    fl = {
        r[0]
        for r in db.execute(
            select(distinct(DeviceFlowUsage.device_sn))
            .where(DeviceFlowUsage.device_sn.isnot(None))
            .where(DeviceFlowUsage.month_complete.is_(True))
            .where(DeviceFlowUsage.stat_month >= since.strftime("%Y-%m"))
        )
    }
    return rc | fl


def _fetch_recharge_by_dim(db: Session, dim: str, since: datetime) -> dict[str, dict[str, Any]]:
    """近 N 天充值金额/笔数, 按维度聚合。"""
    if dim == "device_type":
        stmt = (
            select(
                Device.device_type_code.label("key"),
                func.count().label("cnt"),
                func.coalesce(func.sum(DeviceCombo.actual_amount), 0).label("amount"),
            )
            .select_from(DeviceCombo)
            .join(Device, Device.sn == DeviceCombo.device_sn)
            .where(DeviceCombo.created_at_raw >= since)
            .group_by(Device.device_type_code)
        )
    elif dim == "combo":
        stmt = (
            select(
                DeviceCombo.package_name.label("key"),
                func.count().label("cnt"),
                func.coalesce(func.sum(DeviceCombo.actual_amount), 0).label("amount"),
            )
            .where(DeviceCombo.created_at_raw >= since)
            .group_by(DeviceCombo.package_name)
        )
    elif dim == "merchant":
        stmt = (
            select(
                Merchant.sub_name.label("key"),
                func.count().label("cnt"),
                func.coalesce(func.sum(DeviceCombo.actual_amount), 0).label("amount"),
            )
            .select_from(DeviceCombo)
            .join(Device, Device.sn == DeviceCombo.device_sn)
            .join(DeviceMerchantLink, (DeviceMerchantLink.device_sn == Device.sn) & DeviceMerchantLink.is_current)
            .join(Merchant, Merchant.id == DeviceMerchantLink.merchant_pk)
            .where(DeviceCombo.created_at_raw >= since)
            .group_by(Merchant.sub_name)
        )
    else:
        raise ValueError(f"unknown dim: {dim}")

    return {
        r.key or "未归一": {"recharge_cnt": int(r.cnt), "recharge_amount": D(r.amount)}
        for r in db.execute(stmt)
    }


def _fetch_refund_by_dim(db: Session, dim: str, since: datetime) -> dict[str, int]:
    """近 N 天退款笔数, 按维度聚合。"""
    if dim == "device_type":
        stmt = (
            select(Device.device_type_code.label("key"), func.count().label("cnt"))
            .select_from(Refund)
            .join(Device, Device.sn == Refund.device_sn)
            .where(Refund.created_at_raw >= since)
            .group_by(Device.device_type_code)
        )
    elif dim == "combo":
        # #270: 去掉 JOIN device_combos（充值流水一对多导致退款重复计数 2.5x），
        # 改按 Device.current_combo_name 分组（与设备数/活跃同口径）
        stmt = (
            select(Device.current_combo_name.label("key"), func.count().label("cnt"))
            .select_from(Refund)
            .join(Device, Device.sn == Refund.device_sn)
            .where(Refund.created_at_raw >= since)
            .group_by(Device.current_combo_name)
        )
    elif dim == "merchant":
        stmt = (
            select(Merchant.sub_name.label("key"), func.count().label("cnt"))
            .select_from(Refund)
            .join(Device, Device.sn == Refund.device_sn)
            .join(DeviceMerchantLink, (DeviceMerchantLink.device_sn == Device.sn) & DeviceMerchantLink.is_current)
            .join(Merchant, Merchant.id == DeviceMerchantLink.merchant_pk)
            .where(Refund.created_at_raw >= since)
            .group_by(Merchant.sub_name)
        )
    else:
        raise ValueError(f"unknown dim: {dim}")

    return {r.key or "未归一": int(r.cnt) for r in db.execute(stmt)}


def _fetch_complaint_by_dim(db: Session, dim: str, since: datetime) -> dict[str, int]:
    """近 N 天投诉数, 按维度聚合(complaint_scope 唯一出口)。"""
    base_cond = complaint_scope.is_complaint_ticket_cond(ServiceTicket.service_category)
    if dim == "device_type":
        stmt = (
            select(Device.device_type_code.label("key"), func.count().label("cnt"))
            .select_from(ServiceTicket)
            .join(Device, Device.sn == ServiceTicket.device_sn)
            .where(base_cond)
            .where(ServiceTicket.register_time >= since)
            .group_by(Device.device_type_code)
        )
    elif dim == "combo":
        # #270: 同退款修复，去掉 JOIN device_combos 放大，按 current_combo_name 分组
        stmt = (
            select(Device.current_combo_name.label("key"), func.count().label("cnt"))
            .select_from(ServiceTicket)
            .join(Device, Device.sn == ServiceTicket.device_sn)
            .where(base_cond)
            .where(ServiceTicket.register_time >= since)
            .group_by(Device.current_combo_name)
        )
    elif dim == "merchant":
        stmt = (
            select(Merchant.sub_name.label("key"), func.count().label("cnt"))
            .select_from(ServiceTicket)
            .join(Device, Device.sn == ServiceTicket.device_sn)
            .join(DeviceMerchantLink, (DeviceMerchantLink.device_sn == Device.sn) & DeviceMerchantLink.is_current)
            .join(Merchant, Merchant.id == DeviceMerchantLink.merchant_pk)
            .where(base_cond)
            .where(ServiceTicket.register_time >= since)
            .group_by(Merchant.sub_name)
        )
    else:
        raise ValueError(f"unknown dim: {dim}")

    return {r.key or "未归一": int(r.cnt) for r in db.execute(stmt)}


def _resolve_dim_name(db: Session, dim: str, key: str) -> str:
    """维度 key → 显示名(归一码查字典, 未命中回落原始名)。"""
    if dim == "device_type":
        r = db.execute(
            select(DimDeviceType.type_name).where(DimDeviceType.type_code == key)
        ).scalar_one_or_none()
        return r or key
    if dim == "combo":
        # 原始名 → 归一码 → 标准名
        alias = db.execute(
            select(ComboNameAlias.combo_code)
            .where(ComboNameAlias.raw_package_name == key)
            .where(ComboNameAlias.is_current.is_(True))
            .limit(1)
        ).scalar_one_or_none()
        if alias:
            r = db.execute(
                select(ComboDict.canonical_name).where(ComboDict.combo_code == alias)
            ).scalar_one_or_none()
            return r or key
        return key
    # merchant: key 即 sub_name
    return key


def category_ops_overview(
    db: Session,
    *,
    dim: str = "device_type",
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """分品类运营看板: 按维度聚合运营 KPI。

    返回 {dim, start, end, items: [{key, name, device_cnt, active_cnt,
    recharge_cnt, recharge_amount, refund_cnt, complaint_cnt, complaint_rate}]}。
    投诉率 = complaint_cnt / device_cnt * 100 (每百台)。
    """
    if dim not in ("device_type", "combo", "merchant"):
        raise ValueError(f"dim must be device_type/combo/merchant, got {dim}")

    now = datetime.now(UTC)
    end = end or now
    start = start or (end - timedelta(days=90))
    # 活跃/充值/退款/投诉回看窗口
    active_since = end - timedelta(days=90)
    risk_since = end - timedelta(days=30)

    devices = _fetch_dim_devices(db, dim)
    active_sns = _fetch_active_devices(db, active_since)
    recharges = _fetch_recharge_by_dim(db, dim, active_since)
    refunds = _fetch_refund_by_dim(db, dim, risk_since)
    complaints = _fetch_complaint_by_dim(db, dim, risk_since)

    # 活跃设备按维度归属(需要设备→维度映射, 用 Python 侧聚合)
    # 先取设备→维度 key 映射
    if dim == "device_type":
        sn_to_key = {
            r.sn: r.key or "未归一"
            for r in db.execute(select(Device.sn, Device.device_type_code.label("key")))
        }
    elif dim == "combo":
        sn_to_key = {
            r.sn: r.key or "未归一"
            for r in db.execute(select(Device.sn, Device.current_combo_name.label("key")))
        }
    else:  # merchant
        sn_to_key = {
            r.sn: r.key or "未归一"
            for r in db.execute(
                select(Device.sn, Merchant.sub_name.label("key"))
                .select_from(Device)
                .join(DeviceMerchantLink, (DeviceMerchantLink.device_sn == Device.sn) & DeviceMerchantLink.is_current)
                .join(Merchant, Merchant.id == DeviceMerchantLink.merchant_pk)
            )
        }

    active_by_dim: dict[str, int] = {}
    for sn in active_sns:
        key = sn_to_key.get(sn, "未归一")
        active_by_dim[key] = active_by_dim.get(key, 0) + 1

    # 合并所有维度 key
    all_keys = set(devices) | set(recharges) | set(refunds) | set(complaints)
    items = []
    for key in sorted(all_keys):
        d = devices.get(key, {"device_cnt": 0})
        rc = recharges.get(key, {"recharge_cnt": 0, "recharge_amount": Decimal("0")})
        rf = refunds.get(key, 0)
        cp = complaints.get(key, 0)
        device_cnt = d["device_cnt"]
        complaint_rate = round(cp / device_cnt * 100, 2) if device_cnt else 0.0
        items.append({
            "key": key,
            "name": _resolve_dim_name(db, dim, key),
            "device_cnt": device_cnt,
            "active_cnt": active_by_dim.get(key, 0),
            "recharge_cnt": rc["recharge_cnt"],
            "recharge_amount": float(rc["recharge_amount"]),
            "refund_cnt": rf,
            "complaint_cnt": cp,
            "complaint_rate": complaint_rate,
        })

    # 按充值金额降序取 top N
    items.sort(key=lambda x: x["recharge_amount"], reverse=True)
    return {
        "dim": dim,
        "start": _date_str(start),
        "end": _date_str(end),
        "items": items[:limit],
        "total_dim_count": len(all_keys),
    }
