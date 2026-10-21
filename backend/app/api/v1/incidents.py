"""异常事件登记路由 - 未通知的非预期事项,造成客服负荷激增

管理层(AdminOrSupervisor)登记与维护;普通用户可查看(知情)。
转化链路:异常发生→登记→复盘→转预期事项→下次提前部署。
铁律:用于改进不用于问责。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.incident import (
    Incident,
    IncidentEventType,
    IncidentFollowup,
    IncidentStatus,
)
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/incidents", tags=["异常事件登记"])


def _serialize(i: Incident) -> dict:
    return {
        "id": i.id,
        "title": i.title,
        "subject": i.subject,
        "detail": i.detail,
        "registrar_id": i.registrar_id,
        "impact": i.impact,
        "occurred_at": i.occurred_at.isoformat() if i.occurred_at else None,
        "duration_minutes": i.duration_minutes,
        "related_tickets": i.related_tickets,
        "status": i.status,
        "converted_to_expected": i.converted_to_expected,
        "review_note": i.review_note,
        "category": i.category,
        "department": i.department,
        "severity": i.severity,
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "closed_at": i.closed_at.isoformat() if i.closed_at else None,
        "is_closed": i.is_closed,
        "is_recurring": i.is_recurring,
        "recurrence_pattern": i.recurrence_pattern,
        "valid_from": i.valid_from.isoformat() if i.valid_from else None,
        "valid_to": i.valid_to.isoformat() if i.valid_to else None,
        # #232 B1.5-1 经营事件扩展字段
        "event_type": i.event_type,
        "target_audience": i.target_audience,
        "start_at": i.start_at.isoformat() if i.start_at else None,
        "end_at": i.end_at.isoformat() if i.end_at else None,
        "impact_metrics": i.impact_metrics,
        # #235 B1.5-4 排班调整建议(经营事件用, 可空)
        "scheduling_suggestion": i.scheduling_suggestion,
        "created_by": i.created_by,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }


@router.get("", response_model=R[PageBase], summary="异常事件列表")
def list_incidents(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="状态: registered/processing/calmed/reviewed"),
    event_type: str | None = Query(None, description="事件类型: 经营事件/异常事故"),
    category: str | None = Query(None, description="来源/类型:生产/运营/商务部/店铺/系统平台开发/其他服务商"),
    department: str | None = Query(None, description="涉及部门"),
    start: str | None = Query(None, description="发生时间起 yyyy-mm-dd"),
    end: str | None = Query(None, description="发生时间止 yyyy-mm-dd"),
):
    stmt = select(Incident)
    if status:
        try:
            stmt = stmt.where(Incident.status == IncidentStatus(status).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态值: {status}")
    if event_type:
        # 校验 event_type 取值合法(经营事件/异常事故), 避免传入非法字符串
        try:
            stmt = stmt.where(Incident.event_type == IncidentEventType(event_type).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的事件类型: {event_type}")
    if category:
        stmt = stmt.where(Incident.category == category)
    if department:
        stmt = stmt.where(Incident.department == department)
    if start:
        stmt = stmt.where(Incident.occurred_at >= datetime.fromisoformat(start))
    if end:
        stmt = stmt.where(Incident.occurred_at <= datetime.fromisoformat(end))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(Incident.occurred_at.desc().nulls_last(), Incident.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[_serialize(i) for i in items], total=total or 0, page=page, page_size=page_size))


@router.get("/markers/timeline", response_model=R, summary="时间轴事件标记 (#233)")
def list_timeline_markers(
    db: DBSession,
    current_user: CurrentUser,
    start: str = Query(..., description="查询起始 yyyy-mm-dd(含)"),
    end: str = Query(..., description="查询结束 yyyy-mm-dd(含)"),
    event_type: str | None = Query(None, description="可选过滤: 经营事件/异常事故"),
):
    """返回时间窗口内的事件标记数据 (供 ECharts markPoint/markLine 消费)。

    #233 B1.5-2 看板时间轴标注层:
    - 点事件(异常事故 / start_at 空的经营事件) → 前端 markPoint
    - 区间事件(start_at + end_at 都有的经营事件) → 前端 markLine
    - 推送给前端: id/title/event_type/occurred_at/start_at/end_at/severity 等
    - 周期性事件 (is_recurring=True) 也包含, 由前端按 valid_from~valid_to 展开

    时间窗口以 occurred_at 或 start_at 落在 [start, end] 为准; 若都没填则
    用 created_at 兜底(避免漏登记后未补时间的事件)。

    路由顺序: 必须放在 `/{incident_id}` 之前, 否则会被参数路由抢先匹配
    (FastAPI 按声明顺序匹配, "markers" 会被识别为 incident_id 字符串导致 422)。
    """
    try:
        s_dt = datetime.fromisoformat(start)
        e_dt = datetime.fromisoformat(end)
        # end 含当天 → 加 1 天 - 1 秒, 覆盖到当天 23:59:59
        from datetime import timedelta
        e_dt = e_dt + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="start/end 需为 yyyy-mm-dd 格式")

    # 取窗口内所有事件; 不用 occurred_at 单列过滤 (经营事件可能 occurred_at
    # 为空, 改用 OR 兜底 start_at/created_at 防漏)
    from sqlalchemy import or_
    stmt = select(Incident).where(
        or_(
            Incident.occurred_at.between(s_dt, e_dt),
            Incident.start_at.between(s_dt, e_dt),
            Incident.end_at.between(s_dt, e_dt),
            # 兜底: 没填时间的事件用 created_at 看是否在窗口内
            Incident.occurred_at.is_(None) & Incident.start_at.is_(None)
            & Incident.created_at.between(s_dt, e_dt),
        )
    )
    if event_type:
        try:
            stmt = stmt.where(Incident.event_type == IncidentEventType(event_type).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的事件类型: {event_type}")

    # 时序轴只标注业务异常/业务事件, 排除系统层故障(如数据采集/同步异常)。
    # 数据采集异常由数据质量监控自动登记(category='数据采集'), 属系统告警而非业务事件,
    # 标注在经营时序轴上会干扰业务趋势判断; 用 is_distinct_from 兼容 NULL category。
    stmt = stmt.where(Incident.category.is_distinct_from('数据采集'))

    rows = list(db.scalars(stmt.order_by(Incident.occurred_at.asc().nulls_last(),
                                         Incident.start_at.asc().nulls_last(),
                                         Incident.id.asc())))
    # 返回精简字段集(前端构造 markPoint/markLine 用), 详情字段一并给便于直接弹窗
    return R(data=[
        {
            "id": i.id,
            "title": i.title,
            "event_type": i.event_type,
            "occurred_at": i.occurred_at.isoformat() if i.occurred_at else None,
            "start_at": i.start_at.isoformat() if i.start_at else None,
            "end_at": i.end_at.isoformat() if i.end_at else None,
            "severity": i.severity,
            "category": i.category,
            "subject": i.subject,
            "detail": i.detail,
            "impact": i.impact,
            "target_audience": i.target_audience,
            "impact_metrics": i.impact_metrics,
            "scheduling_suggestion": i.scheduling_suggestion,
            "is_recurring": i.is_recurring,
            "recurrence_pattern": i.recurrence_pattern,
            "valid_from": i.valid_from.isoformat() if i.valid_from else None,
            "valid_to": i.valid_to.isoformat() if i.valid_to else None,
            "status": i.status,
            "is_closed": i.is_closed,
        }
        for i in rows
    ])


@router.get("/{incident_id}", response_model=R, summary="异常事件详情")
def get_incident(incident_id: int, db: DBSession, current_user: CurrentUser):
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    return R(data=_serialize(i))


@router.get("/{incident_id}/impact-comparison", response_model=R,
            summary="事件影响对照 (#234 B1.5-3)")
def get_incident_impact_comparison(
    incident_id: int,
    db: DBSession,
    current_user: CurrentUser,
    window_minutes: int = Query(60, ge=5, le=720,
        description="事件前/后窗口分钟数(默认 60 分钟)"),
):
    """事件影响对照: 比较事件前后时间窗口的接待/投诉/退款量变化。

    #234 B1.5-3 视觉对照 "推文后接待量是否升、事故后投诉量是否升":
    - 点事件(异常事故 / 经营事件无 start_at): 锚点=occurred_at
        pre_window = [anchor - window_minutes, anchor]
        post_window = [anchor, anchor + window_minutes]
    - 区间事件(经营事件有 start_at+end_at): 锚点=start_at, 终点=end_at
        pre_window = [start_at - window_minutes, start_at]
        during_window = [start_at, end_at]
        post_window = [end_at, end_at + window_minutes]
    - 退化为同窗口无锚点(occurred_at/start_at 都空): 返回 400 提示

    统计字段:
      reception  = cs_records.operation_time 落在窗口内的行数
      complaint  = service_tickets.created_at 落在窗口内的行数
      refund_count = refunds.actual_refund_time (回退 created_at_raw) 落在窗口内的行数
      refund_amount = 上述 refunds 的 refund_amount 合计 (元, Decimal→float 出参边界)

    时区: 全部按 AT TIME ZONE 'Asia/Shanghai' 落库时间已带 tz; 比较时
    datetime 对象需 tz-aware, 故统一用 timezone.utc 做 anchor 转换。
    """
    from datetime import timedelta, timezone

    from sqlalchemy import and_
    from sqlalchemy import column as _column
    from sqlalchemy import func as _func
    from sqlalchemy import table as _table

    from app.models.refund_standard import StdRefundOnline
    from app.models.ticket import ServiceTicket

    # #338: 接待量统计 → 读 std_cs_record_all 镜像
    _CS_ALL = _table("std_cs_record", _column("operation_time"))

    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")

    # 确定锚点 + 终点(区间事件)
    anchor = i.occurred_at or i.start_at
    end_anchor = i.end_at if (i.event_type == "经营事件" and i.start_at and i.end_at) else None
    if anchor is None:
        raise HTTPException(
            status_code=400,
            detail="事件缺少 occurred_at / start_at, 无法做前后对照",
        )

    # 统一转 tz-aware (避免 naive 与 aware 比较报错)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    if end_anchor and end_anchor.tzinfo is None:
        end_anchor = end_anchor.replace(tzinfo=timezone.utc)

    pre_start = anchor - timedelta(minutes=window_minutes)
    pre_end = anchor
    if end_anchor:
        during_start = anchor
        during_end = end_anchor
        post_start = end_anchor
        post_end = end_anchor + timedelta(minutes=window_minutes)
    else:
        during_start = during_end = None
        post_start = anchor
        post_end = anchor + timedelta(minutes=window_minutes)

    def _stats(s, e):
        # 接待量(cs_records.operation_time)
        reception = db.scalar(
            select(_func.count()).select_from(_CS_ALL)
            .where(and_(_CS_ALL.c.operation_time.isnot(None),
                        _CS_ALL.c.operation_time >= s, _CS_ALL.c.operation_time <= e))
        ) or 0
        # 投诉量(service_tickets.created_at, 来自 TimestampMixin)
        complaint = db.scalar(
            select(_func.count()).select_from(ServiceTicket)
            .where(and_(ServiceTicket.created_at.isnot(None),
                        ServiceTicket.created_at >= s, ServiceTicket.created_at <= e))
        ) or 0
        # 退款笔数/金额(refunds.actual_refund_time, COALESCE 退化 created_at_raw)
        # 业务口径: actual_refund_time 是"实际扣款时间"(对账基准), E 滞后未回填时
        # 退化用 created_at_raw; 在 SQL 用 COALESCE 实现, 单次扫描即可。
        # #338: 已退款统计 → 读 std_refund_online 采纳行
        refund_time_col = _func.coalesce(StdRefundOnline.actual_refund_time, StdRefundOnline.created_at_raw)
        refund_row = db.execute(
            select(_func.count(), _func.coalesce(_func.sum(StdRefundOnline.refund_amount), 0))
            .where(and_(refund_time_col.isnot(None),
                        refund_time_col >= s, refund_time_col <= e))
        ).first()
        refund_count = int(refund_row[0] or 0) if refund_row else 0
        # 金额出参边界按 #217 C′ 方案: Decimal → float 一次性表示层转换
        refund_amount = float(refund_row[1] or 0) if refund_row else 0.0
        return {
            "start": s.isoformat(),
            "end": e.isoformat(),
            "reception": int(reception),
            "complaint": int(complaint),
            "refund_count": refund_count,
            "refund_amount": round(refund_amount, 2),
        }

    pre = _stats(pre_start, pre_end)
    post = _stats(post_start, post_end)
    during = _stats(during_start, during_end) if during_start else None

    def _delta(a, b):
        # a → b 的增量(允许负)
        return {k: (b.get(k, 0) - a.get(k, 0)) for k in ("reception", "complaint", "refund_count", "refund_amount")}

    return R(data={
        "incident_id": i.id,
        "title": i.title,
        "event_type": i.event_type,
        "anchor_at": anchor.isoformat(),
        "end_at": (end_anchor.isoformat() if end_anchor else None),
        "window_minutes": window_minutes,
        "pre_window": pre,
        "during_window": during,
        "post_window": post,
        # pre → post 增量(区间事件时, pre→during 或 during→post 可由前端二次算)
        "delta_pre_to_post": _delta(pre, post),
    })


@router.post("", response_model=R, summary="登记异常事件")
def create_incident(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    # #232 B1.5-1: event_type 默认「异常事故」(向后兼容); 经营事件需显式传入
    event_type = IncidentEventType(payload.get("event_type", "异常事故")).value
    i = Incident(
        title=payload["title"],
        subject=payload.get("subject"),
        detail=payload.get("detail"),
        registrar_id=payload.get("registrar_id") or current_user.id,
        impact=payload.get("impact"),
        occurred_at=datetime.fromisoformat(payload["occurred_at"]) if payload.get("occurred_at") else None,
        duration_minutes=payload.get("duration_minutes"),
        related_tickets=payload.get("related_tickets"),
        status=IncidentStatus(payload.get("status", "registered")).value,
        category=payload.get("category"),
        department=payload.get("department"),
        severity=payload.get("severity", "中"),
        is_recurring=bool(payload.get("is_recurring", False)),
        recurrence_pattern=payload.get("recurrence_pattern"),
        valid_from=datetime.fromisoformat(payload["valid_from"]) if payload.get("valid_from") else None,
        valid_to=datetime.fromisoformat(payload["valid_to"]) if payload.get("valid_to") else None,
        # #232 B1.5-1 经营事件扩展字段
        event_type=event_type,
        target_audience=payload.get("target_audience"),
        start_at=datetime.fromisoformat(payload["start_at"]) if payload.get("start_at") else None,
        end_at=datetime.fromisoformat(payload["end_at"]) if payload.get("end_at") else None,
        impact_metrics=payload.get("impact_metrics"),
        # #235 B1.5-4 排班调整建议(经营事件用, 可空)
        scheduling_suggestion=payload.get("scheduling_suggestion"),
        created_by=current_user.username,
    )
    db.add(i)
    db.commit()
    db.refresh(i)
    # 通知管理员/主管(异常登记知情)
    try:
        from app.services.notifier import notify_all_admins_and_supervisors
        notify_all_admins_and_supervisors(
            db,
            type="incident",
            title=f"异常事故登记：{i.title}",
            content=payload.get("detail") or "",
            ref_type="incident",
            ref_id=str(i.id),
        )
        db.commit()
    except Exception:
        db.rollback()
    return R(data=_serialize(i))


@router.put("/{incident_id}", response_model=R, summary="更新异常事件")
def update_incident(incident_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    for k in ("title", "subject", "detail", "impact", "duration_minutes", "related_tickets", "review_note",
              "category", "department", "severity", "recurrence_pattern",
              "target_audience", "impact_metrics", "scheduling_suggestion"):
        if k in payload:
            setattr(i, k, payload[k])
    # #232 B1.5-1 event_type 校验后赋值(避免传入非法字符串)
    if "event_type" in payload:
        i.event_type = IncidentEventType(payload["event_type"]).value
    if "occurred_at" in payload:
        i.occurred_at = datetime.fromisoformat(payload["occurred_at"]) if payload["occurred_at"] else None
    if "resolved_at" in payload:
        i.resolved_at = datetime.fromisoformat(payload["resolved_at"]) if payload["resolved_at"] else None
    if "closed_at" in payload:
        i.closed_at = datetime.fromisoformat(payload["closed_at"]) if payload["closed_at"] else None
    if "valid_from" in payload:
        i.valid_from = datetime.fromisoformat(payload["valid_from"]) if payload["valid_from"] else None
    if "valid_to" in payload:
        i.valid_to = datetime.fromisoformat(payload["valid_to"]) if payload["valid_to"] else None
    # #232 B1.5-1 经营事件起止时间
    if "start_at" in payload:
        i.start_at = datetime.fromisoformat(payload["start_at"]) if payload["start_at"] else None
    if "end_at" in payload:
        i.end_at = datetime.fromisoformat(payload["end_at"]) if payload["end_at"] else None
    if "is_recurring" in payload:
        i.is_recurring = bool(payload["is_recurring"])
    if "is_closed" in payload:
        i.is_closed = bool(payload["is_closed"])
        if i.is_closed and not i.closed_at:
            i.closed_at = datetime.now(timezone.utc)
    if "status" in payload:
        i.status = IncidentStatus(payload["status"]).value
    if "converted_to_expected" in payload:
        i.converted_to_expected = bool(payload["converted_to_expected"])
    db.commit()
    db.refresh(i)
    return R(data=_serialize(i))


@router.post("/{incident_id}/convert", response_model=R, summary="转为预期事项")
def convert_incident(incident_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    """复盘后转为预期事项(纳入提前部署):标记 converted_to_expected 并置为已复盘。"""
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    i.converted_to_expected = True
    i.status = IncidentStatus.REVIEWED.value
    if payload.get("review_note"):
        i.review_note = payload["review_note"]
    db.commit()
    db.refresh(i)
    return R(data=_serialize(i))


@router.delete("/{incident_id}", response_model=R, summary="删除异常事件")
def delete_incident(incident_id: int, db: DBSession, current_user: AdminOrSupervisor):
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    db.delete(i)
    db.commit()
    return R()


# ---------- 跟进时间线(规划设计 §4.2.2) ----------


@router.get("/{incident_id}/followups", response_model=R, summary="异常事件跟进记录")
def list_followups(incident_id: int, db: DBSession, current_user: CurrentUser):
    rows = db.scalars(
        select(IncidentFollowup)
        .where(IncidentFollowup.incident_id == incident_id)
        .order_by(IncidentFollowup.id.asc())
    )
    return R(
        data=[
            {
                "id": f.id,
                "incident_id": f.incident_id,
                "author_id": f.author_id,
                "content": f.content,
                "status_change": f.status_change,
                "created_by": f.created_by,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in rows
        ]
    )


@router.post("/{incident_id}/followups", response_model=R, summary="新增跟进记录")
def add_followup(incident_id: int, payload: dict, db: DBSession, current_user: CurrentUser):
    i = db.get(Incident, incident_id)
    if not i:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    f = IncidentFollowup(
        incident_id=incident_id,
        author_id=current_user.id,
        content=payload["content"],
        status_change=payload.get("status_change"),
        created_by=current_user.username,
    )
    db.add(f)
    # 若提供了状态变更, 同步更新异常状态
    if payload.get("status") and payload["status"] in {s.value for s in IncidentStatus}:
        i.status = payload["status"]
    if payload.get("is_closed"):
        i.is_closed = True
        if not i.closed_at:
            i.closed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(f)
    return R(
        data={
            "id": f.id,
            "content": f.content,
            "status_change": f.status_change,
            "created_by": f.created_by,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
    )
