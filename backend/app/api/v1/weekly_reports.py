"""周报看板路由 - 10项标准框架, 复用 Report 的快照/审计机制

周报看板工作流(用户确认):
1. 一键生成本周周报看板(自动周次名称 + 从绑定配置填充 view_ids/owner_id)
2. 主管/组长在对应分项备注摘要(草稿态可编辑)
3. 周会评审: 分析确认 + 记录会议结论(meeting_conclusion)
4. 会议结束固化归档(confirm 固化各 section.data 快照)
5. 后续跟进注释(ReportComment, 统一注释 API)
6. 专项下钻: 对某分项有异议 → 一键创建专项报告(指派负责人) → 回链 related_report_ids
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.api.v1.report_templates import instantiate_framework
from app.models.report import Report, ReportStatus, ReportType
from app.models.report_template import ReportTemplate
from app.models.weekly_report import (
    WEEKLY_FRAMEWORK_SECTIONS,
    WeeklyReport,
    WeeklyReportStatus,
    default_framework,
)
from app.models.weekly_section_binding import WeeklySectionBinding
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/weekly-reports", tags=["周报看板"])


# ---------- 序列化与规范化 ----------


def _serialize(w: WeeklyReport) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "week_start": w.week_start.isoformat() if w.week_start else None,
        "week_end": w.week_end.isoformat() if w.week_end else None,
        "framework": w.framework,
        "summary": w.summary,
        "meeting_conclusion": w.meeting_conclusion,
        "status": w.status,
        "confirmed_at": w.confirmed_at.isoformat() if w.confirmed_at else None,
        "confirmed_by": w.confirmed_by,
        "created_by": w.created_by,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }


def _normalize_framework(framework: list) -> list:
    """规范化框架: 保留用户自定义 section 与顺序, 仅做字段补齐与脏数据清洗。
    不再强制对齐 10 项固定框架(支持可编辑看板)。
    兼容旧格式 view_id(单值)→view_ids(列表), related_report_id(单值)→列表。"""
    result = []
    seen_keys = set()
    for sec in framework or []:
        if not isinstance(sec, dict) or not sec.get("key"):
            continue
        key = sec["key"]
        if key in seen_keys:
            continue  # 去重
        seen_keys.add(key)
        view_ids = sec.get("view_ids")
        if view_ids is None and sec.get("view_id") is not None:
            view_ids = [sec["view_id"]]
        related_ids = sec.get("related_report_ids")
        if related_ids is None and sec.get("related_report_id") is not None:
            related_ids = [sec["related_report_id"]]
        result.append(
            {
                "key": key,
                "title": sec.get("title") or key,
                "section_type": sec.get("section_type") or "view",
                "view_ids": list(view_ids or []),
                "owner_id": sec.get("owner_id"),
                "summary": sec.get("summary") or "",
                "data": sec.get("data"),
                "related_report_ids": list(related_ids or []),
            }
        )
    return result


def _serialize_binding(b: WeeklySectionBinding) -> dict:
    return {
        "id": b.id,
        "section_key": b.section_key,
        "view_ids": b.view_ids or [],
        "owner_id": b.owner_id,
        "is_active": b.is_active,
        "created_by": b.created_by,
    }


def _get_default_template(db, report_type: str) -> ReportTemplate | None:
    """取指定类型的默认模板(无默认则取该类型第一个 active 模板)。"""
    t = db.scalar(
        select(ReportTemplate).where(
            ReportTemplate.report_type == report_type,
            ReportTemplate.is_default.is_(True),
            ReportTemplate.status == "active",
        )
    )
    if not t:
        t = db.scalar(
            select(ReportTemplate).where(
                ReportTemplate.report_type == report_type,
                ReportTemplate.status == "active",
            ).order_by(ReportTemplate.sort_order, ReportTemplate.id)
        )
    return t


# ---------- 周次计算 ----------


def _iso_week_range(dt: datetime | None = None) -> tuple[datetime, datetime, str]:
    """返回给定日期所在 ISO 周的 (周一0点, 周日23:59:59, 'YYYY-Www')"""
    dt = dt or datetime.now()
    iso = dt.isocalendar()  # (year, week, weekday)
    # 周一 = 当前日期 - (weekday-1) 天
    monday = dt - timedelta(days=iso.weekday - 1)
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
    week_label = f"{iso.year}-W{iso.week:02d}"
    return monday, sunday, week_label


# ---------- 框架模板 ----------


@router.get("/framework", response_model=R, summary="周报框架模板(取默认周报模板)")
def get_framework(db: DBSession, current_user: CurrentUser):
    """返回默认周报模板的 section 定义(供前端表单渲染与校验)。
    无模板时退回硬编码 10 项标准框架。"""
    t = _get_default_template(db, "weekly")
    if t and t.framework:
        # 返回模板侧结构(无 data/summary)
        sections = []
        for sec in t.framework:
            sections.append(
                {
                    "key": sec.get("key"),
                    "title": sec.get("title"),
                    "section_type": sec.get("section_type") or "view",
                    "view_ids": sec.get("view_ids") or [],
                    "owner_id": sec.get("owner_id"),
                    "summary_placeholder": sec.get("summary_placeholder") or "",
                }
            )
        return R(data=sections)
    return R(data=WEEKLY_FRAMEWORK_SECTIONS)


# ---------- 绑定配置(deprecated 代理: 读写默认周报模板的 section) ----------


@router.get("/bindings", response_model=R, summary="周报分项默认绑定配置(代理默认模板)")
def list_bindings(db: DBSession, current_user: CurrentUser):
    """返回默认周报模板各 section 的绑定配置(view_ids/owner_id)。
    兼容旧前端 binding 结构: {id, section_key, view_ids, owner_id, is_active}。"""
    t = _get_default_template(db, "weekly")
    if t and t.framework:
        result = []
        for sec in t.framework:
            result.append(
                {
                    "id": None,
                    "section_key": sec.get("key"),
                    "view_ids": sec.get("view_ids") or [],
                    "owner_id": sec.get("owner_id"),
                    "is_active": True,
                    "created_by": None,
                }
            )
        return R(data=result)
    # 无模板时回退旧表
    existing = {b.section_key: b for b in db.scalars(select(WeeklySectionBinding))}
    result = []
    for s in WEEKLY_FRAMEWORK_SECTIONS:
        b = existing.get(s["key"])
        if b:
            result.append(_serialize_binding(b))
        else:
            result.append(
                {
                    "id": None,
                    "section_key": s["key"],
                    "view_ids": [],
                    "owner_id": None,
                    "is_active": True,
                    "created_by": None,
                }
            )
    return R(data=result)


@router.put("/bindings", response_model=R, summary="批量保存绑定配置(代理写入默认模板)")
def save_bindings(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    """批量保存分项默认绑定配置, 写入默认周报模板的 framework section(view_ids/owner_id)。
    payload: {bindings: [{section_key, view_ids, owner_id, is_active}]}"""
    items = payload.get("bindings") or []
    item_map = {it.get("section_key"): it for it in items if it.get("section_key")}
    t = _get_default_template(db, "weekly")
    if t:
        new_framework = []
        for sec in (t.framework or []):
            key = sec.get("key")
            it = item_map.get(key)
            if it:
                new_framework.append(
                    {
                        "key": key,
                        "title": sec.get("title") or key,
                        "section_type": sec.get("section_type") or "view",
                        "view_ids": it.get("view_ids") or [],
                        "owner_id": it.get("owner_id"),
                        "summary_placeholder": sec.get("summary_placeholder") or "",
                    }
                )
            else:
                new_framework.append(dict(sec))
        t.framework = new_framework
        db.commit()
        return R(message="绑定配置已写入默认周报模板")
    # 无模板时回退旧表
    existing = {b.section_key: b for b in db.scalars(select(WeeklySectionBinding))}
    valid_keys = {s["key"] for s in WEEKLY_FRAMEWORK_SECTIONS}
    for item in items:
        key = item.get("section_key")
        if key not in valid_keys:
            continue
        b = existing.get(key)
        if b:
            b.view_ids = item.get("view_ids") or []
            b.owner_id = item.get("owner_id")
            b.is_active = item.get("is_active", True)
        else:
            b = WeeklySectionBinding(
                section_key=key,
                view_ids=item.get("view_ids") or [],
                owner_id=item.get("owner_id"),
                is_active=item.get("is_active", True),
                created_by=current_user.username,
            )
            db.add(b)
    db.commit()
    return R(message="绑定配置已保存")


# ---------- 一键生成本周周报看板 ----------


@router.post("/generate", response_model=R, summary="一键生成本周周报看板(自动周次+默认模板)")
def generate_this_week(db: DBSession, current_user: AdminOrSupervisor, payload: dict = Body(default={})):
    """一键生成本周周报看板:
    - 自动计算本周周一~周日 + ISO 周次名称(如 2026-W30 客服中心周报)
    - 从默认周报模板实例化 framework(含 view_ids/owner_id 默认绑定)
    - 若本周已存在草稿则返回已有(避免重复创建)
    payload 可选: {date?: 'YYYY-MM-DD'(指定周), name?: 覆盖名称, template_id?: 指定模板}
    """
    date_str = payload.get("date")
    base_dt = datetime.fromisoformat(date_str) if date_str else datetime.now()
    monday, sunday, week_label = _iso_week_range(base_dt)

    # 检查本周是否已有周报(按 week_start)
    existing = db.scalar(
        select(WeeklyReport).where(WeeklyReport.week_start == monday).order_by(WeeklyReport.id.desc())
    )
    if existing:
        return R(data=_serialize(existing), message=f"本周({week_label})已存在周报看板")

    # 取模板: 优先指定 template_id, 否则默认周报模板
    t = None
    if payload.get("template_id"):
        t = db.get(ReportTemplate, payload["template_id"])
    if not t:
        t = _get_default_template(db, "weekly")

    if t and t.framework:
        framework = instantiate_framework(t.framework)
    else:
        # 无模板时回退旧逻辑(10 项 + 旧绑定表)
        bindings = {
            b.section_key: b
            for b in db.scalars(select(WeeklySectionBinding).where(WeeklySectionBinding.is_active.is_(True)))
        }
        framework = []
        for s in WEEKLY_FRAMEWORK_SECTIONS:
            b = bindings.get(s["key"])
            framework.append(
                {
                    "key": s["key"],
                    "title": s["title"],
                    "section_type": s["section_type"],
                    "view_ids": (b.view_ids if b and b.view_ids else []),
                    "owner_id": (b.owner_id if b else None),
                    "summary": "",
                    "data": None,
                    "related_report_ids": [],
                }
            )

    name = payload.get("name") or f"{week_label} 客服中心周报"
    w = WeeklyReport(
        name=name,
        week_start=monday,
        week_end=sunday,
        framework=framework,
        status=WeeklyReportStatus.DRAFT.value,
        created_by=current_user.username,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return R(data=_serialize(w), message=f"已生成 {week_label} 周报看板(已从默认模板实例化)")


# ---------- 周报 CRUD ----------


@router.get("", response_model=R[PageBase], summary="周报看板列表")
def list_weekly_reports(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="状态: draft/confirmed/archived"),
):
    stmt = select(WeeklyReport)
    if status:
        try:
            stmt = stmt.where(WeeklyReport.status == WeeklyReportStatus(status).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态值: {status}")
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(
            stmt.order_by(WeeklyReport.week_start.desc().nulls_last(), WeeklyReport.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(
        data=PageBase(items=[_serialize(w) for w in items], total=total or 0, page=page, page_size=page_size)
    )


@router.get("/{weekly_id}", response_model=R, summary="周报详情(含10项框架)")
def get_weekly_report(weekly_id: int, db: DBSession, current_user: CurrentUser):
    w = db.get(WeeklyReport, weekly_id)
    if not w:
        raise HTTPException(status_code=404, detail="周报不存在")
    return R(data=_serialize(w))


@router.post("", response_model=R, summary="创建周报看板(草稿)")
def create_weekly_report(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="周报名不能为空")
    framework = payload.get("framework")
    if not framework:
        framework = default_framework()
    else:
        framework = _normalize_framework(framework)
    w = WeeklyReport(
        name=name,
        week_start=datetime.fromisoformat(payload["week_start"]) if payload.get("week_start") else None,
        week_end=datetime.fromisoformat(payload["week_end"]) if payload.get("week_end") else None,
        framework=framework,
        summary=payload.get("summary"),
        meeting_conclusion=payload.get("meeting_conclusion"),
        status=WeeklyReportStatus.DRAFT.value,
        created_by=current_user.username,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return R(data=_serialize(w))


@router.put("/{weekly_id}", response_model=R, summary="更新周报(仅草稿)")
def update_weekly_report(weekly_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    w = db.get(WeeklyReport, weekly_id)
    if not w:
        raise HTTPException(status_code=404, detail="周报不存在")
    if w.status != WeeklyReportStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="仅草稿可编辑, 已确认/已归档不可改")
    if "name" in payload:
        w.name = (payload["name"] or "").strip() or w.name
    if "summary" in payload:
        w.summary = payload["summary"]
    if "meeting_conclusion" in payload:
        w.meeting_conclusion = payload["meeting_conclusion"]
    if "framework" in payload:
        w.framework = _normalize_framework(payload["framework"])
    for k in ("week_start", "week_end"):
        if k in payload:
            setattr(w, k, datetime.fromisoformat(payload[k]) if payload[k] else None)
    db.commit()
    db.refresh(w)
    return R(data=_serialize(w))


@router.post("/{weekly_id}/confirm", response_model=R, summary="确认周报(固化快照)")
def confirm_weekly_report(weekly_id: int, db: DBSession, current_user: AdminOrSupervisor):
    """确认周报: 固化各 section.data(快照), status→confirmed。
    确认后不再随源数据变, 可持续批注/下钻审计。"""
    w = db.get(WeeklyReport, weekly_id)
    if not w:
        raise HTTPException(status_code=404, detail="周报不存在")
    if w.status != WeeklyReportStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail=f"仅草稿可确认, 当前状态: {w.status}")
    w.status = WeeklyReportStatus.CONFIRMED.value
    w.confirmed_at = datetime.now()
    w.confirmed_by = current_user.username
    db.commit()
    db.refresh(w)
    return R(data=_serialize(w))


@router.post("/{weekly_id}/archive", response_model=R, summary="归档周报")
def archive_weekly_report(weekly_id: int, db: DBSession, current_user: AdminOrSupervisor):
    w = db.get(WeeklyReport, weekly_id)
    if not w:
        raise HTTPException(status_code=404, detail="周报不存在")
    if w.status == WeeklyReportStatus.ARCHIVED.value:
        raise HTTPException(status_code=400, detail="周报已归档")
    w.status = WeeklyReportStatus.ARCHIVED.value
    db.commit()
    db.refresh(w)
    return R(data=_serialize(w))


@router.delete("/{weekly_id}", response_model=R, summary="删除周报")
def delete_weekly_report(weekly_id: int, db: DBSession, current_user: AdminOrSupervisor):
    w = db.get(WeeklyReport, weekly_id)
    if not w:
        raise HTTPException(status_code=404, detail="周报不存在")
    db.delete(w)
    db.commit()
    return R()


# ---------- 专项下钻: 从周报某分项一键创建专项报告 ----------


@router.post("/{weekly_id}/sections/{section_key}/drill-report", response_model=R, summary="专项下钻: 创建专项报告(指派负责人)")
def create_drill_report(
    weekly_id: int,
    section_key: str,
    payload: dict,
    db: DBSession,
    current_user: AdminOrSupervisor,
):
    """从周报某分项下钻创建专项报告(会议中对某专项有异议→安排负责人审查)。
    payload: {name?, assignee_id?, due_date?, summary?}
    - 自动创建 Report(special_audit, 关联本周时间, 回链 source_weekly_id/source_section_key)
    - 把新 report.id 追加到 section.related_report_ids
    """
    w = db.get(WeeklyReport, weekly_id)
    if not w:
        raise HTTPException(status_code=404, detail="周报不存在")
    # 找到对应 section
    framework = w.framework or []
    section = next((s for s in framework if s.get("key") == section_key), None)
    if not section:
        raise HTTPException(status_code=400, detail=f"无效的分项key: {section_key}")

    name = (payload.get("name") or f"{w.name} - {section.get('title', section_key)} 专项审查").strip()
    # 套用专项审计默认模板实例化 framework(支持可编辑看板)
    sa_template = _get_default_template(db, "special_audit")
    report_framework = instantiate_framework(sa_template.framework) if sa_template and sa_template.framework else None
    r = Report(
        name=name,
        report_type=ReportType.SPECIAL_AUDIT.value,
        time_start=w.week_start,
        time_end=w.week_end,
        status=ReportStatus.DRAFT.value,
        framework=report_framework,
        assignee_id=payload.get("assignee_id"),
        due_date=datetime.fromisoformat(payload["due_date"]) if payload.get("due_date") else None,
        source_weekly_id=weekly_id,
        source_section_key=section_key,
        created_by=current_user.username,
    )
    db.add(r)
    db.flush()
    # 回链到 section.related_report_ids
    related_ids = list(section.get("related_report_ids") or [])
    if r.id not in related_ids:
        related_ids.append(r.id)
    section["related_report_ids"] = related_ids
    w.framework = framework  # 触发 JSONB 更新
    flag_modified(w, "framework")  # 确保 SQLAlchemy 检测到 JSONB 原地变更
    db.commit()
    db.refresh(r)
    return R(
        data={
            "report_id": r.id,
            "report_name": r.name,
            "related_report_ids": related_ids,
        },
        message=f"已创建专项报告 #{r.id}, 已回链到分项[{section.get('title')}]",
    )
