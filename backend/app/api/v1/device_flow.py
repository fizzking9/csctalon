"""设备月流量接口 - 设备审计(单 SN × 月)

1. POST /device-flow/fetch   按 SN(可指定月份)拉取上游月流量并入库(幂等)
2. GET  /device-flow/usage   查询本地 device_flow_usage 历史
3. GET  /device-flow/usage/{sn} 单设备月流量明细 + 汇总

凭证来源: 优先请求体显式传; 否则读 bms-wifi 组凭证(get_sync_source_config("refund") 同域)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.device_flow_usage import DeviceFlowUsage
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/device-flow", tags=["设备流量"])


def _serialize(r: DeviceFlowUsage) -> dict:
    return {
        "id": r.id,
        "device_sn": r.device_sn,
        "merchant_id": r.merchant_id,
        "merchant_name": r.merchant_name,
        "device_type_name": r.device_type_name,
        "device_group_name": r.device_group_name,
        "device_combo_name": r.device_combo_name,
        "stat_month": r.stat_month,
        "flow_gb": float(r.flow_gb) if r.flow_gb is not None else None,
        "month_complete": r.month_complete,
        "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
    }


def _resolve_bms_credentials(db) -> tuple[str, str, str]:
    """取 wifi BMS 凭证(base_url, username, password): 优先 refund 源配置(同域同组)"""
    from app.services.sync_source_config_service import get_sync_source_config

    cfg = get_sync_source_config(db, "refund")
    if not cfg or not cfg.get("username") or not cfg.get("password"):
        # 回退 .env
        from app.core.config import settings

        if settings.BMS_USERNAME and settings.BMS_PASSWORD:
            return "https://wifi.cloud-cat.cn/bms", settings.BMS_USERNAME, settings.BMS_PASSWORD
        raise HTTPException(status_code=400, detail="未配置 BMS 凭证(请先配置 refund 源或 .env BMS_USERNAME)")
    return cfg["base_url"] or "https://wifi.cloud-cat.cn/bms", cfg["username"], cfg["password"]


@router.post("/fetch", response_model=R, summary="拉取设备月流量并入库(单SN)")
def fetch_device_flow(
    db: DBSession,
    _: AdminOrSupervisor,
    sn: str = Query(..., description="设备SN(一次一个)"),
    month: str | None = Query(None, description="月份 YYYY-MM; 空=该SN全量有流量月份"),
    username: str | None = Query(None, description="BMS账号(可选, 缺省用组凭证)"),
    password: str | None = Query(None, description="BMS密码(可选, 缺省用组凭证)"),
):
    """按 SN(可指定月)触发上游拉取并幂等入库。该操作会启动一次浏览器登录, 可能耗时数秒~数十秒。"""
    if not sn or not sn.strip():
        raise HTTPException(status_code=400, detail="sn 必填")
    if month and len(month) != 7:
        raise HTTPException(status_code=400, detail="month 格式应为 YYYY-MM")
    base_url, env_user, env_pwd = _resolve_bms_credentials(db)
    u = username or env_user
    p = password or env_pwd
    from app.services.device_flow_sync import sync_device_flow_sn

    try:
        result = sync_device_flow_sn(
            sn=sn.strip(), month=month, username=u, password=p, base_url=base_url
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"设备流量拉取失败: {e}") from e
    return R(data=result)


@router.get("/usage", response_model=R[PageBase], summary="设备月流量本地历史查询")
def list_usage(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    sn: str | None = Query(None, description="设备SN(模糊)"),
    merchant: str | None = Query(None, description="商户(模糊)"),
    month: str | None = Query(None, description="月份 YYYY-MM(精确)"),
    device_type: str | None = Query(None, description="设备类型"),
):
    stmt = select(DeviceFlowUsage)
    if sn:
        stmt = stmt.where(DeviceFlowUsage.device_sn.ilike(f"%{sn.strip()}%"))
    if merchant:
        stmt = stmt.where(DeviceFlowUsage.merchant_name.ilike(f"%{merchant.strip()}%"))
    if month:
        stmt = stmt.where(DeviceFlowUsage.stat_month == month.strip())
    if device_type:
        stmt = stmt.where(DeviceFlowUsage.device_type_name.ilike(f"%{device_type.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(DeviceFlowUsage.stat_month.desc().nullslast(), DeviceFlowUsage.device_sn)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize(r) for r in rows], total=total, page=page, page_size=page_size))


@router.get("/usage/{sn}", response_model=R, summary="单设备SN月流量明细+汇总")
def usage_by_sn(sn: str, db: DBSession, _: CurrentUser):
    rows = list(
        db.scalars(
            select(DeviceFlowUsage)
            .where(DeviceFlowUsage.device_sn == sn)
            .order_by(DeviceFlowUsage.stat_month.desc().nullslast())
        )
    )
    total_flow = sum(float(r.flow_gb) for r in rows if r.flow_gb is not None)
    merchant = next((r.merchant_name for r in rows if r.merchant_name), None)
    device_type = next((r.device_type_name for r in rows if r.device_type_name), None)
    return R(data={
        "device_sn": sn,
        "merchant_name": merchant,
        "device_type_name": device_type,
        "month_count": len(rows),
        "total_flow_gb": round(total_flow, 4),
        "items": [_serialize(r) for r in rows],
    })
