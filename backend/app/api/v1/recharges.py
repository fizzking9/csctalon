"""流量与充值数据查询接口

两种口径:
1. /device-combos/local - 基于本库已同步的 device_combos 表查询(离线/报表用途)
2. /recharges/upstream 和 /traffic/upstream - 上游实时查询占位(需你提供真实 BMS/运营商/支付
   接口文档或抓包数据后在 _upstream_* 函数内填充)

所有接口支持按设备 SN / 商户 / 时间范围筛选,统一返回 {items, total, summary}。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.device_combo import DeviceCombo
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/recharges", tags=["流量与充值"])


# ---------- 工具 ----------

def _to_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"时间格式错误 {s}: {e}") from e


def _bytes_to_human(b: int | None) -> str | None:
    if b is None:
        return None
    if b == -1:
        return "无限"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < step:
            return f"{b:.2f} {unit}"
        b /= step  # type: ignore[assignment]
    return f"{b:.2f} PB"


def _serialize_combo(c: DeviceCombo) -> dict:
    return {
        "recharge_order_no": c.recharge_order_no,
        "device_sn": c.device_sn,
        "package_name": c.package_name,
        "package_type": c.package_type,
        "traffic_type": c.traffic_type,
        "merchant_name": c.merchant_name,
        "actual_amount": float(c.actual_amount) if c.actual_amount is not None else None,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "created_at_raw": c.created_at_raw.isoformat() if c.created_at_raw else None,
        "updated_at_raw": c.updated_at_raw.isoformat() if c.updated_at_raw else None,
        "valid_days": c.valid_days,
        "creator": c.creator,
        "available_traffic": c.available_traffic,
        "used_traffic": c.used_traffic,
        "remaining_traffic": c.remaining_traffic,
        "available_traffic_human": _bytes_to_human(c.available_traffic),
        "used_traffic_human": _bytes_to_human(c.used_traffic),
        "remaining_traffic_human": _bytes_to_human(c.remaining_traffic),
        "status": c.status,
    }


def _apply_combo_filters(stmt, *, sn, merchant, package_type, status, start, end, time_field="created_at_raw"):
    if sn:
        if isinstance(sn, str):
            stmt = stmt.where(DeviceCombo.device_sn == sn)
        else:  # list
            stmt = stmt.where(DeviceCombo.device_sn.in_(list(sn)))
    if merchant:
        stmt = stmt.where(DeviceCombo.merchant_name.ilike(f"%{merchant}%"))
    if package_type:
        stmt = stmt.where(DeviceCombo.package_type == package_type)
    if status:
        stmt = stmt.where(DeviceCombo.status == status)
    dt_col = getattr(DeviceCombo, time_field, DeviceCombo.created_at_raw)
    if start:
        stmt = stmt.where(dt_col >= start)
    if end:
        stmt = stmt.where(dt_col <= end)
    return stmt


# ---------- 本地 device_combos 数据查询 ----------

@router.get("/device-combos/local", response_model=R[PageBase], summary="本地-充值套餐&流量记录列表")
def list_local_combos(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    sn: str | None = Query(None, description="设备SN"),
    merchant: str | None = Query(None, description="商户(模糊)"),
    package_type: str | None = Query(None, description="套餐类型"),
    status: str | None = Query(None, description="套餐状态"),
    start: str | None = Query(None, description="充值开始日期(ISO)"),
    end: str | None = Query(None, description="充值结束日期(ISO)"),
    time_field: str = Query("created_at_raw", description="时间字段: created_at_raw/start_date/end_date"),
):
    if time_field not in {"created_at_raw", "start_date", "end_date"}:
        raise HTTPException(status_code=400, detail="time_field 非法")
    stmt = select(DeviceCombo)
    stmt = _apply_combo_filters(
        stmt,
        sn=sn, merchant=merchant, package_type=package_type, status=status,
        start=_to_dt(start), end=_to_dt(end), time_field=time_field,
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(
        db.scalars(
            stmt.order_by(DeviceCombo.created_at_raw.desc().nullslast())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize_combo(r) for r in rows], total=total or 0, page=page, page_size=page_size))


@router.get("/device-combos/local/summary", response_model=R, summary="本地-充值&流量汇总统计")
def local_combos_summary(
    db: DBSession,
    _: CurrentUser,
    sn: str | None = Query(None),
    merchant: str | None = Query(None),
    package_type: str | None = Query(None),
    status: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    stmt = select(DeviceCombo)
    stmt = _apply_combo_filters(
        stmt,
        sn=sn, merchant=merchant, package_type=package_type, status=status,
        start=_to_dt(start), end=_to_dt(end),
    )
    count_stmt = (
        select(
            func.count(),
            func.coalesce(func.sum(DeviceCombo.actual_amount), 0.0),
            func.coalesce(func.sum(DeviceCombo.available_traffic), 0),
            func.coalesce(func.sum(DeviceCombo.used_traffic), 0),
            func.coalesce(func.sum(DeviceCombo.remaining_traffic), 0),
        )
        .select_from(stmt.subquery())
    )
    row = db.execute(count_stmt).one_or_none()
    if row is None:
        total, amount, avail, used, remain = 0, 0.0, 0, 0, 0
    else:
        total, amount, avail, used, remain = row
    return R(data={
        "recharge_count": int(total or 0),
        "total_amount": float(amount or 0.0),
        "available_traffic": int(avail or 0),
        "used_traffic": int(used or 0),
        "remaining_traffic": int(remain or 0),
        "available_traffic_human": _bytes_to_human(int(avail or 0)),
        "used_traffic_human": _bytes_to_human(int(used or 0)),
        "remaining_traffic_human": _bytes_to_human(int(remain or 0)),
    })


@router.get("/device-combos/local/{sn}", response_model=R, summary="本地-单设备SN充值&流量明细")
def local_combo_by_sn(sn: str, db: DBSession, _: CurrentUser, limit: int = Query(100, ge=1, le=500)):
    """返回某设备所有充值套餐记录(按充值时间倒序),并附当前汇总状态。"""
    rows = list(
        db.scalars(
            select(DeviceCombo)
            .where(DeviceCombo.device_sn == sn)
            .order_by(DeviceCombo.created_at_raw.desc().nullslast())
            .limit(limit)
        )
    )
    total_amount = sum(float(r.actual_amount) for r in rows if r.actual_amount is not None)
    remain = sum((r.remaining_traffic or 0) for r in rows if (r.remaining_traffic or 0) != -1)
    summary = {
        "count": len(rows),
        "total_amount": float(total_amount),
        "remaining_traffic": remain,
        "remaining_traffic_human": _bytes_to_human(remain),
    }
    return R(data={"summary": summary, "items": [_serialize_combo(r) for r in rows]})


# ---------- 上游实时接口(占位,等提供真实接口文档/抓包后填充) ----------

def _upstream_query_recharges(params: dict) -> list[dict]:
    """TODO: 真实充值订单上游查询。

    预期行为:
      - 根据 sn / 商户 / 时间范围调用 BMS 或运营商 HTTP API
      - 统一映射为 { recharge_order_no, device_sn, merchant_name, package_name,
          actual_amount, paid_at, status, source } 结构
      - 失败时抛出 HTTPException(502, detail=上游错误)
    """
    # 占位实现:返回空列表 + 提示,便于前端/调用方联调
    return []


def _upstream_query_traffic(params: dict) -> dict | None:
    """TODO: 真实流量余额/使用量上游查询(按设备 SN 或 商户)。

    预期映射: { device_sn, available_bytes, used_bytes, remaining_bytes,
                quota_type, updated_at, raw_response }
    """
    return None


@router.get("/upstream", response_model=R, summary="[占位]上游-实时充值订单查询")
def upstream_recharges(
    _db: DBSession,
    _user: AdminOrSupervisor,
    sn: str | None = Query(None),
    merchant: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    **占位接口**: 调用上游真实充值查询(例如 BMS/支付侧订单查询)。

    当前未接入真实凭据/文档,返回空 items 并提示接入方式。
    请提供:
      - 接口 URL 与鉴权方式(token / sign / mTLS)
      - 请求参数与签名规则
      - 响应字段结构(或抓包 HAR)
    之后填充 app/api/v1/recharges.py 中 `_upstream_query_recharges` 即可。
    """
    items = _upstream_query_recharges({
        "sn": sn, "merchant": merchant,
        "start": _to_dt(start), "end": _to_dt(end),
        "page": page, "page_size": page_size,
    })
    return R(data={
        "items": items,
        "total": len(items),
        "_hint": "上游接口未接入,请提供真实接口文档/抓包后填充 _upstream_query_recharges。",
    })


@router.get("/traffic/upstream", response_model=R, summary="[占位]上游-实时流量余额/使用量查询")
def upstream_traffic(
    _db: DBSession,
    _user: AdminOrSupervisor,
    sn: str | None = Query(None, description="设备SN(按设备查)"),
    merchant: str | None = Query(None, description="商户(按商户汇总查)"),
):
    """
    **占位接口**: 调用上游查询设备/商户实时流量(例如运营商流量平台、BMS 套餐查询)。

    当前未接入真实凭据/文档,返回空结果并提示。接入方式同上:提供接口文档或抓包,
    填充 `_upstream_query_traffic`。
    """
    data = _upstream_query_traffic({"sn": sn, "merchant": merchant})
    return R(data={
        "result": data,
        "_hint": "上游接口未接入,请提供真实接口文档/抓包后填充 _upstream_query_traffic。",
    })
