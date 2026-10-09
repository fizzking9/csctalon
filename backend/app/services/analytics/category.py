"""分品类运营看板聚合服务 (#254 B3-10)

设计说明（Design Rationale）:
- 目的(替谁/答何决策): 替运营主管回答「哪类设备/哪个套餐/哪个商户号是
  营收主力、谁投诉风险最高」, 在周/月复盘中做资源倾斜与风险管控决策。
- 逻辑(为何这样组织): 三个维度(设备类型/套餐/商户号)各出 top N 聚合,
  指标统一为「规模→活跃→营收→风险」四级, 对标正规商业运营:
  ① 规模: 设备数; ② 活跃: 近90天有充值或流量的设备数;
  ③ 营收: 近90天充值金额(Decimal); ④ 风险: 近30天退款笔数/投诉数/投诉率。
- 口径(#293 改): ① 全部活动量列(活跃/充值/退款/投诉)由 start/end **单一窗口**驱动,
  不再硬编码 90/30 天拼盘(与运营分析看板 grain 标准对齐);
  ② 退款 = 实际退款口径A: NOT IN (无需退款/驳回/申请中)(与退款分析 4442e89 对齐,
  修复前含申请中夸 ~40%);
  ③ 设备类型 key 改 devices.device_type(中文名) —— device_type_code 全表 0 非空,
  旧口径 704,139 台全部落「未归一」一桶, 分类功能失效(#293 实测);
  设备数=存量语义(devices 全量按维度分组); 投诉率分母=窗口内活跃设备(同窗可比)。
- 商户号走 devices.merchant_name(#271);
  投诉走 complaint_scope 唯一出口; 充值金额走 device_combos
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
from app.models.device import Device
from app.models.device_combo import DeviceCombo
from app.models.device_flow_usage import DeviceFlowUsage

# from app.models.merchant import DeviceMerchantLink, Merchant  # #271: 暂用 devices.merchant_name，links/merchants 表全空
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.services import complaint_scope
from app.services.analytics._core import D


def _date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _fetch_dim_devices(db: Session, dim: str) -> dict[str, dict[str, Any]]:
    """按维度聚合设备数(存量语义: devices 全量)。"""
    if dim == "device_type":
        # #293: key 改 device_type(中文名) —— device_type_code 全表 0 非空, 旧 key 分类失效
        stmt = (
            select(
                Device.device_type.label("key"),
                func.count(Device.sn).label("device_cnt"),
            )
            .group_by(Device.device_type)
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
        # #271: links/merchants 表生产全空，商户号信息实际在 devices.merchant_name
        stmt = (
            select(
                Device.merchant_name.label("key"),
                func.count(Device.sn).label("device_cnt"),
            )
            .where(Device.merchant_name.isnot(None))
            .group_by(Device.merchant_name)
        )
    else:
        raise ValueError(f"unknown dim: {dim}")

    return {r.key or "未归一": {"device_cnt": int(r.device_cnt)} for r in db.execute(stmt)}


def _fetch_active_devices(db: Session, since: datetime, end: datetime) -> set[str]:
    """窗口内(since~end, 含端点)有充值或流量的设备 SN 集合。

    #293: 补 end 上界——修复前只过滤 >=since, 「上月窗口」实际查到全量至今,
    环比全线假降 60-85%。
    """
    # 质检套餐排除(#293): 出厂质检流水非真实充值, 不应计入充值活跃
    not_qc = DeviceCombo.package_name.notilike("%质检%")
    # 充值活跃
    rc = {
        r[0]
        for r in db.execute(
            select(distinct(DeviceCombo.device_sn))
            .where(DeviceCombo.device_sn.isnot(None))
            .where(DeviceCombo.created_at_raw >= since)
            .where(DeviceCombo.created_at_raw <= end)
            .where(not_qc)
        )
    }
    # 流量活跃(窗口覆盖的完整月有流量记录)
    fl = {
        r[0]
        for r in db.execute(
            select(distinct(DeviceFlowUsage.device_sn))
            .where(DeviceFlowUsage.device_sn.isnot(None))
            .where(DeviceFlowUsage.month_complete.is_(True))
            .where(DeviceFlowUsage.stat_month >= since.strftime("%Y-%m"))
            .where(DeviceFlowUsage.stat_month <= end.strftime("%Y-%m"))
        )
    }
    return rc | fl


def _fetch_recharge_by_dim(db: Session, dim: str, since: datetime, end: datetime) -> dict[str, dict[str, Any]]:
    """窗口内充值笔数/金额, 按维度聚合。

    #293: 排除「质检」套餐(package_name LIKE '%质检%') —— 设备出厂质检流程记录
    (单笔 999/9999 元, 2026-10 达 7,948 笔/881 万, 占当期流水 57.5%), 非真实营收;
    修复前 CategoryBoard 充值金额虚高 ~2.4 倍。
    """
    # 质检套餐排除条件(出厂质检, 非用户付费)
    not_qc = DeviceCombo.package_name.notilike("%质检%")
    if dim == "device_type":
        stmt = (
            select(
                Device.device_type.label("key"),
                func.count().label("cnt"),
                func.coalesce(func.sum(DeviceCombo.actual_amount), 0).label("amount"),
            )
            .select_from(DeviceCombo)
            .join(Device, Device.sn == DeviceCombo.device_sn)
            .where(DeviceCombo.created_at_raw >= since)
            .where(DeviceCombo.created_at_raw <= end)
            .where(not_qc)
            .group_by(Device.device_type)
        )
    elif dim == "combo":
        stmt = (
            select(
                DeviceCombo.package_name.label("key"),
                func.count().label("cnt"),
                func.coalesce(func.sum(DeviceCombo.actual_amount), 0).label("amount"),
            )
            .where(DeviceCombo.created_at_raw >= since)
            .where(DeviceCombo.created_at_raw <= end)
            .where(not_qc)
            .group_by(DeviceCombo.package_name)
        )
    elif dim == "merchant":
        # #271: 走 devices.merchant_name（links/merchants 表全空）
        stmt = (
            select(
                Device.merchant_name.label("key"),
                func.count().label("cnt"),
                func.coalesce(func.sum(DeviceCombo.actual_amount), 0).label("amount"),
            )
            .select_from(DeviceCombo)
            .join(Device, Device.sn == DeviceCombo.device_sn)
            .where(DeviceCombo.created_at_raw >= since)
            .where(DeviceCombo.created_at_raw <= end)
            .where(not_qc)
            .where(Device.merchant_name.isnot(None))
            .group_by(Device.merchant_name)
        )
    else:
        raise ValueError(f"unknown dim: {dim}")

    return {
        r.key or "未归一": {"recharge_cnt": int(r.cnt), "recharge_amount": D(r.amount)}
        for r in db.execute(stmt)
    }


def _fetch_refund_by_dim(db: Session, dim: str, since: datetime, end: datetime) -> dict[str, int]:
    """窗口内实际退款笔数(口径A), 按维度聚合。

    #293: 加 status 口径A过滤(NOT IN 无需退款/驳回/申请中), 与退款分析 4442e89 对齐;
    修复前把申请中/登记类全算进「退款笔数」, 近30天夸 ~40%。
    #293: 补 end 上界(与充值/活跃同窗)。
    """
    # 口径A: 排除非实际退款状态(与 refund.py 10 处 NOT IN 同模式)
    actual_refund_cond = (
        func.coalesce(Refund.status, "").notin_(("无需退款", "驳回", "申请中"))
    )
    if dim == "device_type":
        stmt = (
            select(Device.device_type.label("key"), func.count().label("cnt"))
            .select_from(Refund)
            .join(Device, Device.sn == Refund.device_sn)
            .where(Refund.created_at_raw >= since)
            .where(Refund.created_at_raw <= end)
            .where(actual_refund_cond)
            .group_by(Device.device_type)
        )
    elif dim == "combo":
        # #270: 去掉 JOIN device_combos（充值流水一对多导致退款重复计数 2.5x），
        # 改按 Device.current_combo_name 分组（与设备数/活跃同口径）
        stmt = (
            select(Device.current_combo_name.label("key"), func.count().label("cnt"))
            .select_from(Refund)
            .join(Device, Device.sn == Refund.device_sn)
            .where(Refund.created_at_raw >= since)
            .where(Refund.created_at_raw <= end)
            .where(actual_refund_cond)
            .group_by(Device.current_combo_name)
        )
    elif dim == "merchant":
        # #271: 走 devices.merchant_name（links/merchants 表全空）
        stmt = (
            select(Device.merchant_name.label("key"), func.count().label("cnt"))
            .select_from(Refund)
            .join(Device, Device.sn == Refund.device_sn)
            .where(Refund.created_at_raw >= since)
            .where(Refund.created_at_raw <= end)
            .where(actual_refund_cond)
            .where(Device.merchant_name.isnot(None))
            .group_by(Device.merchant_name)
        )
    else:
        raise ValueError(f"unknown dim: {dim}")

    return {r.key or "未归一": int(r.cnt) for r in db.execute(stmt)}


def _fetch_complaint_by_dim(db: Session, dim: str, since: datetime, end: datetime) -> dict[str, int]:
    """窗口内投诉数, 按维度聚合(complaint_scope 唯一出口)。#293: 补 end 上界。"""
    base_cond = complaint_scope.is_complaint_ticket_cond(ServiceTicket.service_category)
    if dim == "device_type":
        stmt = (
            select(Device.device_type.label("key"), func.count().label("cnt"))
            .select_from(ServiceTicket)
            .join(Device, Device.sn == ServiceTicket.device_sn)
            .where(base_cond)
            .where(ServiceTicket.register_time >= since)
            .where(ServiceTicket.register_time <= end)
            .group_by(Device.device_type)
        )
    elif dim == "combo":
        # #270: 同退款修复，去掉 JOIN device_combos 放大，按 current_combo_name 分组
        stmt = (
            select(Device.current_combo_name.label("key"), func.count().label("cnt"))
            .select_from(ServiceTicket)
            .join(Device, Device.sn == ServiceTicket.device_sn)
            .where(base_cond)
            .where(ServiceTicket.register_time >= since)
            .where(ServiceTicket.register_time <= end)
            .group_by(Device.current_combo_name)
        )
    elif dim == "merchant":
        # #271: 走 devices.merchant_name（links/merchants 表全空）
        stmt = (
            select(Device.merchant_name.label("key"), func.count().label("cnt"))
            .select_from(ServiceTicket)
            .join(Device, Device.sn == ServiceTicket.device_sn)
            .where(base_cond)
            .where(ServiceTicket.register_time >= since)
            .where(ServiceTicket.register_time <= end)
            .where(Device.merchant_name.isnot(None))
            .group_by(Device.merchant_name)
        )
    else:
        raise ValueError(f"unknown dim: {dim}")

    return {r.key or "未归一": int(r.cnt) for r in db.execute(stmt)}


def _resolve_dim_name(db: Session, dim: str, key: str) -> str:
    """维度 key → 显示名。

    #293: device_type key 已改中文名(device_type), 无需再查归一码字典, 直接回落 key。
    """
    if dim == "device_type":
        return key
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

    #293 口径: 活跃/充值/退款/投诉 **全部由 start/end 单一窗口驱动**(不再 90/30 拼盘,
    与运营分析看板 grain 标准对齐); 退款=口径A(实际退款); 设备数=存量语义;
    投诉率 = complaint_cnt / active_cnt * 100 (分母=窗口内活跃设备, 同窗可比;
    旧口径分母=全量存量含50万台历史设备, 投诉率被稀释失真)。
    """
    if dim not in ("device_type", "combo", "merchant"):
        raise ValueError(f"dim must be device_type/combo/merchant, got {dim}")

    now = datetime.now(UTC)
    end = end or now
    start = start or (end - timedelta(days=90))

    devices = _fetch_dim_devices(db, dim)
    active_sns = _fetch_active_devices(db, start, end)
    recharges = _fetch_recharge_by_dim(db, dim, start, end)
    refunds = _fetch_refund_by_dim(db, dim, start, end)
    complaints = _fetch_complaint_by_dim(db, dim, start, end)

    # 活跃设备按维度归属(需要设备→维度映射, 用 Python 侧聚合)
    # 先取设备→维度 key 映射
    if dim == "device_type":
        # #293: key 改 device_type(中文名), 与 _fetch_dim_devices 同源
        sn_to_key = {
            r.sn: r.key or "未归一"
            for r in db.execute(select(Device.sn, Device.device_type.label("key")))
        }
    elif dim == "combo":
        sn_to_key = {
            r.sn: r.key or "未归一"
            for r in db.execute(select(Device.sn, Device.current_combo_name.label("key")))
        }
    else:  # merchant
        # #271: 走 devices.merchant_name（links/merchants 表全空）
        sn_to_key = {
            r.sn: r.key or "未归一"
            for r in db.execute(select(Device.sn, Device.merchant_name.label("key")))
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
        # #293: 投诉率分母改窗口内活跃设备(同窗可比), 旧口径分母=全量存量被稀释失真
        active_cnt = active_by_dim.get(key, 0)
        complaint_rate = round(cp / active_cnt * 100, 2) if active_cnt else 0.0
        items.append({
            "key": key,
            "name": _resolve_dim_name(db, dim, key),
            "device_cnt": device_cnt,
            "active_cnt": active_cnt,
            "recharge_cnt": rc["recharge_cnt"],
            "recharge_amount": float(rc["recharge_amount"]),
            "refund_cnt": rf,
            "complaint_cnt": cp,
            "complaint_rate": complaint_rate,
        })

    # 按充值金额降序取 top N
    items.sort(key=lambda x: x["recharge_amount"], reverse=True)
    # #293: totals=全桶聚合(不受 limit 截断), KPI 求和必须用它——
    # 回填后 device_type 桶数已 >50, 前端 sum(items) 会因截断少算长尾(实测少算 ~75%)
    totals = {
        "device_cnt": sum(i["device_cnt"] for i in items),
        "active_cnt": sum(i["active_cnt"] for i in items),
        "recharge_cnt": sum(i["recharge_cnt"] for i in items),
        "recharge_amount": round(sum(i["recharge_amount"] for i in items), 2),
        "refund_cnt": sum(i["refund_cnt"] for i in items),
        "complaint_cnt": sum(i["complaint_cnt"] for i in items),
    }
    return {
        "dim": dim,
        "start": _date_str(start),
        "end": _date_str(end),
        "items": items[:limit],
        "totals": totals,
        "total_dim_count": len(all_keys),
    }
