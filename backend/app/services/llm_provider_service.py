"""LLM 多 Provider - 服务层 (工单 #108)

职责:
  1. CRUD: 管理端 AdminUser 调用; API key Fernet 加密入库
  2. 健康探测: /test-connection 独立测任意配置
  3. 调用日志: 每次调用一行审计(成功/失败均记)
  4. Provider 选择: 按 priority 升序取 enabled+healthy, 供 llm_client 调用
  5. 失败计数: 连续失败 N 次自动标 unhealthy; 成功一次清零

设计:
- 库为空时 llm_client 回退 settings.LLM_* 单源模式(向后兼容)
- API key 明文绝不出现在响应中(同 ExternalAccessToken 模式)
- 调用日志只记状态/耗时/字符数, 不打 messages/响应正文(防泄露)
"""

import logging
import time
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.llm_provider import LlmCallLog, LlmProvider
from app.schemas.llm_provider import (
    LlmProviderCreate,
    LlmProviderUpdate,
    LlmTestConnResult,
)

logger = logging.getLogger(__name__)

# 进程内 Provider 缓存: 避免每次 chat_completion 都查库; 写操作显式失效
# key='active' → (ts, list[LlmProvider 字典])
_PROVIDER_CACHE: dict[str, tuple[float, list[dict]]] = {}
_PROVIDER_CACHE_TTL_S = 30  # 30s 进程缓存


# ─────────────────────────────────────────────────────────────────────
# Provider 选择(供 llm_client 调用)
# ─────────────────────────────────────────────────────────────────────
def get_active_providers(db: Session) -> list[dict]:
    """取启用且健康的 Provider 列表, 按 priority 升序。

    返回 dict 列表(已解密 api_key), 含字段:
      id/code/name/base_url/model/api_key/thinking/timeout_s/retry_max/
      fail_threshold/fail_count

    库为空或全 disabled → 返回空列表(由 llm_client 回退 .env 单源)。
    """
    now = time.time()
    hit = _PROVIDER_CACHE.get("active")
    if hit and now - hit[0] < _PROVIDER_CACHE_TTL_S:
        return hit[1]
    rows = db.scalars(
        select(LlmProvider)
        .where(LlmProvider.enabled.is_(True), LlmProvider.healthy.is_(True))
        .order_by(LlmProvider.priority, LlmProvider.id)
    ).all()
    data = []
    for r in rows:
        data.append({
            "id": r.id, "code": r.code, "name": r.name,
            "base_url": r.base_url, "model": r.model,
            "api_key": decrypt_secret(r.api_key_cipher) or "",
            "thinking": r.thinking, "timeout_s": float(r.timeout_s),
            "retry_max": int(r.retry_max), "fail_threshold": int(r.fail_threshold),
            "fail_count": int(r.fail_count),
        })
    _PROVIDER_CACHE["active"] = (now, data)
    return data


def invalidate_cache() -> None:
    """管理端写操作后调用, 让下次调用立即用新配置。"""
    _PROVIDER_CACHE.pop("active", None)


# ─────────────────────────────────────────────────────────────────────
# 调用日志(成功/失败均记)
# ─────────────────────────────────────────────────────────────────────
def record_call_log(
    db: Session,
    *,
    provider_id: int | None,
    provider_code: str | None,
    model: str | None,
    status: str,
    status_code: int | None,
    duration_ms: int,
    prompt_chars: int | None = None,
    out_chars: int | None = None,
    error_message: str | None = None,
    source: str | None = None,
) -> None:
    """写一行调用审计日志; 不打 messages/响应正文(防泄露)。

    错误信息脱敏: 不含 API key / 响应正文, 仅异常类名+短描述。
    """
    # 脱敏: 截断错误信息长度, 避免超长堆栈占库
    err = (error_message or "").strip()
    if len(err) > 500:
        err = err[:500] + "...[truncated]"
    try:
        log = LlmCallLog(
            provider_id=provider_id, provider_code=provider_code, model=model,
            status=status, status_code=status_code, duration_ms=duration_ms,
            prompt_chars=prompt_chars, out_chars=out_chars,
            error_message=err or None, source=source,
        )
        db.add(log)
        db.commit()
    except Exception as e:  # noqa: BLE001  审计日志写失败不能阻塞业务
        logger.warning("llm call log write failed: %s", e)
        db.rollback()


# ─────────────────────────────────────────────────────────────────────
# 失败计数 / 健康状态维护
# ─────────────────────────────────────────────────────────────────────
def mark_provider_failed(db: Session, provider_id: int, *, fail_threshold: int) -> None:
    """连续失败计数+1; 达阈值标 unhealthy(降级)。

    healthy=false 后 llm_client 不再选这个 Provider, 由运维在管理界面
    手动恢复(healthy=true)或自动健康探测恢复(后续可加调度任务)。
    """
    try:
        # 用 UPDATE 语句原子操作: fail_count+1 + last_fail_at
        db.execute(
            update(LlmProvider)
            .where(LlmProvider.id == provider_id)
            .values(
                fail_count=LlmProvider.fail_count + 1,
                last_fail_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("llm mark_provider_failed update failed: %s", e)
        db.rollback()
        return
    # 再读一次判断是否触发降级
    row = db.get(LlmProvider, provider_id)
    if row and row.fail_count >= fail_threshold and row.healthy:
        try:
            row.healthy = False
            db.commit()
            logger.warning("llm provider %s(%s) unhealthy: fail_count=%s >= threshold=%s",
                           row.id, row.code, row.fail_count, fail_threshold)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.warning("llm provider unhealthy flag update failed: %s", e)
        invalidate_cache()


def mark_provider_succeeded(db: Session, provider_id: int) -> None:
    """成功一次清零 fail_count + last_success_at + healthy=true(自愈)。"""
    try:
        db.execute(
            update(LlmProvider)
            .where(LlmProvider.id == provider_id)
            .values(
                fail_count=0,
                last_success_at=datetime.now(timezone.utc),
                healthy=True,
            )
        )
        db.commit()
        invalidate_cache()
    except Exception as e:  # noqa: BLE001
        logger.warning("llm mark_provider_succeeded update failed: %s", e)
        db.rollback()


# ─────────────────────────────────────────────────────────────────────
# 测试连接(独立测任意配置, 不依赖 Provider id)
# ─────────────────────────────────────────────────────────────────────
async def test_connection(
    base_url: str, model: str, api_key: str | None, timeout_s: float = 30.0,
) -> LlmTestConnResult:
    """发送一个最小 prompt, 验证端到端连通性 + 模型可用性。

    成功返回样例输出(脱敏截断); 失败返回错误类名(不含响应正文)。
    """
    t0 = time.perf_counter()
    url = (base_url or "").rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # 最小 prompt: 一句问候, 避免触发长生成
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "stream": False,
        "temperature": 0,
    }
    timeout = httpx.Timeout(connect=8.0, read=timeout_s, write=10.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as e:
        return LlmTestConnResult(
            ok=False, status_code=None,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            error=f"超时: {e.__class__.__name__}",
        )
    except httpx.HTTPError as e:
        return LlmTestConnResult(
            ok=False, status_code=None,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            error=f"连接异常: {e.__class__.__name__}",
        )
    duration_ms = int((time.perf_counter() - t0) * 1000)
    if resp.status_code != 200:
        return LlmTestConnResult(
            ok=False, status_code=resp.status_code, duration_ms=duration_ms,
            error=f"HTTP {resp.status_code}",
        )
    try:
        data = resp.json()
        sample = (data.get("choices", [{}])[0].get("message", {}).get("content") or "")[:60]
    except Exception:
        sample = ""
    return LlmTestConnResult(
        ok=True, status_code=200, duration_ms=duration_ms,
        sample=sample or "(空响应)",
    )


# ─────────────────────────────────────────────────────────────────────
# CRUD(管理端 AdminUser)
# ─────────────────────────────────────────────────────────────────────
def _to_out(row: LlmProvider) -> dict:
    """ORM → dict(脱敏: 不含 api_key 明文, 仅 prefix + 是否已配)"""
    return {
        "id": row.id, "code": row.code, "name": row.name,
        "base_url": row.base_url, "model": row.model,
        "api_key_prefix": row.api_key_prefix,
        "api_key_configured": bool(row.api_key_cipher),
        "priority": row.priority, "enabled": row.enabled, "healthy": row.healthy,
        "thinking": row.thinking, "timeout_s": float(row.timeout_s),
        "retry_max": int(row.retry_max), "fail_threshold": int(row.fail_threshold),
        "fail_count": int(row.fail_count),
        "last_fail_at": row.last_fail_at, "last_success_at": row.last_success_at,
        "note": row.note, "created_by": row.created_by, "updated_by": row.updated_by,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def list_providers(db: Session) -> list[dict]:
    rows = db.scalars(select(LlmProvider).order_by(LlmProvider.priority, LlmProvider.id)).all()
    return [_to_out(r) for r in rows]


def create_provider(db: Session, payload: LlmProviderCreate, username: str) -> dict:
    exists = db.scalar(select(LlmProvider).where(LlmProvider.code == payload.code))
    if exists:
        raise HTTPException(400, f"code 已存在: {payload.code}")
    key_cipher = encrypt_secret(payload.api_key) if payload.api_key else None
    key_prefix = (payload.api_key or "")[:6] or None
    row = LlmProvider(
        code=payload.code[:64], name=payload.name[:128],
        base_url=payload.base_url[:256], model=payload.model[:128],
        api_key_cipher=key_cipher, api_key_prefix=key_prefix,
        priority=payload.priority, enabled=payload.enabled,
        thinking=payload.thinking, timeout_s=payload.timeout_s,
        retry_max=payload.retry_max, fail_threshold=payload.fail_threshold,
        note=payload.note, created_by=(username or "")[:64], updated_by=(username or "")[:64],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_cache()
    return _to_out(row)


def update_provider(db: Session, provider_id: int, payload: LlmProviderUpdate, username: str) -> dict:
    row = db.get(LlmProvider, provider_id)
    if not row:
        raise HTTPException(404, "Provider 不存在")
    upd = payload.model_dump(exclude_unset=True)
    if "code" in upd:
        raise HTTPException(400, "code 不可修改(唯一标识)")
    # api_key 特殊处理: 传值=重置; 留空=不改
    new_key = upd.pop("api_key", None)
    if new_key is not None:
        row.api_key_cipher = encrypt_secret(new_key) if new_key else None
        row.api_key_prefix = (new_key or "")[:6] or None
    for field in ("name", "base_url", "model", "priority", "enabled", "healthy",
                  "thinking", "timeout_s", "retry_max", "fail_threshold", "note"):
        if field in upd and upd[field] is not None:
            setattr(row, field, upd[field])
    row.updated_by = (username or "")[:64]
    db.commit()
    db.refresh(row)
    invalidate_cache()
    return _to_out(row)


def delete_provider(db: Session, provider_id: int) -> bool:
    row = db.get(LlmProvider, provider_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    invalidate_cache()
    return True
