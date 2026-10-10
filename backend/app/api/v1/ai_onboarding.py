"""AI 数据接入助手路由（工单 #157）

接口契约见 docs/design/AI数据接入助手设计-20260919.md §七：
  7 个端点：POST/GET/PUT /drafts、confirm、apply、列表、registry-snapshot。
响应遵循 error-code 规范（{code:0,data,message}，错误走 HTTPException）。

设计关键点：
1. 起草为「异步 202 + 轮询」：POST 预建占位行（status=drafting, code=NULL）立即返回
   id，后台任务经 llm_scheduler L1 跑真实 LLM，完成后置 status=draft（或 failed）。
   → 前端用 GET /drafts/{id} 轮询 status 即可，不需要第二个状态字段。
2. 权限（设计 §七）：草稿可见/可编辑 = 创建者 + admin；confirm/apply 仅 admin
   （确认会冻结声明，落地包影响 registry，属管理员决策）。
3. PUT 人工编辑后重跑校验器（规范可能已变）即时反馈；confirm 里再跑一道终检
   （fail=0 + warn 逐条处置），避免编辑途中半途而废把旧报告当结论。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.database import SessionLocal
from app.models.data_source_spec import (
    SPEC_STATUS_APPLIED,
    SPEC_STATUS_CONFIRMED,
    SPEC_STATUS_DRAFTING,
    DataSourceSpec,
)
from app.models.user import RoleEnum
from app.schemas.common import R
from app.services.data_source_registry import CATEGORY_ORDER
from app.services.onboarding_apply_service import (
    SpecFlowError,
    confirm_spec,
    generate_apply_package,
    rerun_validation,
)
from app.services.onboarding_draft_service import (
    DraftError,
    create_draft_placeholder,
    start_draft_task,
)
from app.services.onboarding_validator import (
    _ALLOWED_PREFIXES,
    VALID_SEMANTIC_ROLES,
    build_registry_snapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-onboarding", tags=["AI 数据接入助手"])

# 起草输入样例行数上限（与 draft_service._MAX_SAMPLE_ROWS 镜像；量级校验在路由层即可）
_MAX_SAMPLE_ROWS = 50
# 可被人工编辑的声明主体字段（PUT 白名单：AI 产物 JSONB 不在此列，避免改坏结构）
_EDITABLE_FIELDS = (
    "code", "table_name", "name_cn", "category", "sort_order",
    "time_field", "sn_field", "pk_field", "pk_strategy", "sync_source", "description",
)


def _is_admin(user) -> bool:
    return user.role == RoleEnum.ADMIN


def _spec_dict(spec: DataSourceSpec) -> dict:
    """声明行 → 接口返回 dict（含主要 JSONB 产物，供前端详情展示）。"""
    return {
        "id": spec.id,
        "code": spec.code,
        "table_name": spec.table_name,
        "name_cn": spec.name_cn,
        "category": spec.category,
        "sort_order": spec.sort_order,
        "time_field": spec.time_field,
        "sn_field": spec.sn_field,
        "pk_field": spec.pk_field,
        "pk_strategy": spec.pk_strategy,
        "sync_source": spec.sync_source,
        "description": spec.description,
        "status": spec.status,
        "issue_no": spec.issue_no,
        "error_message": spec.error_message,
        "sample_summary": spec.sample_summary,
        "field_specs": spec.field_specs,
        "dedup_recommendation": spec.dedup_recommendation,
        "validator_report": spec.validator_report,
        "apply_package": spec.apply_package,
        "created_by": spec.created_by,
        "confirmed_by": spec.confirmed_by,
        "confirmed_at": spec.confirmed_at.isoformat() if spec.confirmed_at else None,
        "applied_at": spec.applied_at.isoformat() if spec.applied_at else None,
        "created_at": spec.created_at.isoformat() if spec.created_at else None,
        "updated_at": spec.updated_at.isoformat() if spec.updated_at else None,
    }


def _load_visible(db, draft_id: int, user) -> DataSourceSpec:
    """取草稿并校验可见性（admin 全量；其余仅本人创建），越权一律 403。"""
    spec = db.get(DataSourceSpec, draft_id)
    if spec is None:
        raise HTTPException(404, "草稿不存在")
    if not (_is_admin(user) or spec.created_by == user.id):
        raise HTTPException(403, "无权访问该草稿")
    return spec


@router.post("/drafts", response_model=R, status_code=status.HTTP_202_ACCEPTED,
             summary="提交样例，异步起草（202 + draft_id，轮询 GET /drafts/{id}）")
# #213: 保留 async def —— 本端点末尾 start_draft_task() 依赖 asyncio.get_running_loop()
# 在线程上 create_task（fire-and-forget LLM 起草），故不能改成纯同步 def；
# 仅把其中唯一的阻塞同步 DB 调用 create_draft_placeholder 放入线程池，避免阻塞事件循环。
async def create_draft(payload: dict, db: DBSession, user: CurrentUser):
    """上传样例 → 预建占位行（drafting）→ 立即返回 draft_id；后台经 L1 门闸跑 LLM。

    payload: {table_hint?, business_note?, samples:[{列名:值}, ...]（≤50 行）}
    返回 202 + {draft_id}；轮询 GET /drafts/{draft_id} 直到 status=draft（成功）
    或 failed（失败，error_message 给原因，重新提交即可重试）。
    """
    samples = payload.get("samples")
    if not isinstance(samples, list) or not samples:
        raise HTTPException(400, "samples 不能为空（至少一行样例数据）")
    if len(samples) > _MAX_SAMPLE_ROWS:
        raise HTTPException(400, f"samples 最多 {_MAX_SAMPLE_ROWS} 行")
    for row in samples:
        if not isinstance(row, dict):
            raise HTTPException(400, "samples 每行必须是 JSON 对象（列名→值）")

    try:
        # #213: 同步 DB 插入放入线程池；start_draft_task 必须留在事件循环线程 create_task。
        draft = await run_in_threadpool(
            create_draft_placeholder,
            db, table_hint=payload.get("table_hint"), samples=samples, created_by=user.id,
        )
    except DraftError as e:
        raise HTTPException(400, str(e))

    start_draft_task(
        session_factory=SessionLocal,
        spec_id=draft.id,
        table_hint=payload.get("table_hint"),
        business_note=payload.get("business_note"),
        samples=samples,
        created_by=user.id,
    )
    logger.info("onboarding 起草已提交 draft_id=%s user=%s", draft.id, user.username)
    return R(data={"draft_id": draft.id})


@router.get("/drafts", response_model=R, summary="草稿列表（status/issue_no 筛选）")
def list_drafts(
    db: DBSession, user: CurrentUser,
    status_value: str | None = Query(
        None, alias="status",
        description="状态筛选: drafting/draft/failed/confirmed/applied/rejected",
    ),
    issue_no: str | None = Query(None, description="按落地工单号筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
):
    """草稿列表（admin 全量，其余仅本人创建），最新在前。"""
    stmt = select(DataSourceSpec)
    if not _is_admin(user):
        stmt = stmt.where(DataSourceSpec.created_by == user.id)
    if status_value:
        stmt = stmt.where(DataSourceSpec.status == status_value)
    if issue_no:
        stmt = stmt.where(DataSourceSpec.issue_no == issue_no)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(
        stmt.order_by(DataSourceSpec.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return R(data={"items": [_spec_dict(s) for s in rows], "total": total,
                   "page": page, "page_size": page_size})


@router.get("/registry-snapshot", response_model=R, summary="校验器对照的 registry 快照")
def registry_snapshot(db: DBSession, _: CurrentUser):
    """返回校验器对照物（现有数据源/投影/字段角色 + 规则常量），供前端提示冲突。

    前端用它做「边填边提示」：code 撞车、name_cn 重复、前缀不在白名单、分组非法，
    都在提交前就能看见，不必等 confirm 才被 fail 拦。
    """
    snap = build_registry_snapshot(db)
    return R(data={
        "sources": snap.sources,
        "projections": snap.projections,
        "field_roles": {k: sorted(v) for k, v in snap.field_roles.items()},
        "allowed_prefixes": list(_ALLOWED_PREFIXES),
        "valid_semantic_roles": sorted(VALID_SEMANTIC_ROLES),
        "category_order": list(CATEGORY_ORDER),
    })


@router.get("/drafts/{draft_id}", response_model=R, summary="查询草稿详情（轮询起草状态）")
def get_draft(draft_id: int, db: DBSession, user: CurrentUser):
    """返回草稿（status + 声明主体 + AI 产物 + 校验报告 + 落地包），用于轮询与详情展示。"""
    return R(data=_spec_dict(_load_visible(db, draft_id, user)))


@router.put("/drafts/{draft_id}", response_model=R, summary="人工编辑草稿（改后重跑校验）")
def update_draft(draft_id: int, payload: dict, db: DBSession, user: CurrentUser):
    """人工编辑声明主体字段；改后立即重跑校验器并写入报告。

    payload 为字段 subset（白名单 _EDITABLE_FIELDS）；起草中（drafting）与已落地
    （applied）不可编辑；已确认（confirmed）需先走「新建」而非改（确认即冻结）。
    """
    spec = _load_visible(db, draft_id, user)
    if spec.status in (SPEC_STATUS_DRAFTING, SPEC_STATUS_APPLIED):
        raise HTTPException(400, f"当前状态 '{spec.status}' 不可编辑")
    if spec.status == SPEC_STATUS_CONFIRMED:
        raise HTTPException(400, "已确认草稿不可编辑（确认即冻结，如需变更请新建草稿）")

    new_code = payload.get("code")
    if new_code and new_code != spec.code:
        # code 是 unique：改前先查重，避免落到 IntegrityError（500）
        clash = db.execute(
            select(DataSourceSpec.id).where(
                DataSourceSpec.code == new_code, DataSourceSpec.id != spec.id,
            )
        ).scalar()
        if clash is not None:
            raise HTTPException(400, f"code '{new_code}' 已被草稿 id={clash} 占用")

    for k in _EDITABLE_FIELDS:
        if k in payload and payload[k] is not None:
            setattr(spec, k, payload[k])
    report = rerun_validation(db, spec)
    logger.info("onboarding 草稿编辑 draft_id=%s user=%s fail=%s",
                spec.id, user.username, len(report.get("fail") or []))
    return R(data=_spec_dict(spec))


@router.post("/drafts/{draft_id}/confirm", response_model=R, summary="确认接入声明（admin）")
def confirm_draft(draft_id: int, payload: dict, db: DBSession, user: AdminUser):
    """重跑校验 → fail 清零 + warn 全处置 → status=confirmed。

    payload: {issue_no?, warn_resolutions?: {"<warn 下标>": "处置理由"}}
    warn 必须逐条备注理由（设计 §4.1：warn 需人工说明为何可放行，禁止静默通过）。
    """
    spec = db.get(DataSourceSpec, draft_id)
    if spec is None:
        raise HTTPException(404, "草稿不存在")
    try:
        spec = confirm_spec(
            db, spec, issue_no=payload.get("issue_no"),
            warn_resolutions=payload.get("warn_resolutions"), user_id=user.id,
        )
    except SpecFlowError as e:
        raise HTTPException(400, str(e))
    return R(data=_spec_dict(spec))


@router.post("/drafts/{draft_id}/apply", response_model=R,
             summary="生成落地包（admin，只产出配置不写库不改码）")
def apply_draft(draft_id: int, db: DBSession, user: AdminUser):
    """把已确认草稿转成落地包 JSON 并置 status=applied。

    落地包 = 迁移建议 + registry 种子 + 字段覆盖建议 + 缓存铁律 + 测试清单；
    本接口**不建表、不写 data_sources、不改任何代码文件**，执行由人工按
    issue-workflow 完成（设计 §1.3 非目标）。
    """
    spec = db.get(DataSourceSpec, draft_id)
    if spec is None:
        raise HTTPException(404, "草稿不存在")
    try:
        pkg = generate_apply_package(db, spec)
    except SpecFlowError as e:
        raise HTTPException(400, str(e))
    logger.info("onboarding 落地包 draft_id=%s user=%s", spec.id, user.username)
    return R(data={"id": spec.id, "code": spec.code, "status": spec.status,
                   "apply_package": pkg})
