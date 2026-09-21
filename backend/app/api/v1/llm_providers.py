"""LLM 多 Provider 管理路由 (工单 #108)

面向系统管理员: 增删改 LLM Provider(本地 Talon / 云端 DeepSeek 等),
测连通性, 查调用审计日志。

权限: AdminUser(改模型接入=高风险); 不依赖 LLM_ENABLED(可先配置后启用)。
"""

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import AdminUser, DBSession
from app.models.llm_provider import LlmCallLog
from app.schemas.common import R
from app.schemas.llm_provider import (
    LlmProviderCreate,
    LlmProviderUpdate,
    LlmCloudPolicyUpdate,
    LlmRoleMemberAdd,
    LlmRoleMemberOrder,
    LlmRoleUpdate,
    LlmTestConnRequest,
)
from app.services import llm_provider_service

router = APIRouter(prefix="/llm-providers", tags=["LLM 多 Provider 管理"])


@router.get("", response_model=R, summary="LLM Provider 列表")
def list_providers(db: DBSession, _: AdminUser):
    return R(data=llm_provider_service.list_providers(db))


@router.post("", response_model=R, summary="新建 LLM Provider")
def create_provider(payload: LlmProviderCreate, db: DBSession, me: AdminUser):
    return R(data=llm_provider_service.create_provider(db, payload, me.username))


@router.patch("/{provider_id}", response_model=R, summary="更新 LLM Provider")
def update_provider(provider_id: int, payload: LlmProviderUpdate, db: DBSession, me: AdminUser):
    return R(data=llm_provider_service.update_provider(db, provider_id, payload, me.username))


@router.delete("/{provider_id}", response_model=R, summary="删除 LLM Provider")
def delete_provider(provider_id: int, db: DBSession, _: AdminUser):
    ok = llm_provider_service.delete_provider(db, provider_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(404, "Provider 不存在")
    return R(data={"ok": True})


@router.post("/test-connection", response_model=R, summary="测试任意配置的连通性")
async def test_connection(payload: LlmTestConnRequest, _: AdminUser):
    """独立测任意 base_url/model/api_key, 不依赖 Provider id(便于新建前预检)。"""
    result = await llm_provider_service.test_connection(
        payload.base_url, payload.model, payload.api_key, payload.timeout_s,
    )
    return R(data=result)


@router.post("/{provider_id}/test", response_model=R, summary="用已存的 Provider 配置测连通性")
async def test_existing(provider_id: int, db: DBSession, _: AdminUser):
    """读 DB 中 Provider 配置测连通性, api_key 解密后传入(不入参不入日志)。"""
    from fastapi import HTTPException

    from app.core.crypto import decrypt_secret
    from app.models.llm_provider import LlmProvider
    row = db.get(LlmProvider, provider_id)
    if not row:
        raise HTTPException(404, "Provider 不存在")
    api_key = decrypt_secret(row.api_key_cipher) or ""
    result = await llm_provider_service.test_connection(
        row.base_url, row.model, api_key, timeout_s=30.0,
    )
    return R(data=result)


@router.post("/warmup", response_model=R, summary="手动触发模型预热(让模型常驻显存)")
async def warmup(_: AdminUser):
    """手动触发所有 enabled Provider 的最小 prompt, 触发模型加载到显存。

    启动后 30s 也会自动预热一次; 这里手动触发便于运维排障/重新加载。
    """
    from app.services.llm_warmup import warmup_all_providers
    result = await warmup_all_providers()
    return R(data=result)


@router.get("/call-logs", response_model=R, summary="LLM 调用审计日志(分页)")
def list_call_logs(
    db: DBSession,
    _: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
    provider_id: int | None = Query(None, description="按 Provider 过滤"),
    status: str | None = Query(None, description="success/failed/transient/permanent/timeout"),
    source: str | None = Query(None, description="调用来源(ai_analyze/llm_time_parse/...)"),
):
    """分页查询调用审计日志, 不返回 messages/响应正文(本来就没存)。"""
    from sqlalchemy import func

    q = select(LlmCallLog).order_by(LlmCallLog.id.desc())
    if provider_id is not None:
        q = q.where(LlmCallLog.provider_id == provider_id)
    if status:
        q = q.where(LlmCallLog.status == status)
    if source:
        q = q.where(LlmCallLog.source == source)
    # total: 复用同样 where 条件
    count_q = select(func.count()).select_from(LlmCallLog)
    if provider_id is not None:
        count_q = count_q.where(LlmCallLog.provider_id == provider_id)
    if status:
        count_q = count_q.where(LlmCallLog.status == status)
    if source:
        count_q = count_q.where(LlmCallLog.source == source)
    total = db.scalar(count_q) or 0
    rows = db.scalars(q.limit(page_size).offset((page - 1) * page_size)).all()
    return R(data={
        "items": [
            {
                "id": r.id, "provider_id": r.provider_id, "provider_code": r.provider_code,
                "model": r.model, "status": r.status, "status_code": r.status_code,
                "duration_ms": r.duration_ms, "prompt_chars": r.prompt_chars,
                "out_chars": r.out_chars, "error_message": r.error_message,
                "source": r.source, "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    })


# ─────────────────────────────────────────────────────────────────────
# 角色（能力档位）与出域策略（2026-09-21）
# 见 docs/design/20260921-模型角色分组与出域管控设计.md
# ─────────────────────────────────────────────────────────────────────
@router.get("/roles", response_model=R, summary="角色列表（含成员与生效链）")
def list_roles(db: DBSession, _: AdminUser):
    """按「助理/专员/专家」分区展示，每区含成员（按 role_order）与实际生效调用链。"""
    return R(data=llm_provider_service.list_roles(db))


@router.put("/roles/{role_key}", response_model=R, summary="更新角色")
def update_role(role_key: str, payload: LlmRoleUpdate, db: DBSession, me: AdminUser):
    """更新角色开关/是否允许出域/文案。成员顺序改 provider 的 role_order。"""
    return R(data=llm_provider_service.update_role(
        db, role_key, payload.model_dump(exclude_unset=True), me.username))


@router.get("/cloud-policy", response_model=R, summary="读全局出域策略")
def get_cloud_policy(db: DBSession, _: AdminUser):
    return R(data=llm_provider_service.get_cloud_policy(db))


# ── 角色成员（关联表，支持一模型多角色）────────────────────────────────
# 2026-09-21: 原用 llm_providers.role_scope 单值列，实测证伪（coder-30b
# 需同属 assistant 与 specialist）。改为关联表后，同一 provider 在不同
# 角色中的顺序互相独立。
@router.post("/roles/{role_key}/members", response_model=R, summary="把 Provider 加入角色")
def add_role_member(role_key: str, payload: LlmRoleMemberAdd, db: DBSession, me: AdminUser):
    return R(data=llm_provider_service.add_role_member(
        db, role_key, payload.provider_code, payload.sort_order or 100, me.username))


@router.delete("/roles/{role_key}/members/{provider_code}", response_model=R,
               summary="把 Provider 移出角色（不删 Provider）")
def remove_role_member(role_key: str, provider_code: str, db: DBSession, _: AdminUser):
    ok = llm_provider_service.remove_role_member(db, role_key, provider_code)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(404, "该角色中不存在此成员")
    return R(data={"ok": True})


@router.patch("/roles/{role_key}/members/{provider_code}", response_model=R,
              summary="调整成员在本角色内的顺序")
def set_role_member_order(role_key: str, provider_code: str, payload: LlmRoleMemberOrder,
                          db: DBSession, me: AdminUser):
    ok = llm_provider_service.set_role_member_order(
        db, role_key, provider_code, payload.sort_order, me.username)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(404, "该角色中不存在此成员")
    return R(data={"ok": True})


@router.put("/cloud-policy", response_model=R, summary="更新全局出域策略")
def update_cloud_policy(payload: LlmCloudPolicyUpdate, db: DBSession, me: AdminUser):
    """⚠️ 数据边界开关。改为 auto 意味着本地失败会自动出网（可能发往公网）。

    保存后立即失效进程缓存（不设 30s 窗口期）。
    """
    return R(data=llm_provider_service.update_cloud_policy(
        db, payload.policy, me.username, payload.require_confirm))
