"""看板路由 - 决策消费层

看板 = 多个 AnalysisView 聚合到一个可配置页面。
看板层不重新实现聚合,前端按 items 逐个调 /analysis-views/execute(view_id 模式)。

权限双轨:
- visibility 字段(public/manager/admin)做 API 行级过滤
- 路由 meta.manager 做页面级过滤(前端守卫)
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AdminOrSupervisor, CurrentUser, DBSession
from app.models.analysis_view import AnalysisView
from app.models.dashboard import Dashboard
from app.schemas.common import PageBase, R
from app.schemas.dashboard import (
    DashboardCreate,
    DashboardExecuteRequest,
    DashboardItemResult,
    DashboardOut,
    DashboardUpdate,
)
from app.services.analysis_engine import execute_view

router = APIRouter(prefix="/dashboards", tags=["看板"])


def _allowed_visibility(role: str) -> list[str]:
    """按角色返回可见的 visibility 列表(行级过滤)"""
    if role == "admin":
        return ["public", "manager", "admin"]
    if role == "supervisor":
        return ["public", "manager"]
    return ["public"]  # agent


def _check_visibility(d: Dashboard, role: str) -> None:
    """单看板可见性校验,无权则 403"""
    if d.visibility not in _allowed_visibility(role):
        raise HTTPException(403, "无权访问该看板")


@router.get("", response_model=R, summary="看板列表(分页)")
def list_dashboards(
    db: DBSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None, description="按分类筛选"),
    status: str | None = Query(None, description="active/archived,默认 active"),
    keyword: str | None = Query(None, description="名称搜索"),
):
    stmt = select(Dashboard).where(Dashboard.visibility.in_(_allowed_visibility(user.role)))
    if status:
        stmt = stmt.where(Dashboard.status == status)
    else:
        stmt = stmt.where(Dashboard.status == "active")
    if category:
        stmt = stmt.where(Dashboard.category == category)
    if keyword:
        stmt = stmt.where(Dashboard.name.ilike(f"%{keyword}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(Dashboard.sort_order, Dashboard.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return R(data=PageBase(items=[DashboardOut.model_validate(d) for d in items], total=total, page=page, page_size=page_size))


@router.get("/{dashboard_id}", response_model=R, summary="看板详情")
def get_dashboard(db: DBSession, user: CurrentUser, dashboard_id: int):
    d = db.get(Dashboard, dashboard_id)
    if not d or d.status != "active":
        raise HTTPException(404, "看板不存在")
    _check_visibility(d, user.role)
    return R(data=DashboardOut.model_validate(d))


@router.post("/{dashboard_id}/execute", response_model=R, summary="批量执行看板(一次取全部视图结果)")
def execute_dashboard(db: DBSession, user: CurrentUser, dashboard_id: int, payload: DashboardExecuteRequest):
    """批量执行看板所有 visible 项,单视图失败隔离。

    前端一次调用取全部结果,避免 N 次并发 /analysis-views/execute。
    start/end 为空则各视图回退自身 time_range_default(复用 _resolve_time_range 既有逻辑)。
    """
    d = db.get(Dashboard, dashboard_id)
    if not d or d.status != "active":
        raise HTTPException(404, "看板不存在")
    _check_visibility(d, user.role)

    params = {"start": payload.start, "end": payload.end}
    if payload.time_grain:
        params["time_grain"] = payload.time_grain
    items_out: list[DashboardItemResult] = []
    for it in d.items or []:
        if it.get("visible") is False:
            continue
        view_id = it.get("view_id")
        if not view_id:
            continue
        v = db.get(AnalysisView, view_id)
        item = DashboardItemResult(
            view_id=view_id,
            view_code=it.get("view_code"),
            title=it.get("title"),
        )
        if not v or v.status != "active":
            item.error = "视图不存在或已归档"
        else:
            # 附带视图元数据,前端卡片无需再单独 GET 视图详情
            item.view_name = v.name
            item.ratios = v.ratios
            item.is_preset = v.is_preset
            try:
                item.result = execute_view(db, v, params)
            except Exception as e:  # noqa: BLE001 单视图失败隔离,不影响其他项
                item.error = str(e)
        items_out.append(item)

    return R(data={
        "dashboard_id": dashboard_id,
        "start": payload.start,
        "end": payload.end,
        "items": items_out,
    })


@router.post("", response_model=R, summary="创建看板(管理层)")
def create_dashboard(db: DBSession, user: AdminOrSupervisor, payload: DashboardCreate):
    d = Dashboard(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        category=payload.category,
        items=payload.items,
        time_range_default=payload.time_range_default,
        is_preset=False,
        status="active",
        visibility=payload.visibility,
        owner_id=user.id,
        sort_order=payload.sort_order,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return R(data=DashboardOut.model_validate(d))


@router.put("/{dashboard_id}", response_model=R, summary="更新看板(管理层)")
def update_dashboard(db: DBSession, user: AdminOrSupervisor, dashboard_id: int, payload: DashboardUpdate):
    d = db.get(Dashboard, dashboard_id)
    if not d:
        raise HTTPException(404, "看板不存在")
    data = payload.model_dump(exclude_unset=True)
    # 预设看板的 code/category 不可改
    if d.is_preset:
        data.pop("category", None)
    # code 一律不允许通过 update 改(避免破坏预设 upsert)
    data.pop("code", None)
    for k, val in data.items():
        setattr(d, k, val)
    db.commit()
    db.refresh(d)
    return R(data=DashboardOut.model_validate(d))


@router.delete("/{dashboard_id}", response_model=R, summary="删除看板(预设不可删)")
def delete_dashboard(db: DBSession, _: AdminOrSupervisor, dashboard_id: int):
    d = db.get(Dashboard, dashboard_id)
    if not d:
        raise HTTPException(404, "看板不存在")
    if d.is_preset:
        raise HTTPException(400, "预设看板不可删除,可改为 archived")
    db.delete(d)
    db.commit()
    return R(message="已删除")
