"""专项任务路由 - 项目式(一批设备 SN + 状态流转 + 跟进记录)

架构定位(G37~40):通用事务分派、跟进、管理、统计模块。
设备「打标签」即登记为专项任务,记录专项时间,不做设备操作。
权限:所有登录用户可查看;创建/修改/删除需 admin/supervisor。
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.device import Device
from app.models.task import SpecialTask, TaskDevice, TaskFollowup, TaskStatus
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/tasks", tags=["专项任务"])


# ---------- 序列化 ----------


def _serialize_task(t: SpecialTask, stats: dict | None = None) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "task_type": t.task_type,
        "operator_tag": t.operator_tag,
        "owner_id": t.owner_id,
        "status": t.status,
        "deadline": t.deadline.isoformat() if t.deadline else None,
        "description": t.description,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        **(stats or {}),
    }


def _serialize_followup(f: TaskFollowup) -> dict:
    return {
        "id": f.id,
        "author_name": f.author_name,
        "content": f.content,
        "status_change": f.status_change,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _serialize_device(d: TaskDevice) -> dict:
    return {
        "id": d.id,
        "device_sn": d.device_sn,
        "device_status": d.device_status,
        "note": d.note,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _device_stats(db, task_id: int) -> dict:
    """统计专项下设备数量及各状态分布"""
    rows = db.execute(
        select(TaskDevice.device_status, func.count()).where(TaskDevice.task_id == task_id).group_by(TaskDevice.device_status)
    ).all()
    by_status = {r[0]: r[1] for r in rows}
    return {
        "device_total": sum(by_status.values()),
        "device_pending": by_status.get("pending", 0),
        "device_executing": by_status.get("executing", 0),
        "device_done": by_status.get("done", 0),
        "device_abnormal": by_status.get("abnormal", 0),
    }


# ---------- SN 文本解析 ----------


def _parse_sn_text(text: str) -> list[str]:
    """解析设备 SN 文本:按换行/逗号/空格/制表符分隔,去重,过滤空值

    专项登记常以批量 SN 列表形式录入(从导出文件粘贴)。
    """
    if not text:
        return []
    import re

    parts = re.split(r"[\s,，;；\t]+", text)
    seen: list[str] = []
    seen_set: set[str] = set()
    for p in parts:
        s = p.strip()
        if s and s not in seen_set:
            seen.append(s)
            seen_set.add(s)
    return seen


def _ensure_devices(db, sns: list[str]) -> None:
    """SN 不存在则自动创建 Device(SN 全域唯一,缺失因导入不完整)"""
    if not sns:
        return
    existing = set(db.scalars(select(Device.sn).where(Device.sn.in_(sns))))
    to_create = [Device(sn=sn) for sn in sns if sn not in existing]
    if to_create:
        db.bulk_save_objects(to_create)
        db.flush()


# ---------- 任务 CRUD ----------


@router.get("", response_model=R[PageBase], summary="专项任务列表")
def list_tasks(
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    task_type: str | None = None,
    keyword: str | None = None,
):
    stmt = select(SpecialTask)
    if status:
        stmt = stmt.where(SpecialTask.status == status)
    if task_type:
        stmt = stmt.where(SpecialTask.task_type == task_type)
    if keyword:
        stmt = stmt.where(SpecialTask.title.ilike(f"%{keyword}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(SpecialTask.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(
        data=PageBase(
            items=[_serialize_task(t, _device_stats(db, t.id)) for t in items],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.post("", response_model=R, summary="创建专项任务")
def create_task(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="专项名称不能为空")
    task = SpecialTask(
        title=title,
        task_type=payload.get("task_type"),
        operator_tag=payload.get("operator_tag"),
        owner_id=payload.get("owner_id"),
        status=TaskStatus(payload.get("status", "pending_start")).value,
        deadline=datetime.fromisoformat(payload["deadline"]) if payload.get("deadline") else None,
        description=payload.get("description"),
        created_by=current_user.username,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    # 创建时支持一次性录入设备 SN
    if payload.get("sn_text"):
        sns = _parse_sn_text(payload["sn_text"])
        _ensure_devices(db, sns)
        for sn in sns:
            db.add(TaskDevice(task_id=task.id, device_sn=sn))
        db.commit()
    return R(data=_serialize_task(task, _device_stats(db, task.id)))


@router.get("/{task_id}", response_model=R, summary="专项任务详情")
def get_task(task_id: int, db: DBSession, _: CurrentUser):
    task = db.get(SpecialTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="专项任务不存在")
    data = _serialize_task(task, _device_stats(db, task_id))
    followups = list(
        db.scalars(select(TaskFollowup).where(TaskFollowup.task_id == task_id).order_by(TaskFollowup.id))
    )
    data["followups"] = [_serialize_followup(f) for f in followups]
    return R(data=data)


@router.put("/{task_id}", response_model=R, summary="更新专项任务")
def update_task(task_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    task = db.get(SpecialTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="专项任务不存在")
    for k in ("title", "task_type", "operator_tag", "owner_id", "description"):
        if k in payload:
            setattr(task, k, payload[k])
    if "deadline" in payload:
        task.deadline = datetime.fromisoformat(payload["deadline"]) if payload["deadline"] else None
    if "status" in payload:
        task.status = TaskStatus(payload["status"]).value
    db.commit()
    db.refresh(task)
    return R(data=_serialize_task(task, _device_stats(db, task_id)))


@router.delete("/{task_id}", response_model=R, summary="删除专项任务")
def delete_task(task_id: int, db: DBSession, _: AdminOrSupervisor):
    task = db.get(SpecialTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="专项任务不存在")
    # 级联删除设备批次与跟进记录(无 relationship 声明,需先 flush 确保子记录先删,避免 FK 约束)
    for td in db.scalars(select(TaskDevice).where(TaskDevice.task_id == task_id)):
        db.delete(td)
    for f in db.scalars(select(TaskFollowup).where(TaskFollowup.task_id == task_id)):
        db.delete(f)
    db.flush()
    db.delete(task)
    db.commit()
    return R()


# ---------- 设备批次管理 ----------


@router.get("/{task_id}/devices", response_model=R[PageBase], summary="专项设备批次列表")
def list_task_devices(
    task_id: int,
    db: DBSession,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    device_status: str | None = None,
    keyword: str | None = None,
):
    stmt = select(TaskDevice).where(TaskDevice.task_id == task_id)
    if device_status:
        stmt = stmt.where(TaskDevice.device_status == device_status)
    if keyword:
        stmt = stmt.where(TaskDevice.device_sn.ilike(f"%{keyword}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(TaskDevice.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return R(
        data=PageBase(
            items=[_serialize_device(d) for d in items],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/{task_id}/devices", response_model=R, summary="批量添加设备SN")
def add_task_devices(task_id: int, payload: dict, db: DBSession, _: AdminOrSupervisor):
    """批量添加设备 SN:解析换行/逗号/空格分隔的文本,去重,已存在则跳过"""
    task = db.get(SpecialTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="专项任务不存在")
    sns = _parse_sn_text(payload.get("sn_text", ""))
    if not sns:
        raise HTTPException(status_code=400, detail="未解析到有效 SN")
    _ensure_devices(db, sns)
    # 查已存在的 SN,跳过
    existing = set(
        db.scalars(
            select(TaskDevice.device_sn).where(
                TaskDevice.task_id == task_id, TaskDevice.device_sn.in_(sns)
            )
        )
    )
    added = 0
    for sn in sns:
        if sn not in existing:
            db.add(TaskDevice(task_id=task_id, device_sn=sn))
            added += 1
    db.commit()
    return R(data={"added": added, "skipped": len(sns) - added, "device_total": _device_stats(db, task_id)["device_total"]})


@router.put("/{task_id}/devices/{device_id}", response_model=R, summary="更新设备状态")
def update_task_device(
    task_id: int, device_id: int, payload: dict, db: DBSession, _: AdminOrSupervisor
):
    td = db.get(TaskDevice, device_id)
    if not td or td.task_id != task_id:
        raise HTTPException(status_code=404, detail="设备记录不存在")
    if "device_status" in payload:
        td.device_status = payload["device_status"]
    if "note" in payload:
        td.note = payload["note"]
    db.commit()
    db.refresh(td)
    return R(data=_serialize_device(td))


@router.delete("/{task_id}/devices/{device_id}", response_model=R, summary="移除设备")
def remove_task_device(task_id: int, device_id: int, db: DBSession, _: AdminOrSupervisor):
    td = db.get(TaskDevice, device_id)
    if not td or td.task_id != task_id:
        raise HTTPException(status_code=404, detail="设备记录不存在")
    db.delete(td)
    db.commit()
    return R()


# ---------- 跟进记录 ----------


@router.post("/{task_id}/followups", response_model=R, summary="添加跟进记录")
def add_task_followup(task_id: int, payload: dict, db: DBSession, current_user: CurrentUser):
    task = db.get(SpecialTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="专项任务不存在")
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="跟进内容不能为空")
    status_change = None
    # 带状态变更:记录流转并同步任务状态
    if payload.get("new_status"):
        old_status = task.status
        new_status = TaskStatus(payload["new_status"]).value
        if old_status != new_status:
            task.status = new_status
            status_change = f"{old_status} → {new_status}"
    followup = TaskFollowup(
        task_id=task_id,
        author_id=current_user.id,
        author_name=current_user.display_name,
        content=content,
        status_change=status_change,
    )
    db.add(followup)
    db.commit()
    db.refresh(followup)
    return R(data=_serialize_followup(followup))
