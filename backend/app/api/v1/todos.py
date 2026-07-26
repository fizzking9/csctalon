"""事项跟进路由 - 工单式待办"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession
from app.models.todo import TodoFollowup, TodoItem, TodoPriority, TodoSource, TodoStatus
from app.models.user import RoleEnum
from app.schemas.common import PageBase, R

router = APIRouter(prefix="/todos", tags=["事项跟进"])


def _serialize(t: TodoItem) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "source": t.source,
        "priority": t.priority,
        "status": t.status,
        "assignee_id": t.assignee_id,
        "deadline": t.deadline.isoformat() if t.deadline else None,
        "related_device_sn": t.related_device_sn,
        "related_task_id": t.related_task_id,
        "related_refund_no": t.related_refund_no,
        "related_ticket_no": t.related_ticket_no,
        "description": t.description,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("", response_model=R[PageBase], summary="待办列表")
def list_todos(
    db: DBSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="状态: pending/processing/confirming/done"),
    priority: str | None = Query(None, description="优先级: low/medium/high/urgent"),
    assignee_id: int | None = None,
):
    stmt = select(TodoItem)
    # 数据隔离:客服只能看指派给自己的
    if current_user.role == RoleEnum.AGENT.value:
        stmt = stmt.where(TodoItem.assignee_id == current_user.id)
    elif current_user.role == RoleEnum.SUPERVISOR.value and current_user.group_name:
        # 主管看本组(简化:同组用户的待办) - 此处先用 assignee_id 或全部,后续完善
        pass
    # 空字符串/None 均视为不过滤;非空时校验枚举值
    if status:
        try:
            stmt = stmt.where(TodoItem.status == TodoStatus(status).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态值: {status}")
    if priority:
        try:
            stmt = stmt.where(TodoItem.priority == TodoPriority(priority).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的优先级值: {priority}")
    if assignee_id:
        stmt = stmt.where(TodoItem.assignee_id == assignee_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(TodoItem.priority.desc(), TodoItem.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return R(data=PageBase(items=[_serialize(t) for t in items], total=total or 0, page=page, page_size=page_size))


@router.post("", response_model=R, summary="创建待办")
def create_todo(payload: dict, db: DBSession, current_user: CurrentUser):
    todo = TodoItem(
        title=payload["title"],
        source=TodoSource(payload.get("source", "manual")).value,
        priority=TodoPriority(payload.get("priority", "medium")).value,
        assignee_id=payload.get("assignee_id"),
        deadline=datetime.fromisoformat(payload["deadline"]) if payload.get("deadline") else None,
        related_device_sn=payload.get("related_device_sn"),
        related_task_id=payload.get("related_task_id"),
        related_refund_no=payload.get("related_refund_no"),
        related_ticket_no=payload.get("related_ticket_no"),
        description=payload.get("description"),
        created_by=current_user.username,
    )
    db.add(todo)
    db.commit()
    db.refresh(todo)
    return R(data=_serialize(todo))


@router.get("/{todo_id}", response_model=R, summary="待办详情")
def get_todo(todo_id: int, db: DBSession, current_user: CurrentUser):
    todo = db.get(TodoItem, todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="待办不存在")
    # 权限校验
    if current_user.role == RoleEnum.AGENT.value and todo.assignee_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权查看他人待办")
    data = _serialize(todo)
    # 跟进记录
    followups = list(db.scalars(select(TodoFollowup).where(TodoFollowup.todo_id == todo_id).order_by(TodoFollowup.id)))
    data["followups"] = [
        {
            "id": f.id,
            "author_name": f.author_name,
            "content": f.content,
            "status_change": f.status_change,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in followups
    ]
    return R(data=data)


@router.put("/{todo_id}", response_model=R, summary="更新待办")
def update_todo(todo_id: int, payload: dict, db: DBSession, current_user: CurrentUser):
    todo = db.get(TodoItem, todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="待办不存在")
    if current_user.role == RoleEnum.AGENT.value and todo.assignee_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权修改他人待办")
    for k in ("title", "assignee_id", "deadline", "description", "related_device_sn", "related_task_id", "related_refund_no", "related_ticket_no"):
        if k in payload:
            setattr(todo, k, datetime.fromisoformat(payload[k]) if k == "deadline" and payload[k] else payload[k])
    if "status" in payload:
        todo.status = TodoStatus(payload["status"]).value
    if "priority" in payload:
        todo.priority = TodoPriority(payload["priority"]).value
    db.commit()
    db.refresh(todo)
    return R(data=_serialize(todo))


@router.post("/{todo_id}/followups", response_model=R, summary="添加跟进记录")
def add_followup(todo_id: int, payload: dict, db: DBSession, current_user: CurrentUser):
    todo = db.get(TodoItem, todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="待办不存在")
    if current_user.role == RoleEnum.AGENT.value and todo.assignee_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权跟进他人待办")
    content = payload.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="跟进内容不能为空")
    followup = TodoFollowup(
        todo_id=todo_id,
        author_id=current_user.id,
        author_name=current_user.display_name,
        content=content,
        status_change=payload.get("status_change"),
    )
    db.add(followup)
    # 如果带状态变更,同步更新待办状态
    if payload.get("new_status"):
        todo.status = TodoStatus(payload["new_status"]).value
    db.commit()
    db.refresh(followup)
    return R(data={"id": followup.id, "created_at": followup.created_at.isoformat() if followup.created_at else None})


@router.delete("/{todo_id}", response_model=R, summary="删除待办")
def delete_todo(todo_id: int, db: DBSession, current_user: CurrentUser):
    todo = db.get(TodoItem, todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="待办不存在")
    if current_user.role not in (RoleEnum.ADMIN.value, RoleEnum.SUPERVISOR.value) and todo.created_by != current_user.username:
        raise HTTPException(status_code=403, detail="无权删除")
    # 删除关联的跟进记录(无 relationship 声明,需先 flush 确保子记录先删,避免 FK 约束)
    followups = list(db.scalars(select(TodoFollowup).where(TodoFollowup.todo_id == todo_id)))
    for f in followups:
        db.delete(f)
    db.flush()
    db.delete(todo)
    db.commit()
    return R()
