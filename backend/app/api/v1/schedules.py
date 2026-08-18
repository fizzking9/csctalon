"""排班路由(规划设计 §4.1)

排班源头为企业微信(对接同步), 系统内做展示 + 顶班/请假微调 + 与接待/工单关联对照。
"""

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession, ManagerUser
from app.models.cs_record import CsRecord
from app.models.person import Person, PersonAlias
from app.models.schedule import Attendance, Schedule, ShiftTemplate
from app.models.ticket import ServiceTicket
from app.schemas.common import PageBase, R

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


@router.get("/comparison", response_model=R, summary="排班×接待×工单对照")
def comparison(db: DBSession, _: CurrentUser, day: str = Query(..., description="日期 yyyy-mm-dd")):
    """当日排班 vs 实际接待/工单对照(四源: 排班/考勤/接待/工单)。"""
    d = date.fromisoformat(day)
    dt_start = datetime.combine(d, time.min, tzinfo=timezone.utc)
    dt_end = datetime.combine(d + timedelta(days=1), time.min, tzinfo=timezone.utc)

    schedules = list(
        db.scalars(select(Schedule).where(Schedule.schedule_date == d).order_by(Schedule.person_id))
    )
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
        select(CsRecord.operator, func.count())
        .where(CsRecord.operation_time >= dt_start, CsRecord.operation_time < dt_end, CsRecord.operator.isnot(None))
        .group_by(CsRecord.operator)
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
        detail.append(
            {
                "person_id": s.person_id,
                "person_name": p.display_name if p else None,
                "shift_name": sh.name if sh else None,
                "start_time": s.start_time or (sh.start_time if sh else None),
                "end_time": s.end_time or (sh.end_time if sh else None),
                "status": s.status,
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

    return R(
        data={
            "date": d.isoformat(),
            "scheduled_count": len(schedules),
            "active_reception_count": len(active_reception_persons),
            "detail": detail,
        }
    )
