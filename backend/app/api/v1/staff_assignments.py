"""人员任职历史路由 - SCD2 任职记录 CRUD + 点查询 + HR 快照导入

挂在 /persons 前缀下(与 persons.py 同前缀,FastAPI 自动合并路由):
- GET    /persons/{person_id}/assignments           任职历史列表
- POST   /persons/{person_id}/assignments           新增任职(自动关闭旧 current)
- PUT    /persons/{person_id}/assignments/{aid}     编辑任职
- DELETE /persons/{person_id}/assignments/{aid}     删除任职(删 current 自动提升前一条)
- GET    /persons/{person_id}/assignment-at         点查询(某日有效任职)
- POST   /persons/import-snapshot/preview            HR 快照导入预览(dry-run,只读)
- POST   /persons/import-snapshot                    HR 人力快照 xlsx 确认导入
- GET    /persons/export                             导出 HR 主表 xlsx
"""

import io
from datetime import datetime, timezone
from urllib.parse import quote

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AdminOrSupervisor, AdminUser, CurrentUser, DBSession
from app.core.storage import resolve_upload_path
from app.models.person import Person, PersonAlias
from app.models.staff_assignment import StaffAssignment
from app.models.upload import UploadRecord
from app.schemas.common import R
from app.schemas.staff_assignment import (
    StaffAssignmentCreate,
    StaffAssignmentOut,
    StaffAssignmentUpdate,
)
from app.schemas.upload import ImportResponse
from app.services.data_parser import load_dataframe
from app.services.staff_assignment_service import (
    create_assignment,
    delete_assignment,
    get_assignment_at,
    list_assignments,
    update_assignment,
)
from app.services.staff_import import preview_staff_snapshot, upsert_staff_snapshot
from app.services.upsert import ImportResult

router = APIRouter(prefix="/persons", tags=["系统-人员任职"])


@router.get("/{person_id}/assignments", response_model=R, summary="任职历史列表(按生效日期倒序)")
def list_assignments_endpoint(
    person_id: int,
    db: DBSession,
    _: AdminOrSupervisor,
):
    items = list_assignments(db, person_id)
    return R(data=[StaffAssignmentOut.model_validate(a) for a in items])


@router.post("/{person_id}/assignments", response_model=R, summary="新增任职记录(自动关闭旧 current)")
def create_assignment_endpoint(
    person_id: int,
    payload: StaffAssignmentCreate,
    db: DBSession,
    _: AdminOrSupervisor,
):
    try:
        a = create_assignment(db, person_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    db.refresh(a)
    return R(data=StaffAssignmentOut.model_validate(a))


@router.put("/{person_id}/assignments/{assignment_id}", response_model=R, summary="编辑任职记录")
def update_assignment_endpoint(
    person_id: int,
    assignment_id: int,
    payload: StaffAssignmentUpdate,
    db: DBSession,
    _: AdminUser,
):
    try:
        a = update_assignment(db, person_id, assignment_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    return R(data=StaffAssignmentOut.model_validate(a))


@router.delete("/{person_id}/assignments/{assignment_id}", response_model=R, summary="删除任职记录")
def delete_assignment_endpoint(
    person_id: int,
    assignment_id: int,
    db: DBSession,
    _: AdminUser,
):
    try:
        delete_assignment(db, person_id, assignment_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    return R(data={"id": assignment_id, "deleted": True})


@router.get("/{person_id}/assignment-at", response_model=R, summary="点查询(某日有效任职)")
def assignment_at_endpoint(
    person_id: int,
    db: DBSession,
    _: CurrentUser,
    date: str = Query(..., description="查询日期 YYYY-MM-DD"),
):
    from datetime import date as date_cls

    try:
        target = date_cls.fromisoformat(date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from e
    a = get_assignment_at(db, person_id, target)
    return R(data=StaffAssignmentOut.model_validate(a) if a else None)


@router.post("/import-snapshot/preview", response_model=R, summary="HR 快照导入预览(dry-run,只读不写库)")
def import_snapshot_preview(
    payload: dict,
    db: DBSession,
    _: AdminOrSupervisor,
):
    """导入前预览:按 名称1/2/客服名 alias 匹配 Person,呈现每行将发生的动作
    (new/update/skip/conflict),供用户确认后才合并。不建 UploadRecord、不写库。

    Body: stored_path, filename, sheet_name, encoding, column_mapping
    """
    stored_path = payload.get("stored_path")
    sheet_name = payload.get("sheet_name")
    encoding = payload.get("encoding")
    mapping = payload.get("column_mapping") or {}
    if not stored_path:
        raise HTTPException(status_code=400, detail="stored_path 缺失")
    path = resolve_upload_path(stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="上传文件不存在,请重新上传")
    df = load_dataframe(path, sheet_name=sheet_name, encoding=encoding)
    diff = preview_staff_snapshot(df, mapping, db)
    return R(data=diff)


@router.post("/import-snapshot", response_model=R[ImportResponse], summary="HR 人力快照 xlsx 导入")
async def import_snapshot(
    payload: dict,
    db: DBSession,
    current_user: AdminUser,
):
    """导入 HR 人力快照:按 名称1/2/客服名 alias 匹配 Person,写 SCD2 任职历史 + 生命周期

    Body: stored_path, filename, sheet_name, encoding, column_mapping
    """
    stored_path = payload.get("stored_path")
    filename = payload.get("filename") or "staff_snapshot.xlsx"
    sheet_name = payload.get("sheet_name")
    encoding = payload.get("encoding")
    mapping = payload.get("column_mapping") or {}
    if not stored_path:
        raise HTTPException(status_code=400, detail="stored_path 缺失")

    path = resolve_upload_path(stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="上传文件不存在,请重新上传")

    record = UploadRecord(
        data_type="staff_snapshot",
        filename=filename,
        stored_path=stored_path,
        file_size=path.stat().st_size,
        sheet_name=sheet_name,
        status="parsing",
        uploaded_by=current_user.username,
        source="manual",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    try:
        df = load_dataframe(path, sheet_name=sheet_name, encoding=encoding)
        result = ImportResult()
        upsert_staff_snapshot(df, mapping, db, result)

        record.total_rows = result.total
        record.inserted_rows = result.inserted
        record.updated_rows = result.updated
        record.failed_rows = result.failed
        record.status = "imported"
        record.finished_at = datetime.now(timezone.utc)
        if result.errors:
            record.error_detail = {"errors": result.errors[:50]}
        db.commit()
        db.refresh(record)

        return R(
            data=ImportResponse(
                upload_id=record.id,
                data_type=record.data_type,
                filename=record.filename,
                total_rows=record.total_rows,
                inserted_rows=record.inserted_rows,
                updated_rows=record.updated_rows,
                failed_rows=record.failed_rows,
                matched_rows=0,
                status=record.status,
                errors=result.errors[:50] if result.errors else None,
                finished_at=record.finished_at,
            )
        )
    except Exception as e:
        db.rollback()
        record = db.get(UploadRecord, record.id)
        if record:
            record.status = "failed"
            record.error_detail = {"error": str(e)[:2000]}
            record.finished_at = datetime.now(timezone.utc)
            db.commit()
        raise HTTPException(status_code=500, detail=f"导入失败: {e}") from e


@router.get("/export", summary="导出 HR 主表 xlsx(列对齐导入格式,可 round-trip)")
def export_persons(db: DBSession, _: CurrentUser):
    """导出在职人员主表为 xlsx,列对齐 COLUMN_TEMPLATES["staff"]:
    姓名/客服名/名称1/名称2/备注/专项/岗位/更新时间 + 生命周期四列。
    别名按 source_field 回填(优先 staff_snapshot 来源);当前任职取 position/specialty/effective_from。
    """
    persons = list(db.scalars(select(Person).where(Person.is_active.is_(True)).order_by(Person.id)))
    # 一次性取所有别名和当前任职,避免 N+1
    alias_map: dict[int, dict[str, str]] = {}
    for a in db.scalars(
        select(PersonAlias).where(PersonAlias.person_id.in_([p.id for p in persons] or [0]))
    ).all():
        alias_map.setdefault(a.person_id, {})
        slot = alias_map[a.person_id].get(a.source_field or "")
        # 优先 staff_snapshot 来源;已存在且非 staff_snapshot 时不覆盖
        if slot and a.source_table != "staff_snapshot":
            continue
        alias_map[a.person_id][a.source_field or ""] = a.alias
    cur_assign: dict[int, StaffAssignment] = {
        sa.person_id: sa
        for sa in db.scalars(
            select(StaffAssignment).where(
                StaffAssignment.person_id.in_([p.id for p in persons] or [0]),
                StaffAssignment.is_current.is_(True),
            )
        ).all()
    }

    rows = []
    for p in persons:
        am = alias_map.get(p.id, {})
        sa = cur_assign.get(p.id)
        rows.append({
            "姓名": p.display_name,
            "客服名": am.get("客服名", ""),
            "名称1": am.get("名称1", ""),
            "名称2": am.get("名称2", ""),
            "备注": p.remark or "",
            "专项": (sa.specialty if sa else p.specialty) or "",
            "岗位": (sa.position if sa else p.position) or "",
            "更新时间": (sa.effective_from if sa else None),
            "入职日期": p.hire_date,
            "试用期到期": p.probation_end_date,
            "转正日期": p.regularization_date,
            "离职日期": p.resignation_date,
        })

    df = pd.DataFrame(rows, columns=[
        "姓名", "客服名", "名称1", "名称2", "备注", "专项", "岗位", "更新时间",
        "入职日期", "试用期到期", "转正日期", "离职日期",
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="人力资源")
    buf.seek(0)
    fname = quote("人力资源数据_导出.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )
