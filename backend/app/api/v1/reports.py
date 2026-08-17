"""报告与审计路由 - 管理闭环核心

核心原则(需求说明):
- 只有报告才能审计(动态数据不能审): AuditNote 必须关联 report_id
- 报告相对固化数据视图: confirm 时固化 snapshot,之后不再随源数据变
- 报告要保存,审计结果也要保存

审计→措施闭环: 添加审计附注时可派生 Measure(source=audit, related_report_id=报告)。
归因不归责: 审计结论用于改进。
"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile
from sqlalchemy import func, select, update

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.core.storage import ALLOWED_IMAGE_EXT, MAX_IMAGE_SIZE, save_audit_attachment, validate_image_content
from app.models.audit_note import AuditJudgment, AuditNote
from app.models.measure import Measure, MeasureSource, MeasureStatus
from app.models.report import Report, ReportStatus, ReportType
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/reports", tags=["报告与审计"])


# ---------- Report 序列化 ----------


def _serialize_report(r: Report, audit_note_count: int | None = None) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "report_type": r.report_type,
        "time_start": r.time_start.isoformat() if r.time_start else None,
        "time_end": r.time_end.isoformat() if r.time_end else None,
        "filters": r.filters,
        "view_ids": r.view_ids,
        "framework": r.framework,
        "has_snapshot": r.snapshot is not None,
        "pdf_archive": r.pdf_archive,
        "status": r.status,
        "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
        "confirmed_by": r.confirmed_by,
        "assignee_id": r.assignee_id,
        "due_date": r.due_date.isoformat() if r.due_date else None,
        "source_weekly_id": r.source_weekly_id,
        "source_section_key": r.source_section_key,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        **({"audit_note_count": audit_note_count} if audit_note_count is not None else {}),
    }


def _serialize_note(n: AuditNote) -> dict:
    return {
        "id": n.id,
        "report_id": n.report_id,
        "conclusion": n.conclusion,
        "judgment": n.judgment,
        "sampled_tickets": n.sampled_tickets,
        "attachments": n.attachments,
        "output_measure_id": n.output_measure_id,
        "author_id": n.author_id,
        "author_name": n.author_name,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


# ---------- Report CRUD ----------


@router.get("", response_model=R[PageBase], summary="报告列表")
def list_reports(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    report_type: str | None = Query(None, description="类型: weekly/special_audit/custom"),
    status: str | None = Query(None, description="状态: draft/confirmed/archived"),
):
    stmt = select(Report)
    if report_type:
        try:
            stmt = stmt.where(Report.report_type == ReportType(report_type).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的类型值: {report_type}")
    if status:
        try:
            stmt = stmt.where(Report.status == ReportStatus(status).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态值: {status}")
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(
        db.scalars(stmt.order_by(Report.id.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    # 批量取审计附注数
    counts = {}
    if items:
        ids = [r.id for r in items]
        counts = {
            r[0]: r[1]
            for r in db.execute(
                select(AuditNote.report_id, func.count())
                .where(AuditNote.report_id.in_(ids))
                .group_by(AuditNote.report_id)
            ).all()
        }
    return R(
        data=PageBase(
            items=[_serialize_report(r, counts.get(r.id, 0)) for r in items],
            total=total or 0,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/{report_id}", response_model=R, summary="报告详情(含快照+看板框架)")
def get_report(report_id: int, db: DBSession, current_user: CurrentUser):
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="报告不存在")
    data = _serialize_report(r)
    data["snapshot"] = r.snapshot  # 详情返回完整快照
    return R(data=data)


@router.post("", response_model=R, summary="创建报告(草稿)")
def create_report(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="报告名不能为空")
    r = Report(
        name=name,
        report_type=ReportType(payload.get("report_type", "custom")).value,
        time_start=datetime.fromisoformat(payload["time_start"]) if payload.get("time_start") else None,
        time_end=datetime.fromisoformat(payload["time_end"]) if payload.get("time_end") else None,
        filters=payload.get("filters"),
        view_ids=payload.get("view_ids"),
        framework=payload.get("framework"),
        status=ReportStatus.DRAFT.value,
        assignee_id=payload.get("assignee_id"),
        due_date=datetime.fromisoformat(payload["due_date"]) if payload.get("due_date") else None,
        source_weekly_id=payload.get("source_weekly_id"),
        source_section_key=payload.get("source_section_key"),
        created_by=current_user.username,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return R(data=_serialize_report(r, 0))


@router.put("/{report_id}", response_model=R, summary="更新报告(仅草稿)")
def update_report(report_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="报告不存在")
    if r.status != ReportStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="仅草稿可编辑,已确认/已归档不可改")
    for k in ("name", "filters", "view_ids", "framework", "pdf_archive", "assignee_id", "source_weekly_id", "source_section_key"):
        if k in payload:
            setattr(r, k, payload[k])
    for k in ("time_start", "time_end", "due_date"):
        if k in payload:
            setattr(r, k, datetime.fromisoformat(payload[k]) if payload[k] else None)
    if "report_type" in payload:
        r.report_type = ReportType(payload["report_type"]).value
    db.commit()
    db.refresh(r)
    return R(data=_serialize_report(r))


@router.post("/{report_id}/confirm", response_model=R, summary="确认报告(固化快照)")
def confirm_report(report_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    """确认报告: 固化快照, status→confirmed。双模式:
    - 看板模式(有 framework): 固化各 section.data(若前端传入), 自动派生轻量 snapshot
    - 纯快照模式(无 framework): 必须传 snapshot(向后兼容)
    快照一旦固化不再随源数据变,满足"报告固化数据视图"的审计要求。"""
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="报告不存在")
    if r.status != ReportStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail=f"仅草稿可确认,当前状态: {r.status}")

    framework = r.framework
    # 更新 framework(固化各 section.data 快照)
    if framework is not None and payload.get("framework") is not None:
        r.framework = payload["framework"]
        framework = r.framework

    if framework:
        # 看板模式: 派生轻量 snapshot = 各 section 的 view_ids + data 摘要
        derived = []
        for sec in framework:
            if not isinstance(sec, dict):
                continue
            derived.append(
                {
                    "key": sec.get("key"),
                    "title": sec.get("title"),
                    "view_ids": sec.get("view_ids") or [],
                    "has_data": sec.get("data") is not None,
                }
            )
        r.snapshot = {"mode": "kanban", "sections": derived}
    else:
        # 纯快照模式: 必须传 snapshot
        snapshot = payload.get("snapshot")
        if snapshot is None:
            raise HTTPException(status_code=400, detail="无看板框架时,确认必须提供快照数据")
        r.snapshot = snapshot

    if payload.get("pdf_archive"):
        r.pdf_archive = payload["pdf_archive"]
    r.status = ReportStatus.CONFIRMED.value
    r.confirmed_at = datetime.now()
    r.confirmed_by = current_user.username
    db.commit()
    db.refresh(r)
    data = _serialize_report(r)
    data["snapshot"] = r.snapshot  # 确认后返回完整快照(供前端展示)
    return R(data=data)


@router.post("/{report_id}/archive", response_model=R, summary="归档报告")
def archive_report(report_id: int, db: DBSession, current_user: AdminOrSupervisor):
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="报告不存在")
    if r.status == ReportStatus.ARCHIVED.value:
        raise HTTPException(status_code=400, detail="报告已归档")
    r.status = ReportStatus.ARCHIVED.value
    db.commit()
    db.refresh(r)
    return R(data=_serialize_report(r))


@router.delete("/{report_id}", response_model=R, summary="删除报告")
def delete_report(report_id: int, db: DBSession, current_user: AdminOrSupervisor):
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="报告不存在")
    # 解除 measures 对该报告的引用(避免 FK 冲突;措施本身保留,仅断开关联)
    db.execute(update(Measure).where(Measure.related_report_id == report_id).values(related_report_id=None))
    # 级联删除审计附注
    for n in db.scalars(select(AuditNote).where(AuditNote.report_id == report_id)):
        db.delete(n)
    db.flush()
    db.delete(r)
    db.commit()
    return R()


# ---------- 贴图上传 ----------


@router.post("/attachments", response_model=R, summary="上传审计贴图")
def upload_attachment(
    file: UploadFile,
    db: DBSession,
    current_user: AdminOrSupervisor,
    report_id: int | None = Query(None, description="关联报告ID(用于分目录)"),
):
    """上传审计贴图(聊天记录截图),返回 {path, url}。
    供 AuditNote.attachments 使用。内网本地目录存储,按报告/日期分目录。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(status_code=400, detail=f"仅支持图片格式: {', '.join(ALLOWED_IMAGE_EXT)}")
    content = file.file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail=f"图片大小不能超过 {MAX_IMAGE_SIZE // 1024 // 1024}MB")
    # P0-3: magic bytes 校验, 防止 .html 等改扩展名伪装成图片上传
    try:
        validate_image_content(content, ext)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    file.file.seek(0)
    rel, url = save_audit_attachment(file, report_id)
    return R(data={"path": rel, "url": url})


# ---------- AuditNote 审计附注 ----------


@router.get("/{report_id}/audit-notes", response_model=R, summary="报告审计附注列表")
def list_audit_notes(report_id: int, db: DBSession, current_user: CurrentUser):
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="报告不存在")
    notes = list(
        db.scalars(select(AuditNote).where(AuditNote.report_id == report_id).order_by(AuditNote.id))
    )
    return R(data=[_serialize_note(n) for n in notes])


@router.post("/{report_id}/audit-notes", response_model=R, summary="添加审计附注(可派生措施)")
def add_audit_note(report_id: int, payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    """添加审计附注(结论/评判/抽检工单/贴图),可选派生 Measure 形成审计→措施闭环。
    归因不归责: 审计结论用于改进。"""
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="报告不存在")
    note = AuditNote(
        report_id=report_id,
        conclusion=payload.get("conclusion"),
        sampled_tickets=payload.get("sampled_tickets"),
        attachments=payload.get("attachments"),
        author_id=current_user.id,
        author_name=current_user.display_name,
    )
    if payload.get("judgment"):
        try:
            note.judgment = AuditJudgment(payload["judgment"]).value
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的评判值: {payload['judgment']}")
    # 派生管理措施(审计→措施闭环)
    measure_spec = payload.get("create_measure")
    if measure_spec:
        title = (measure_spec.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="派生措施的标题不能为空")
        m = Measure(
            title=title,
            source=MeasureSource.AUDIT.value,
            related_report_id=report_id,
            owner_id=measure_spec.get("owner_id"),
            expected_goal=measure_spec.get("expected_goal"),
            verify_time=datetime.fromisoformat(measure_spec["verify_time"]) if measure_spec.get("verify_time") else None,
            verify_method=measure_spec.get("verify_method"),
            status=MeasureStatus.EXECUTING.value,
            created_by=current_user.username,
        )
        db.add(m)
        db.flush()
        note.output_measure_id = m.id
    db.add(note)
    db.commit()
    db.refresh(note)
    return R(data=_serialize_note(note))


@router.delete("/{report_id}/audit-notes/{note_id}", response_model=R, summary="删除审计附注")
def delete_audit_note(report_id: int, note_id: int, db: DBSession, current_user: AdminOrSupervisor):
    n = db.get(AuditNote, note_id)
    if not n or n.report_id != report_id:
        raise HTTPException(status_code=404, detail="审计附注不存在")
    db.delete(n)
    db.commit()
    return R()
