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
def _load_active_providers(db: Session) -> list[dict]:
    """取启用且健康的全部 Provider(含 pregen_only), 按 priority 升序, 带 30s 进程缓存。

    #131: 缓存保留 pregen_only 行, 由 get_active_providers/get_provider_chain 各自过滤。
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
            "fail_count": int(r.fail_count), "pregen_only": bool(r.pregen_only),
            # 出域标记（2026-09-21）: 供 get_provider_chain 按策略过滤；
            # 兼容旧库（列可能尚未迁移）用 getattr 兜底
            "is_cloud": bool(getattr(r, "is_cloud", False)),
            "role_scope": getattr(r, "role_scope", None),
            "role_order": int(getattr(r, "role_order", 100) or 100),
        })
    _PROVIDER_CACHE["active"] = (now, data)
    return data


def get_active_providers(db: Session) -> list[dict]:
    """实时默认链: 启用+健康且 **非 pregen_only** 的 Provider, 按 priority 升序。

    用于用户实时问答 / 时间解析等同步路径。pregen_only(如 122b 慢模型)
    对这些调用不可见, 避免实时请求被拖到分钟级(#131)。

    返回 dict 列表(已解密 api_key), 含字段:
      id/code/name/base_url/model/api_key/thinking/timeout_s/retry_max/
      fail_threshold/fail_count/pregen_only

    库为空或全 disabled → 返回空列表(由 llm_client 回退 .env 单源)。
    """
    return [p for p in _load_active_providers(db) if not p.get("pregen_only")]


def get_provider_chain(db: Session, provider_code: "str | list[str] | None" = None) -> list[dict]:
    """构造本次调用的 Provider 链(#131; 2026-09-21 支持能力档位链)。

    - provider_code 为空: 返回实时默认链(排除 pregen_only)。
    - provider_code 为 str(报告预生成按模板指定):
      * 命中 enabled+healthy 的指定 provider → 置顶, 其后接常规链作 transient 降级兜底;
      * 未命中/已停用/不健康 → 退回常规链(预生成不因慢模型暂时不可用而硬失败)。
    - provider_code 为 list(能力档位: 首选 → 备选 → 云端兜底, 见 llm_tiers):
      * 按列表顺序取出所有可用者置顶(保持声明顺序), 其后接常规链兜底;
      * 列表内不可用者逐个跳过并记日志, 不因个别缺失而放弃整条链。

    顺序: [指定的可用 provider(按给定顺序)] + [其余非 pregen_only provider, priority 升序]。

    向后兼容: 单字符串行为与旧实现完全一致。
    """
    full = _load_active_providers(db)
    normal = [p for p in full if not p.get("pregen_only")]
    if not provider_code:
        # 默认链（用户实时问答等未指定档位时）同样受出域策略约束
        return _apply_cloud_filter(normal, db)

    # 归一为列表（单字符串 → [字符串]），两种调用方式共用后续逻辑
    codes = [provider_code] if isinstance(provider_code, str) else list(provider_code)
    codes = [c for c in codes if c]
    if not codes:
        return normal

    by_code = {p["code"]: p for p in full}
    designated: list[dict] = []
    for c in codes:
        p = by_code.get(c)
        if p is None:
            logger.warning("provider_code=%s 未启用/不存在/不健康, 跳过", c)
            continue
        if all(p["id"] != d["id"] for d in designated):
            designated.append(p)

    if not designated:
        # 全部缺失（如档位声明的模型都没启用）→ 退回常规链, 保证不硬失败
        logger.warning("provider 链 %s 全部不可用, 退回常规链", codes)
        return _apply_cloud_filter(normal, db)

    picked_ids = {d["id"] for d in designated}
    tail = [p for p in normal if p["id"] not in picked_ids]
    return designated + _apply_cloud_filter(tail, db)


def _apply_cloud_filter(providers: list[dict], db: Session) -> list[dict]:
    """按全局出域策略过滤「常规链尾巴」中的云端 provider。

    ⚠️ 为什么需要这道闸门（2026-09-21 补漏）:
      `llm_tiers.resolve_tier()` 已过滤了**档位自己声明的**云端兜底，
      但 `get_provider_chain` 还会把「其余 enabled+healthy 的 provider」
      按 priority 追加到链尾 —— 那里同样可能含云端（如 deepseek priority=30）。
      若只改前者，本地模型失败时仍会**静默出网**。故在此统一收口。

    策略语义见 `llm_tiers.cloud_policy()` / `cloud_allowed()`。
    注意: provider 的 `is_cloud` 字段是权威判据（由迁移按 base_url 回填），
    不再依赖 code 硬编码比对。
    """
    from app.services import llm_tiers

    if llm_tiers.cloud_policy() == llm_tiers.CLOUD_POLICY_AUTO:
        return providers          # auto: 保留云端作为兜底（旧行为）
    # never / explicit_only 均不把云端放进「自动降级」路径 ——
    # 区别在于 never 连显式指定也不允许（由 resolve_tier 与调用方保证）
    kept = [p for p in providers if not p.get("is_cloud")]
    dropped = len(providers) - len(kept)
    if dropped:
        logger.info(
            "出域策略=%s: 从常规链尾部移除 %d 个云端 provider（数据不出域）",
            llm_tiers.cloud_policy(), dropped)
    return kept


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
        "priority": row.priority, "enabled": row.enabled,
        "pregen_only": bool(row.pregen_only),
        "healthy": row.healthy,
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
        pregen_only=payload.pregen_only,
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
    for field in ("name", "base_url", "model", "priority", "enabled", "pregen_only",
                  "healthy", "thinking", "timeout_s", "retry_max", "fail_threshold", "note",
                  # 2026-09-21 出域管控 + 角色归属（界面可改）
                  "is_cloud", "role_scope", "role_order"):
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


# ─────────────────────────────────────────────────────────────────────
# 角色（能力档位）管理（2026-09-21 B2/B3）
# ─────────────────────────────────────────────────────────────────────
# 见 docs/design/20260921-模型角色分组与出域管控设计.md
def list_roles(db: Session) -> list[dict]:
    """角色列表 + 各角色成员（按 role_order 排序）+ 实际生效的调用链。

    供管理界面按「助理/专员/专家」分区展示。
    """
    from app.models.llm_role_config import LlmRoleConfig
    from app.services import llm_tiers

    roles = db.query(LlmRoleConfig).order_by(LlmRoleConfig.tier_order).all()

    # 成员：role_scope 命中的 provider（含禁用的，界面需看到以便启用）
    members: dict[str, list] = {}
    for p in db.query(LlmProvider).order_by(LlmProvider.role_order, LlmProvider.priority).all():
        if p.role_scope:
            members.setdefault(p.role_scope, []).append(p)

    out = []
    for r in roles:
        rows = members.get(r.role_key, [])
        out.append({
            "role_key": r.role_key,
            "display_name": r.display_name,
            "description": r.description,
            "rationale": r.rationale,
            "enabled": r.enabled,
            "allow_cloud": r.allow_cloud,
            "tier_order": r.tier_order,
            "members": [{
                "id": p.id, "code": p.code, "name": p.name, "model": p.model,
                "is_cloud": bool(p.is_cloud), "enabled": p.enabled, "healthy": p.healthy,
                "role_order": p.role_order, "priority": p.priority,
            } for p in rows],
            # 实际生效链（含 DB/常量回退与云端过滤），供界面显示"当前会用谁"
            "effective_chain": llm_tiers.resolve_tier(r.role_key, db=db),
        })
    return out


def update_role(db: Session, role_key: str, payload: dict, username: str) -> dict:
    """更新角色定义（开关/是否允许出域/文案）。成员顺序通过 update_provider 调整。"""
    from fastapi import HTTPException

    from app.models.llm_role_config import LlmRoleConfig

    row = db.query(LlmRoleConfig).filter(LlmRoleConfig.role_key == role_key).first()
    if not row:
        raise HTTPException(404, f"角色不存在: {role_key}")

    for field in ("display_name", "description", "rationale",
                  "enabled", "allow_cloud", "tier_order"):
        if field in payload and payload[field] is not None:
            setattr(row, field, payload[field])
    row.updated_by = (username or "")[:64]
    db.commit()
    db.refresh(row)
    invalidate_cache()
    return {
        "role_key": row.role_key, "display_name": row.display_name,
        "enabled": row.enabled, "allow_cloud": row.allow_cloud,
    }


def get_cloud_policy() -> dict:
    """读全局出域策略 + 受约束的云端 provider 清单（供界面展示与警示）。"""
    from app.services import llm_tiers

    return {
        "policy": llm_tiers.cloud_policy(),
        "options": [
            {"value": "never", "label": "禁止出域",
             "desc": "云端完全不可用（数据绝对不出内网）"},
            {"value": "explicit_only", "label": "仅显式允许",
             "desc": "仅当用户/调用方明确要求时才用云端（推荐）"},
            {"value": "auto", "label": "自动降级",
             "desc": "本地失败时自动降级到云端（⚠️ 可能静默出域）"},
        ],
        "cloud_providers": _list_cloud_providers(),
    }


def _list_cloud_providers() -> list[dict]:
    """列出标记为出域的 provider（供界面警示）。"""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        rows = db.query(LlmProvider).filter(LlmProvider.is_cloud.is_(True)).all()
        return [{"code": r.code, "model": r.model, "enabled": r.enabled} for r in rows]
    finally:
        db.close()
