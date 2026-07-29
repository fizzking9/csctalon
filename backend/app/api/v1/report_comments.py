"""报告/周报跟进注释路由 - 统一的活文档批注(周报+报告通用)

设计原则:
- 周报/报告确认固化后, 后续跟进通过注释累积(时间线), 不改原文
- 注释可派生管理措施(跟进中发现问题→转措施, 形成"注释→措施"闭环)
- target_type=weekly 时 section_key 定位到周报某分项; target_type=report 时 section_key 为空

与 AuditNote 区别: ReportComment 是轻量跟进(纯文字+可派生措施), AuditNote 是正式审计(结论+评判+贴图)。
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.measure import Measure, MeasureSource, MeasureStatus
from app.models.report_comment import CommentTarget, ReportComment
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/report-comments", tags=["跟进注释"])


def _serialize(c: ReportComment) -> dict:
    return {
        "id": c.id,
        "target_type": c.target_type,
        "target_id": c.target_id,
        "section_key": c.section_key,
        "content": c.content,
        "author_id": c.author_id,
        "author_name": c.author_name,
        "output_measure_id": c.output_measure_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("", response_model=R, summary="跟进注释列表(按目标筛选)")
def list_comments(
    db: DBSession,
    current_user: CurrentUser,
    target_type: str = Query(..., description="weekly/report"),
    target_id: int = Query(..., description="周报ID/报告ID"),
    section_key: str | None = Query(None, description="定位到周报某分项(可选)"),
):
    try:
        tt = CommentTarget(target_type).value
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的目标类型: {target_type}")
    stmt = select(ReportComment).where(
        ReportComment.target_type == tt,
        ReportComment.target_id == target_id,
    )
    if section_key:
        stmt = stmt.where(ReportComment.section_key == section_key)
    items = list(db.scalars(stmt.order_by(ReportComment.id)))
    return R(data=[_serialize(c) for c in items])


@router.post("", response_model=R, summary="添加跟进注释(可派生措施)")
def add_comment(payload: dict, db: DBSession, current_user: AdminOrSupervisor):
    """添加跟进注释。payload: {target_type, target_id, section_key?, content, create_measure?}
    create_measure(可选): {title, owner_id?, expected_goal?, verify_time?, verify_method?} → 派生 Measure
    """
    try:
        tt = CommentTarget(payload.get("target_type")).value
    except (ValueError, KeyError):
        raise HTTPException(status_code=400, detail="target_type 必须为 weekly/report")
    target_id = payload.get("target_id")
    if not target_id:
        raise HTTPException(status_code=400, detail="target_id 不能为空")
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="注释内容不能为空")

    comment = ReportComment(
        target_type=tt,
        target_id=target_id,
        section_key=payload.get("section_key"),
        content=content,
        author_id=current_user.id,
        author_name=current_user.display_name,
    )

    # 派生管理措施(跟进→措施闭环)
    measure_spec = payload.get("create_measure")
    if measure_spec:
        title = (measure_spec.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="派生措施的标题不能为空")
        source = MeasureSource.WEEKLY if tt == CommentTarget.WEEKLY.value else MeasureSource.AUDIT
        m = Measure(
            title=title,
            source=source.value,
            owner_id=measure_spec.get("owner_id"),
            expected_goal=measure_spec.get("expected_goal"),
            verify_time=datetime.fromisoformat(measure_spec["verify_time"]) if measure_spec.get("verify_time") else None,
            verify_method=measure_spec.get("verify_method"),
            status=MeasureStatus.EXECUTING.value,
            created_by=current_user.username,
        )
        db.add(m)
        db.flush()
        comment.output_measure_id = m.id

    db.add(comment)
    db.commit()
    db.refresh(comment)
    return R(data=_serialize(comment))


@router.delete("/{comment_id}", response_model=R, summary="删除跟进注释")
def delete_comment(comment_id: int, db: DBSession, current_user: AdminOrSupervisor):
    c = db.get(ReportComment, comment_id)
    if not c:
        raise HTTPException(status_code=404, detail="注释不存在")
    db.delete(c)
    db.commit()
    return R()
