"""today 主题域子模块 queue (拆分自 today.py #226)

纯物理切片, 不改实现。原 today.py 头部 import 块原样保留,
函数体内惰性 import 随函数体原样保留。
"""
# ruff: noqa: F401, I001  # 拆分自原 today.py(#226), 保留原 import 顺序避免漂移
"""今日看板: today_kpi / 小时趋势 / 接待拆分 / 退款拆分 / 队列 / 员工效率。"""

import datetime as _dt
import logging
from decimal import Decimal
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select, text, union
from sqlalchemy.orm import Session

from app.models.ods_clean import CardComplaintClean
from app.models.ods_clean import CsRecordClean
from app.models.ods_clean import ExternalComplaintClean
from app.models.group import Group, PersonGroupMembership
from app.models.incident import Incident
from app.models.person import Person, PersonAlias
from app.models.refund_standard import StdRefundOnline  # #332: 退款统计切标准表(预留 import)
from app.models.refund_standard import StdRefundRecord  # #332: E 路切标准表(预留 import)
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.task import SpecialTask
from app.models.ticket import ServiceTicket
from app.models.todo import TodoItem
from app.models.refund_standard import StdWalletFlow  # #332: B 路切标准表(预留 import)
from app.services.complaint_scope import (
    ALL_BUCKETS,
    BUCKET_CARD,
    BUCKET_EXTERNAL,
    BUCKET_INTERNAL,
    # ⚠️ #53: BUCKET_OTHER 已从来源桶移除(四类完备)。保留导入仅供历史数据识别,
    #    若确认无处使用可删除。
    BUCKET_OTHER,  # noqa: F401
    CARD_SCOPE,
)
from app.services.system_accounts import SourceKind  # noqa: I001
from app.services.system_accounts import is_virtual_name as _sa_is_virtual_name

logger = logging.getLogger(__name__)

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    _EXTERNAL_DONE_RESULTS_LOCAL,
    _EXTERNAL_ITEM_COMPLAINT_KW,
    _INCLUDE_COMPLAINT_FILES,
    COMPLAINT_SPECIALTY,
    COMPLAINT_SPECIALTY_WEIGHT,
    D,
    TZ,
    _complaint_source_type_case,
    _complaint_subitem_case,
    _derive_service_item_for_refund,
    _parse_date,
    _pct_delta,
    _resolve_windows,
    _route_a_online_refund_filter,
    _route_b_withdraw_filter,
    _route_e_cashflow_filter,
    _strip_qw_prefix,
    _ticket_category_case,
)
from app.services.analytics.cash import (
    _cash_routes_total,
    _route_c_shanmao_trend,
    _route_d_other_trend,
)
from app.services.analytics.complaint import _ticket_effective_finish_time_where
from app.services.analytics.reception import (
    _category_case,
    _merge_person_rows,
    _operator_to_person_cte,
)


def _recurring_matches_today(pattern: str | None, valid_from, target_date) -> bool:
    """判断周期性事件在 target_date 当日是否生效.
    pattern: daily(每日) / weekly(同周几) / weekday(工作日) / monthly(同日)
    valid_from: 周期起始(用于确定 周几/几号 基准). 无 pattern 默认按 daily.
    """
    if pattern is None or pattern == "" or pattern == "daily":
        return True
    vf = valid_from
    if vf is None:
        return True
    # 统一取 weekday(0=Mon..6=Sun) 与 day(月内日)
    try:
        vf_wd = vf.weekday() if hasattr(vf, "weekday") else None
        vf_day = vf.day if hasattr(vf, "day") else None
        tgt_wd = target_date.weekday() if hasattr(target_date, "weekday") else None
        tgt_day = target_date.day if hasattr(target_date, "day") else None
    except Exception:
        return True
    if pattern == "weekly":
        return vf_wd is not None and tgt_wd is not None and vf_wd == tgt_wd
    if pattern == "weekday":
        return tgt_wd is not None and tgt_wd < 5  # 周一~周五
    if pattern == "monthly":
        return vf_day is not None and tgt_day is not None and vf_day == tgt_day
    return True



def today_queue(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块5: 今日处理队列 = 今日截止待办 + 进行中专项 + 今日异常事件.
    每项最多10条, 点击可跳转详情页.
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None
    today_start = ts
    today_end = te
    # 待办 status: pending/processing/confirming 是未完成, done 已完成
    not_done = TodoItem.status.notin_(["done"])
    todo_rows = db.execute(
        select(TodoItem.id, TodoItem.title, TodoItem.priority, TodoItem.status,
               TodoItem.deadline, TodoItem.related_refund_no)
        .where(TodoItem.deadline.isnot(None),
               TodoItem.deadline >= today_start, TodoItem.deadline <= today_end,
               not_done)
        .order_by(case(
            (TodoItem.priority == "urgent", 0),
            (TodoItem.priority == "high", 1),
            (TodoItem.priority == "medium", 2),
            (TodoItem.priority == "low", 3),
            else_=4
        ), TodoItem.deadline.asc())
        .limit(10)
    ).all()
    todos = [{
        "id": r.id, "title": r.title, "priority": r.priority, "status": r.status,
        "deadline": r.deadline.isoformat() if r.deadline else None,
        "related_refund_no": r.related_refund_no,
    } for r in todo_rows]

    # 2. 进行中专项: status in (pending_start/executing/following) + deadline在7天内或无deadline
    active_status = ["pending_start", "executing", "following"]
    week_later = today_end + timedelta(days=7)
    task_rows = db.execute(
        select(SpecialTask.id, SpecialTask.title, SpecialTask.status,
               SpecialTask.task_type, SpecialTask.deadline)
        .where(SpecialTask.status.in_(active_status))
        .order_by(case(
            (SpecialTask.deadline.is_(None), 1),
            (SpecialTask.deadline <= week_later, 0),
            else_=2
        ), SpecialTask.deadline.asc().nullslast())
        .limit(10)
    ).all()
    tasks = [{
        "id": r.id, "title": r.title, "status": r.status, "type": r.task_type,
        "deadline": r.deadline.isoformat() if r.deadline else None,
    } for r in task_rows]

    # 3. 今日影响事件:
    #    (a) 未闭环(is_closed=False)的非周期事件 持续每天显示, 直到闭环 —— "状态不注明就持续影响每天"
    #    (b) 当日新发生/登记的非周期事件
    #    (c) 周期性事件(is_recurring=True): 在 [valid_from, valid_to] 生效区间内 + 周期模式命中当日
    #        用于广告推送/定期维护等阶段性影响, 每日生效时明确显示
    #    排序: 周期性/未闭环在前(按发生时间倒序), 已闭环的当日事件在后
    inc_between = or_(
        and_(Incident.occurred_at.isnot(None), Incident.occurred_at >= today_start, Incident.occurred_at <= today_end),
        and_(Incident.created_at.isnot(None), Incident.created_at >= today_start, Incident.created_at <= today_end),
    )
    # 非周期事件: 未闭环 或 当日发生
    # #294: 业务看板只展示业务事件, 排除系统层采集告警(category='数据采集',
    # 由数据质量监控/熔断自动登记)。该类事件属运维告警, 在「管理-异常事件」
    # 台账仍完整可见; 口径与 incidents 路由 markers/timeline 保持一致。
    # is_distinct_from 兼容历史 category=NULL 的事件(NULL 视为非采集类, 正常展示)。
    non_recur_rows = db.execute(
        select(Incident.id, Incident.title, Incident.status, Incident.subject,
               Incident.occurred_at, Incident.duration_minutes, Incident.is_closed,
               Incident.severity, Incident.is_recurring, Incident.recurrence_pattern)
        .where(Incident.is_recurring == False,  # noqa: E712
               Incident.category.is_distinct_from("数据采集"),
               or_(Incident.is_closed == False, inc_between))  # noqa: E712
        .order_by(Incident.is_closed.asc(), Incident.occurred_at.desc().nullslast(), Incident.created_at.desc())
        .limit(10)
    ).all()
    seen_ids = {r.id for r in non_recur_rows}
    incidents = [{
        "id": r.id, "title": r.title, "status": r.status, "subject": r.subject,
        "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
        "duration_minutes": r.duration_minutes,
        "is_closed": bool(r.is_closed),
        "severity": r.severity,
        "is_recurring": False, "recurrence_pattern": None,
    } for r in non_recur_rows]

    # 周期性事件: 生效区间覆盖当日 + 周期模式命中
    recur_rows = db.execute(
        select(Incident.id, Incident.title, Incident.status, Incident.subject,
               Incident.occurred_at, Incident.duration_minutes, Incident.is_closed,
               Incident.severity, Incident.is_recurring, Incident.recurrence_pattern,
               Incident.valid_from, Incident.valid_to)
        .where(
            Incident.is_recurring == True,  # noqa: E712
            # #294: 与非周期查询同口径, 业务看板排除系统层采集告警
            Incident.category.is_distinct_from("数据采集"),
            Incident.valid_from.isnot(None),
            Incident.valid_from <= today_end,
            or_(Incident.valid_to.is_(None), Incident.valid_to >= today_start),
        )
        .order_by(Incident.occurred_at.desc().nullslast())
        .limit(30)
    ).all()
    # 当日目标日期(本地时区)用于周期模式命中判定
    target_date = today_start.astimezone(TZ).date() if today_start.tzinfo else today_start.date()
    for r in recur_rows:
        if r.id in seen_ids:
            continue
        if not _recurring_matches_today(r.recurrence_pattern, r.valid_from, target_date):
            continue
        seen_ids.add(r.id)
        incidents.append({
            "id": r.id, "title": r.title, "status": r.status, "subject": r.subject,
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "duration_minutes": r.duration_minutes,
            "is_closed": bool(r.is_closed),
            "severity": r.severity,
            "is_recurring": True, "recurrence_pattern": r.recurrence_pattern,
        })
    # 周期性在前, 其次未闭环, 最后已闭环当日
    def _inc_sort_key(inc):
        return (
            0 if inc["is_recurring"] else (1 if not inc["is_closed"] else 2),
            inc["occurred_at"] or "",
        )
    incidents.sort(key=_inc_sort_key, reverse=True)
    incidents = incidents[:10]

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "todos": todos,
        "tasks": tasks,
        "incidents": incidents,
    }

