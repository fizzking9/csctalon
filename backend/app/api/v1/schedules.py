"""排班路由(规划设计 §4.1)

排班源头为企业微信(对接同步), 系统内做展示 + 顶班/请假微调 + 与接待/工单关联对照。
"""

from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import column, func, select, table

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.models.person import Person, PersonAlias

# #338: 排班读 std_all 镜像
_CS = table("std_cs_record_all", column("operator"), column("operation_time"))
from app.models.schedule import Attendance, Schedule, ShiftTemplate
from app.models.ticket import ServiceTicket
from app.schemas.common import R

router = APIRouter(prefix="/schedules", tags=["排班管理"])

_DEFAULT_SHIFTS = [
    {"name": "早班", "start_time": "08:00", "end_time": "16:00", "color": "#409eff", "sort_order": 1},
    {"name": "晚班", "start_time": "16:00", "end_time": "24:00", "color": "#e6a23c", "sort_order": 2},
]


def _ensure_default_shifts(db) -> None:
    existing = db.scalar(select(func.count()).select_from(ShiftTemplate))
    if existing == 0:
        for s in _DEFAULT_SHIFTS:
            db.add(ShiftTemplate(**s))
        db.commit()


@router.get("/shifts", response_model=R, summary="班次模板列表")
def list_shifts(db: DBSession, _: CurrentUser):
    _ensure_default_shifts(db)
    rows = db.scalars(select(ShiftTemplate).order_by(ShiftTemplate.sort_order))
    return R(
        data=[
            {
                "id": s.id,
                "name": s.name,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "is_overnight": s.is_overnight,
                "color": s.color,
                "sort_order": s.sort_order,
                "is_active": s.is_active,
            }
            for s in rows
        ]
    )


@router.post("/shifts", response_model=R, summary="新建班次模板")
def create_shift(payload: dict, db: DBSession, _: ManagerUser):
    s = ShiftTemplate(
        name=payload["name"],
        start_time=payload.get("start_time", "08:00"),
        end_time=payload.get("end_time", "16:00"),
        is_overnight=bool(payload.get("is_overnight", False)),
        color=payload.get("color"),
        sort_order=payload.get("sort_order", 0),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return R(data={"id": s.id, "name": s.name})


@router.get("", response_model=R, summary="排班列表(按日期范围)")
def list_schedules(
    db: DBSession,
    _: CurrentUser,
    start: str = Query(..., description="开始日期 yyyy-mm-dd"),
    end: str | None = Query(None, description="结束日期 yyyy-mm-dd"),
    person_id: int | None = Query(None),
):
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end) if end else d0
    stmt = select(Schedule).where(Schedule.schedule_date >= d0, Schedule.schedule_date <= d1)
    if person_id:
        stmt = stmt.where(Schedule.person_id == person_id)
    rows = db.scalars(stmt.order_by(Schedule.schedule_date, Schedule.person_id))
    persons = {p.id: p.display_name for p in db.scalars(select(Person))}
    shifts = {s.id: s.name for s in db.scalars(select(ShiftTemplate))}
    return R(
        data=[
            {
                "id": s.id,
                "person_id": s.person_id,
                "person_name": persons.get(s.person_id),
                "schedule_date": s.schedule_date.isoformat(),
                "shift_template_id": s.shift_template_id,
                "shift_name": shifts.get(s.shift_template_id),
                "start_time": s.start_time,
                "end_time": s.end_time,
                "status": s.status,
                "note": s.note,
                "source": s.source,
            }
            for s in rows
        ]
    )


@router.post("/bulk", response_model=R, summary="批量排班")
def bulk_schedule(payload: dict, db: DBSession, current_user: ManagerUser):
    """payload: {person_ids: [int], start: 'yyyy-mm-dd', end: 'yyyy-mm-dd', shift_template_id: int, note?}"""
    person_ids = payload.get("person_ids") or []
    start = date.fromisoformat(payload["start"])
    end = date.fromisoformat(payload.get("end", payload["start"]))
    shift_id = payload.get("shift_template_id")
    # 校验人员存在, 避免外键冲突 500(返回无效名单而非崩溃)
    valid_ids = set(
        db.scalars(select(Person.id).where(Person.id.in_(person_ids)))
    ) if person_ids else set()
    invalid_ids = [pid for pid in person_ids if pid not in valid_ids]
    created = 0
    d = start
    while d <= end:
        for pid in person_ids:
            if pid not in valid_ids:
                continue
            existing = db.scalar(
                select(Schedule).where(Schedule.person_id == pid, Schedule.schedule_date == d)
            )
            if existing:
                existing.shift_template_id = shift_id or existing.shift_template_id
                existing.status = payload.get("status", "正常")
                existing.note = payload.get("note")
            else:
                db.add(
                    Schedule(
                        person_id=pid,
                        schedule_date=d,
                        shift_template_id=shift_id,
                        status=payload.get("status", "正常"),
                        note=payload.get("note"),
                        source=payload.get("source", "manual"),
                        created_by=current_user.username,
                    )
                )
                created += 1
        d += timedelta(days=1)
    db.commit()
    return R(
        data={
            "created": created,
            "invalid_ids": invalid_ids,
            "message": f"成功排班 {created} 条" + (f"; 无效人员: {invalid_ids}" if invalid_ids else ""),
        }
    )


@router.put("/{schedule_id}", response_model=R, summary="更新排班(顶班/请假/调班)")
def update_schedule(schedule_id: int, payload: dict, db: DBSession, _: ManagerUser):
    s = db.get(Schedule, schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="排班不存在")
    for k in ("shift_template_id", "start_time", "end_time", "status", "note"):
        if k in payload:
            setattr(s, k, payload[k])
    db.commit()
    return R(message="更新成功")


@router.delete("/{schedule_id}", response_model=R, summary="删除排班")
def delete_schedule(schedule_id: int, db: DBSession, _: ManagerUser):
    s = db.get(Schedule, schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="排班不存在")
    db.delete(s)
    db.commit()
    return R()


# ---------- 考勤打卡(规划设计 §4.1.4) ----------


@router.get("/attendance", response_model=R, summary="考勤记录(按日期范围)")
def list_attendance(
    db: DBSession,
    _: CurrentUser,
    start: str = Query(..., description="开始日期 yyyy-mm-dd"),
    end: str | None = Query(None, description="结束日期 yyyy-mm-dd"),
    person_id: int | None = Query(None),
):
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end) if end else d0
    stmt = select(Attendance).where(Attendance.attendance_date >= d0, Attendance.attendance_date <= d1)
    if person_id:
        stmt = stmt.where(Attendance.person_id == person_id)
    rows = db.scalars(stmt.order_by(Attendance.attendance_date, Attendance.person_id))
    persons = {p.id: p.display_name for p in db.scalars(select(Person))}
    return R(
        data=[
            {
                "id": a.id,
                "person_id": a.person_id,
                "person_name": persons.get(a.person_id),
                "attendance_date": a.attendance_date.isoformat(),
                "check_in_at": a.check_in_at.isoformat() if a.check_in_at else None,
                "check_out_at": a.check_out_at.isoformat() if a.check_out_at else None,
                "source": a.source,
            }
            for a in rows
        ]
    )


@router.post("/attendance", response_model=R, summary="登记考勤(手动/兜底)")
def create_attendance(payload: dict, db: DBSession, _: ManagerUser):
    """payload: {person_id, attendance_date, check_in_at?, check_out_at?, source?, raw?}"""
    pid = int(payload["person_id"])
    if not db.get(Person, pid):
        raise HTTPException(status_code=404, detail="人员不存在")
    d = date.fromisoformat(payload["attendance_date"])
    existing = db.scalar(
        select(Attendance).where(Attendance.person_id == pid, Attendance.attendance_date == d)
    )
    def _parse_dt(s):
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if isinstance(s, str) else s
    if existing:
        if payload.get("check_in_at"):
            existing.check_in_at = _parse_dt(payload["check_in_at"])
        if payload.get("check_out_at"):
            existing.check_out_at = _parse_dt(payload["check_out_at"])
        if payload.get("source"):
            existing.source = payload["source"]
        db.commit()
        db.refresh(existing)
        a = existing
    else:
        a = Attendance(
            person_id=pid,
            attendance_date=d,
            check_in_at=_parse_dt(payload.get("check_in_at")),
            check_out_at=_parse_dt(payload.get("check_out_at")),
            source=payload.get("source", "manual"),
            raw=payload.get("raw"),
        )
        db.add(a)
        db.commit()
        db.refresh(a)
    return R(data={"id": a.id, "person_id": a.person_id, "attendance_date": a.attendance_date.isoformat()})


@router.post("/attendance/bulk", response_model=R, summary="批量导入考勤(Excel/CSV)")
def bulk_attendance(payload: dict, db: DBSession, current_user: ManagerUser):
    """payload: {rows: [{person_id, attendance_date, check_in_at?, check_out_at?}, ...], source?}
    重复按 (person_id, attendance_date) upsert。
    """
    rows = payload.get("rows") or []
    source = payload.get("source", "manual")
    if not rows:
        raise HTTPException(status_code=400, detail="无数据")
    valid_pids = set(db.scalars(select(Person.id).where(Person.id.in_([r.get("person_id") for r in rows if r.get("person_id")]))))
    created, updated, invalid = 0, 0, 0
    for r in rows:
        pid = r.get("person_id")
        if not pid or int(pid) not in valid_pids:
            invalid += 1
            continue
        try:
            d = date.fromisoformat(str(r["attendance_date"])[:10])
        except (KeyError, ValueError):
            invalid += 1
            continue
        def _parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            except ValueError:
                return None
        existing = db.scalar(select(Attendance).where(Attendance.person_id == int(pid), Attendance.attendance_date == d))
        if existing:
            ci = _parse_dt(r.get("check_in_at"))
            co = _parse_dt(r.get("check_out_at"))
            if ci:
                existing.check_in_at = ci
            if co:
                existing.check_out_at = co
            existing.source = source
            updated += 1
        else:
            db.add(Attendance(
                person_id=int(pid),
                attendance_date=d,
                check_in_at=_parse_dt(r.get("check_in_at")),
                check_out_at=_parse_dt(r.get("check_out_at")),
                source=source,
            ))
            created += 1
    db.commit()
    return R(data={"created": created, "updated": updated, "invalid": invalid, "total": len(rows)})


@router.delete("/attendance/{attendance_id}", response_model=R, summary="删除考勤记录")
def delete_attendance(attendance_id: int, db: DBSession, _: ManagerUser):
    a = db.get(Attendance, attendance_id)
    if not a:
        raise HTTPException(status_code=404, detail="考勤记录不存在")
    db.delete(a)
    db.commit()
    return R()


@router.get("/comparison", response_model=R, summary="排班×考勤×接待×工单对照")
def comparison(db: DBSession, _: CurrentUser, day: str = Query(..., description="日期 yyyy-mm-dd")):
    """当日排班 vs 考勤 vs 实际接待/工单对照(四源: 排班/考勤/接待/工单)。"""
    d = date.fromisoformat(day)
    dt_start = datetime.combine(d, time.min, tzinfo=timezone.utc)
    dt_end = datetime.combine(d + timedelta(days=1), time.min, tzinfo=timezone.utc)

    schedules = list(
        db.scalars(select(Schedule).where(Schedule.schedule_date == d).order_by(Schedule.person_id))
    )
    # 当日考勤(按 person_id 索引)
    attendances = {
        a.person_id: a for a in db.scalars(select(Attendance).where(Attendance.attendance_date == d))
    }
    persons = {p.id: p for p in db.scalars(select(Person))}
    # 每个人员对应的所有名字(display_name/qw_name/别名)
    aliases = db.scalars(select(PersonAlias).where(PersonAlias.person_id.in_([s.person_id for s in schedules])))
    name_map: dict[int, set] = {}
    for a in aliases:
        if a.person_id is None:
            continue
        name_map.setdefault(a.person_id, set()).add(a.alias)

    # 当日接待 operator 与 工单 handler
    rec_ops = db.scalars(
        select(_CS.c.operator, func.count())
        .where(_CS.c.operation_time >= dt_start, _CS.c.operation_time < dt_end, _CS.c.operator.isnot(None))
        .group_by(_CS.c.operator)
    )
    op_count = {op: int(c) for op, c in rec_ops}
    tkt_handlers = db.scalars(
        select(ServiceTicket.handler, func.count())
        .where(ServiceTicket.register_time >= dt_start, ServiceTicket.register_time < dt_end, ServiceTicket.handler.isnot(None))
        .group_by(ServiceTicket.handler)
    )
    handler_count = {h: int(c) for h, c in tkt_handlers}

    shifts = {s.id: s for s in db.scalars(select(ShiftTemplate))}

    detail = []
    for s in schedules:
        p = persons.get(s.person_id)
        names = set()
        if p:
            names.add(p.display_name)
            if p.qw_name:
                names.add(p.qw_name)
            names |= name_map.get(p.id, set())
        rec_cnt = sum(c for n, c in op_count.items() if n in names)
        tkt_cnt = sum(c for n, c in handler_count.items() if n in names)
        sh = shifts.get(s.shift_template_id)
        att = attendances.get(s.person_id)
        detail.append(
            {
                "person_id": s.person_id,
                "person_name": p.display_name if p else None,
                "shift_name": sh.name if sh else None,
                "start_time": s.start_time or (sh.start_time if sh else None),
                "end_time": s.end_time or (sh.end_time if sh else None),
                "status": s.status,
                "check_in_at": att.check_in_at.isoformat() if att and att.check_in_at else None,
                "check_out_at": att.check_out_at.isoformat() if att and att.check_out_at else None,
                "is_present": bool(att and (att.check_in_at or att.check_out_at)),
                "reception_count": rec_cnt,
                "ticket_count": tkt_cnt,
            }
        )

    active_reception_persons = set()
    for op in op_count:
        for pid, names in name_map.items():
            if op in names:
                active_reception_persons.add(pid)
                break

    # 仅有考勤但无排班(疑似漏排/顶班)
    present_no_schedule = [
        {"person_id": pid, "person_name": persons.get(pid, Person(display_name="")).display_name if pid in persons else None,
         "check_in_at": a.check_in_at.isoformat() if a.check_in_at else None}
        for pid, a in attendances.items() if pid not in {s.person_id for s in schedules}
    ]
    # 有排班但无考勤(疑似请假/脱岗)
    scheduled_no_present = [
        {"person_id": s.person_id, "person_name": persons.get(s.person_id, Person(display_name="")).display_name if s.person_id in persons else None}
        for s in schedules if s.person_id not in attendances
    ]

    return R(
        data={
            "date": d.isoformat(),
            "scheduled_count": len(schedules),
            "present_count": len(attendances),
            "active_reception_count": len(active_reception_persons),
            "present_no_schedule": present_no_schedule,
            "scheduled_no_present": scheduled_no_present,
            "detail": detail,
        }
    )


@router.post("/import", response_model=R, summary="导入企微排班表(#256 B3-8 镜像)")
def import_wecom_schedule(payload: dict, db: DBSession, current_user: ManagerUser):
    """导入企微排班导出表: 镜像形态(企微源 → 系统展示), source='wecom_export'。

    前置: 文件先经通用上传通道(parseUpload)存档拿 stored_path。
    列约定(表头宽容): 姓名(姓名/人员/名字) + 日期(日期/排班日期) +
    班次(班次/班, 匹配班次模板名); 可选 时间列(HH:MM-HH:MM, 无班次时写入起止时间)。

    冲突判定: schedules 有 uq_schedules_person_date(同人同日唯一)——
    已存在时按 conflict_mode 处理: skip 跳过 / overwrite 覆盖为本导入值。
    覆盖时 source 置为 wecom_export(以企微为准)。

    留痕: upload_records(data_type='schedule', source='manual'), 对齐 B3-9 质量留痕。
    """
    from app.core.storage import resolve_upload_path
    from app.models.upload import UploadRecord
    from app.services.data_parser import load_dataframe

    stored_path = payload.get("stored_path")
    if not stored_path:
        raise HTTPException(status_code=400, detail="stored_path 缺失")
    filename = payload.get("filename") or "schedule.xlsx"
    conflict_mode = payload.get("conflict_mode") or "skip"
    if conflict_mode not in ("skip", "overwrite"):
        raise HTTPException(status_code=400, detail="conflict_mode 仅支持 skip/overwrite")

    path = resolve_upload_path(stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="上传文件不存在,请重新上传")

    record = UploadRecord(
        data_type="schedule",
        filename=filename,
        stored_path=stored_path,
        file_size=path.stat().st_size,
        sheet_name=payload.get("sheet_name"),
        status="parsing",
        uploaded_by=current_user.username,
        source="manual",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    try:
        df = load_dataframe(path, sheet_name=payload.get("sheet_name"), encoding=payload.get("encoding"))
    except Exception as e:
        record.status = "failed"
        record.error_detail = {"errors": [f"文件解析失败: {e}"]}
        record.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=400, detail=f"文件解析失败: {e}") from e

    # 表头宽容匹配: 取第一个命中的列名
    def _col(cands: list[str]) -> str | None:
        for c in cands:
            if c in df.columns:
                return c
        return None

    col_name = _col(["姓名", "人员", "名字", "客服名"])
    col_date = _col(["日期", "排班日期", "排班时间"])
    col_shift = _col(["班次", "班"])
    col_time = _col(["时间", "班段时间", "起止时间"])
    if col_name is None or col_date is None:
        record.status = "failed"
        record.error_detail = {"errors": [f"缺少必填列: 姓名/日期, 实际表头={list(df.columns)[:20]}"]}
        record.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=400, detail="缺少必填列: 姓名/日期")

    # 姓名→Person: display_name 精确 + 别名表(alias 唯一), 一次载入建索引
    persons = {p.display_name.strip(): p for p in db.scalars(select(Person))}
    alias_to_pid: dict[str, int] = {}
    for a in db.scalars(select(PersonAlias).where(PersonAlias.person_id.isnot(None))):
        alias_to_pid[a.alias.strip()] = a.person_id
    shifts_by_name = {s.name.strip(): s for s in db.scalars(select(ShiftTemplate))}

    total = inserted = updated = skipped = failed = 0
    errors: list[str] = []
    # 日期宽容解析(字符串/Excel datetime/Timestamp 统一转 date)
    parsed_dates = pd.to_datetime(df[col_date], errors="coerce")

    for idx, row in df.iterrows():
        total += 1
        line_no = idx + 2  # xlsx 行号(表头占第1行)
        name = str(row[col_name]).strip() if pd.notna(row[col_name]) else ""
        dt = parsed_dates.iloc[idx]
        if not name or pd.isna(dt):
            failed += 1
            errors.append(f"第{line_no}行: 姓名或日期为空")
            continue
        d = dt.date()
        p = persons.get(name)
        pid = p.id if p else alias_to_pid.get(name)
        if pid is None:
            failed += 1
            errors.append(f"第{line_no}行: 人员未匹配 [{name}]")
            continue
        # 班次: 模板名匹配; 无班次列或未匹配时回退可选时间列
        st_id, st_start, st_end = None, None, None
        shift_raw = str(row[col_shift]).strip() if col_shift is not None and pd.notna(row[col_shift]) else ""
        if shift_raw:
            sh = shifts_by_name.get(shift_raw)
            if sh:
                st_id, st_start, st_end = sh.id, sh.start_time, sh.end_time
            else:
                failed += 1
                errors.append(f"第{line_no}行: 班次未匹配 [{shift_raw}]")
                continue
        elif col_time is not None and pd.notna(row[col_time]):
            m = str(row[col_time]).strip()
            parts = m.replace("—", "-").replace("～", "-").split("-")
            if len(parts) == 2 and len(parts[0]) == 5 and len(parts[1]) == 5:
                st_start, st_end = parts[0], parts[1]
            else:
                failed += 1
                errors.append(f"第{line_no}行: 时间格式无法解析 [{m}]")
                continue
        status_raw = "正常"
        existing = db.scalar(
            select(Schedule).where(Schedule.person_id == pid, Schedule.schedule_date == d)
        )
        if existing:
            if conflict_mode == "skip":
                skipped += 1
                continue
            existing.shift_template_id = st_id
            existing.start_time = st_start
            existing.end_time = st_end
            existing.status = status_raw
            existing.source = "wecom_export"
            updated += 1
        else:
            db.add(Schedule(
                person_id=pid, schedule_date=d,
                shift_template_id=st_id, start_time=st_start, end_time=st_end,
                status=status_raw, source="wecom_export", created_by=current_user.username,
            ))
            inserted += 1

    record.total_rows = total
    record.inserted_rows = inserted
    record.updated_rows = updated
    record.failed_rows = failed
    record.status = "imported" if failed == 0 else ("imported" if inserted + updated > 0 else "failed")
    record.error_detail = {"errors": errors[:50]} if errors else None
    record.finished_at = datetime.now(timezone.utc)
    db.commit()
    return R(data={
        "record_id": record.id, "total": total, "inserted": inserted,
        "updated": updated, "skipped": skipped, "failed": failed,
        "errors": errors[:20],
    })
