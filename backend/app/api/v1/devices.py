"""设备管理路由"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DBSession, ManagerUser
from app.core.database import get_db
from app.models.device import Device, DeviceTag, DeviceTagBatch, DeviceTagBinding, device_tag_rel
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal
from app.schemas.common import PageBase, R
from app.services.audit_timeline import build_device_timeline

router = APIRouter(prefix="/devices", tags=["设备管理"])


@router.get("", response_model=R[PageBase], summary="设备列表")
def list_devices(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    merchant_name: str | None = None,
    tag_id: int | None = None,
):
    stmt = select(Device)
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(Device.sn.like(kw), Device.device_type.like(kw), Device.device_type_remark.like(kw)))
    if merchant_name:
        stmt = stmt.where(Device.merchant_name == merchant_name)
    if tag_id:
        stmt = stmt.join(device_tag_rel, device_tag_rel.c.device_sn == Device.sn).where(
            device_tag_rel.c.tag_id == tag_id
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(Device.sn).offset((page - 1) * page_size).limit(page_size)).unique())
    return R(
        data=PageBase(
            items=[
                {
                    "sn": d.sn,
                    "device_type": d.device_type,
                    "device_type_remark": d.device_type_remark,
                    "merchant_name": d.merchant_name,
                    "status": d.status,
                    "active_time": d.active_time.isoformat() if d.active_time else None,
                    "tags": [{"id": t.id, "name": t.tag_name, "color": t.color} for t in (d.tags or [])],
                }
                for d in items
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/{sn}", response_model=R, summary="设备详情(聚合各表数据)")
def get_device(sn: str, db: DBSession, _: CurrentUser):
    device = db.get(Device, sn)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    # 聚合退费单
    refunds = list(db.scalars(select(Refund).where(Refund.device_sn == sn).order_by(Refund.created_at_raw.desc()).limit(50)))
    tickets = list(db.scalars(select(ServiceTicket).where(ServiceTicket.device_sn == sn).order_by(ServiceTicket.register_time.desc()).limit(20)))
    wallets = list(db.scalars(select(WalletWithdrawal).where(WalletWithdrawal.device_sn == sn).order_by(WalletWithdrawal.op_time.desc()).limit(20)))
    merchant_refunds = list(db.scalars(select(MerchantRefund).where(MerchantRefund.device_no == sn).order_by(MerchantRefund.trade_time.desc()).limit(20)))
    return R(
        data={
            "device": {
                "sn": device.sn,
                "device_type": device.device_type,
                "device_type_remark": device.device_type_remark,
                "merchant_name": device.merchant_name,
                "status": device.status,
                "active_time": device.active_time.isoformat() if device.active_time else None,
                "tags": [{"id": t.id, "name": t.tag_name, "color": t.color} for t in (device.tags or [])],
            },
            "refund_count": db.scalar(select(func.count()).select_from(Refund).where(Refund.device_sn == sn)) or 0,
            "refund_total": float(db.scalar(select(func.coalesce(func.sum(Refund.refund_amount), 0)).where(Refund.device_sn == sn)) or 0),
            "cash_out_total": float(
                db.scalar(select(func.coalesce(func.sum(MerchantRefund.refund_amount), 0)).where(MerchantRefund.device_no == sn, MerchantRefund.refund_amount > 0)) or 0
            ),
            "recent_refunds": [
                {
                    "refund_no": r.refund_no,
                    "amount": float(r.refund_amount) if r.refund_amount else None,
                    "status": r.status,
                    "created_at_raw": r.created_at_raw.isoformat() if r.created_at_raw else None,
                }
                for r in refunds
            ],
            "recent_tickets": [
                {
                    "ticket_no": t.ticket_no,
                    "service_item": t.service_item,
                    "handler": t.handler,
                    "status": t.status,
                    "register_time": t.register_time.isoformat() if t.register_time else None,
                }
                for t in tickets
            ],
        }
    )


@router.get("/{sn}/timeline", response_model=R, summary="设备审计时序流(跨表合并)")
def device_timeline(
    sn: str,
    db: DBSession,
    _: CurrentUser,
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
    days: int | None = Query(90, ge=0, description="时间窗口天数,0=不限"),
    include_person_name: bool = Query(True, description="每条事件包含操作人姓名(如有)"),
    include_device_sn: bool = Query(False, description="每条事件包含设备SN(如已有subject通常不需)"),
):
    """设备维度审计时序:合并 7 张业务表(refund/ticket/wallet/merchant_refund/cs_record/
    external_complaint/card_complaint)按 device_sn 关联的记录,按时间倒序统一呈现。

    用于设备审计页:时序流 + 明细表双视图。
    """
    device = db.get(Device, sn)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    result = build_device_timeline(
        db, sn,
        limit=limit,
        days=days if days else None,
        include_person_name=include_person_name,
        include_device_sn=include_device_sn,
    )
    # 补设备基础信息
    result["subject"] = {
        "kind": "device",
        "sn": device.sn,
        "device_type": device.device_type,
        "device_type_remark": device.device_type_remark,
        "merchant_name": device.merchant_name,
        "status": device.status,
        "active_time": device.active_time.isoformat() if device.active_time else None,
        "tags": [{"id": t.id, "name": t.tag_name, "color": t.color} for t in (device.tags or [])],
    }
    return R(data=result)


@router.put("/{sn}/merchant", response_model=R, summary="更新设备商户")
def update_merchant(sn: str, payload: dict, db: DBSession, _: AdminUser):
    device = db.get(Device, sn)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    device.merchant_name = payload.get("merchant_name")
    db.commit()
    return R(message="更新成功")


# ---------- 标签 ----------


@router.get("/tags/list", response_model=R, summary="标签列表")
def list_tags(db: DBSession, _: CurrentUser):
    tags = list(db.scalars(select(DeviceTag).order_by(DeviceTag.id)))
    return R(data=[{"id": t.id, "tag_name": t.tag_name, "tag_type": t.tag_type, "color": t.color, "description": t.description} for t in tags])


@router.post("/tags", response_model=R, summary="创建标签")
def create_tag(payload: dict, db: DBSession, _: AdminUser):
    tag = DeviceTag(
        tag_name=payload["tag_name"],
        tag_type=payload.get("tag_type", "custom"),
        color=payload.get("color"),
        description=payload.get("description"),
    )
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return R(data={"id": tag.id, "tag_name": tag.tag_name})


@router.post("/{sn}/tags/{tag_id}", response_model=R, summary="设备打标")
def add_tag(sn: str, tag_id: int, db: DBSession, current_user: CurrentUser):
    device = db.get(Device, sn)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    tag = db.get(DeviceTag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    # 检查是否已打标
    if tag not in (device.tags or []):
        device.tags.append(tag)
        db.commit()
    return R(message="打标成功")


@router.delete("/{sn}/tags/{tag_id}", response_model=R, summary="移除设备标签")
def remove_tag(sn: str, tag_id: int, db: DBSession, _: AdminUser):
    device = db.get(Device, sn)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    tag = db.get(DeviceTag, tag_id)
    if tag and tag in (device.tags or []):
        device.tags.remove(tag)
        db.commit()
    return R(message="移除成功")


# ---------- 设备专项标签批量导入(规划设计 §4.3) ----------


def _gen_batch_no() -> str:
    from datetime import datetime as _dt
    return "BT" + _dt.now().strftime("%Y%m%d%H%M%S%f")


@router.post("/tags/batch-import", response_model=R, summary="批量导入SN打标")
def batch_import(payload: dict, db: DBSession, current_user: ManagerUser):
    """批量 SN 打标: 提交 SN 清单, 系统校验设备是否存在并打标, 回执成功/已存在/无效。

    payload: {tag_id, title?, sn_list: [str], note?}
    """
    tag_id = payload.get("tag_id")
    if not tag_id:
        raise HTTPException(status_code=400, detail="缺少 tag_id")
    tag = db.get(DeviceTag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    raw_sns = [str(s).strip() for s in payload.get("sn_list", []) if str(s).strip()]
    # 去重保序
    seen = set()
    sns = []
    for s in raw_sns:
        if s not in seen:
            seen.add(s)
            sns.append(s)

    batch = DeviceTagBatch(
        batch_no=_gen_batch_no(),
        tag_id=tag_id,
        title=payload.get("title"),
        note=payload.get("note"),
        total_count=len(sns),
        matched_count=0,
        created_by=current_user.username,
    )
    db.add(batch)
    db.flush()

    matched = 0
    already = 0
    invalid = []
    for sn in sns:
        dev = db.get(Device, sn)
        if not dev:
            invalid.append(sn)
            continue
        # 检查是否已打标(按 sn+tag_id)
        existing = db.scalar(
            select(DeviceTagBinding).where(
                DeviceTagBinding.device_sn == sn, DeviceTagBinding.tag_id == tag_id
            )
        )
        if existing:
            if existing.batch_id is None:
                existing.batch_id = batch.id
            if existing.status != "active":
                existing.status = "active"
            already += 1
            continue
        db.add(
            DeviceTagBinding(
                device_sn=sn,
                tag_id=tag_id,
                created_by=current_user.username,
                batch_id=batch.id,
                status="active",
            )
        )
        matched += 1
    batch.matched_count = matched
    batch.total_count = len(sns)
    db.commit()
    db.refresh(batch)
    return R(
        data={
            "batch_id": batch.id,
            "batch_no": batch.batch_no,
            "total": len(sns),
            "matched": matched,
            "already": already,
            "invalid": invalid,
            "invalid_count": len(invalid),
        }
    )


@router.get("/tags/batches", response_model=R, summary="标签批次列表")
def list_batches(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = select(DeviceTagBatch).order_by(DeviceTagBatch.id.desc())
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    tag_names = {t.id: t.tag_name for t in db.scalars(select(DeviceTag))}
    return R(
        data=PageBase(
            items=[
                {
                    "id": b.id,
                    "batch_no": b.batch_no,
                    "tag_id": b.tag_id,
                    "tag_name": tag_names.get(b.tag_id),
                    "title": b.title,
                    "total_count": b.total_count,
                    "matched_count": b.matched_count,
                    "note": b.note,
                    "created_by": b.created_by,
                    "created_at": b.created_at.isoformat() if b.created_at else None,
                }
                for b in items
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/tags/stats", response_model=R, summary="标签统计(各标签SN数)")
def tag_stats(db: DBSession, _: CurrentUser):
    rows = db.execute(
        select(
            device_tag_rel.c.tag_id,
            func.count().label("cnt"),
        )
        .where(device_tag_rel.c.status == "active")
        .group_by(device_tag_rel.c.tag_id)
    ).all()
    tag_names = {t.id: t.tag_name for t in db.scalars(select(DeviceTag))}
    data = [
        {"tag_id": tag_id, "tag_name": tag_names.get(tag_id), "sn_count": int(cnt)}
        for tag_id, cnt in rows
    ]
    data.sort(key=lambda x: -x["sn_count"])
    return R(data=data)
