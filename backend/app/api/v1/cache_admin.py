"""缓存管理 API - 管理员强制重建/失效看板缓存(admin only)

背景:
  缓存预热机制(cache_warmer)在同步后/启动后/空闲期自动填充缓存, 但预热
  WARM_TTL=180s, 极端情况看板数据可能滞后几分钟; 或缓存写入异常/手动改库
  后需立即刷新。本接口供管理员手动强制重建(失效+重算)或仅失效指定范围,
  确保数据及时刷新。

权限: 仅管理员(AdminUser)。所有操作记事务通知可追溯。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, DBSession
from app.schemas.common import R
from app.services.cache_warmer import (
    AVAILABLE_SCOPES,
    SCOPE_LABELS,
    is_warming,
    rebuild_cache,
)
from app.core.redis_client import get_redis

router = APIRouter(prefix="/system/cache", tags=["系统设置-缓存管理"])


class CacheRebuildReq(BaseModel):
    scope: str = Field(..., description="重建范围: today/week/reports/all")
    invalidate_only: bool = Field(
        False, description="True=仅失效不预热(下次访问自然回填), False=失效+强制重算预热"
    )


@router.get("/info", response_model=R, summary="缓存管理信息(可用范围+状态)")
def cache_info(db: DBSession, user: AdminUser):
    """返回可用重建范围、Redis 是否可用、当前是否在预热、同步是否进行中"""
    sync_busy = False
    try:
        from app.services.sync_task_manager import sync_task_manager
        sync_busy = sync_task_manager.is_busy()
    except Exception:
        pass
    return R(data={
        "scopes": [{"value": v, "label": SCOPE_LABELS.get(v, v)} for v in AVAILABLE_SCOPES],
        "redis_available": get_redis() is not None,
        "is_warming": is_warming(),
        "sync_busy": sync_busy,
    })


@router.post("/rebuild", response_model=R, summary="强制重建/失效缓存(admin only)")
def rebuild(payload: CacheRebuildReq, db: DBSession, user: AdminUser):
    """管理员强制重建指定范围缓存(先失效 pattern, 再强制重算预热)或仅失效。

    - scope 非法 → 400
    - 同步进行中 / 另一预热在跑(单飞锁) → 409(请稍后重试)
    - 成功 → 200 + 统计(失效条数/预热条数/耗时), 并记事务通知
    """
    if payload.scope not in AVAILABLE_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"未知范围: {payload.scope}, 可选: {AVAILABLE_SCOPES}",
        )
    result = rebuild_cache(payload.scope, invalidate_only=payload.invalidate_only)
    if not result.get("ok"):
        # 被拒(同步中/单飞锁) → 409 Conflict, 客户端可稍后重试
        raise HTTPException(
            status_code=409,
            detail=result.get("error", "重建被拒, 请稍后重试"),
        )
    # 成功 → 记事务通知可追溯
    op = "缓存失效" if payload.invalidate_only else "缓存重建"
    try:
        from app.services.system_event_service import record_system_event
        record_system_event(
            db,
            category="alert",
            action="cache_rebuild",
            title=op,
            summary=(
                f"管理员 {getattr(user, 'username', user.id)} {op} 范围={payload.scope}"
                f"({SCOPE_LABELS.get(payload.scope, payload.scope)})"
                f"{'[仅失效]' if payload.invalidate_only else ''}: "
                f"失效 {result.get('invalidated', 0)} 条 / "
                f"预热 {result.get('warmed', 0)} 条 / "
                f"耗时 {result.get('elapsed_ms', 0)}ms"
            ),
            status="success",
            detail=str(result),
        )
    except Exception:
        pass  # 通知写入失败不影响重建结果
    return R(data=result)
