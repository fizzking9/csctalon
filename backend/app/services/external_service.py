"""外部只读开放平台 - 服务层

对应 docs/arch/A-20260902-021-外部工具只读开放平台.md (LOCKED v1.0)。

职责:
  1. token 生成/校验(库中存 SHA-256 hash + prefix, 常量时间比较)
  2. scope 校验
  3. Redis 固定窗口限频(每 token rate_limit_per_min 或全局默认)
  4. 只读数据组装(reception / refund / complaint / brief), 全部复用既有聚合,
     不重复实现统计口径。

只读原则: 本模块对业务表零写入; 仅更新 token.last_used_at + 可选的 system_events 审计。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import logging
import secrets
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis_client import get_redis
from app.models.external_token import ExternalAccessToken

logger = logging.getLogger(__name__)

# 可用 scope 集合(一期, 与架构一致; 后续绩效/人员等在此扩展)
# #237(B2-2): 新增实体维度四类 — device 单设备行为/ recharge 充值流量/
# device_tag 设备标签/ schedule 排班考勤区间。
SCOPES = ("reception", "refund", "complaint", "brief",
          "device", "recharge", "device_tag", "schedule")

# 北京时区
TZ_CN = _dt.timezone(_dt.timedelta(hours=8))


def _hash_token(token: str) -> str:
    """SHA-256 hexdigest(不存明文; 常量时间比较在 hmac.compare_digest)"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_bj_window(start_s: str | None, end_s: str | None) -> tuple[datetime | None, datetime | None]:
    """北京时区窗口 → naive UTC 边界(供 timestamptz 列比较)。

    与 analytics/complaints 的 _parse_dt/_parse_dt_end 语义一致:
      start 纯日期 YYYY-MM-DD → 北京 00:00 → naive UTC
      end   纯日期 YYYY-MM-DD → 北京 23:59:59.999999 → naive UTC
    """
    def _parse_dt(s: str | None, end_of_day: bool = False) -> datetime | None:
        if not s:
            return None
        s = s.strip()
        try:
            if len(s) == 10:
                d = datetime.strptime(s, "%Y-%m-%d")
                if end_of_day:
                    dt_sh = d.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=TZ_CN)
                else:
                    dt_sh = d.replace(tzinfo=TZ_CN)
                return dt_sh.astimezone(_dt.timezone.utc).replace(tzinfo=None)
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ_CN)
            return dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None

    return _parse_dt(start_s, end_of_day=False), _parse_dt(end_s, end_of_day=True)


def _now_cn() -> datetime:
    return datetime.now(TZ_CN)


# ─────────────────────────────────────────────────────────────────────
# Token 生成 / 校验 / 吊销
# ─────────────────────────────────────────────────────────────────────
def generate_token(db: Session, name: str, scopes: list[str], *,
                   rate_limit_per_min: int | None = None,
                   expires_at: datetime | None = None,
                   owner_note: str | None = None,
                   created_by: str | None = None) -> tuple[str, ExternalAccessToken]:
    """生成新 token: 明文仅本次返回; 库中存 hash + prefix。

    Returns:
        (raw_token, token_row)  — raw_token 仅此一次展示, 调用方负责分发给外部工具。
    """
    invalid = [s for s in scopes if s not in SCOPES]
    if invalid:
        raise HTTPException(400, f"非法 scope: {', '.join(invalid)}; 允许: {', '.join(SCOPES)}")
    raw = secrets.token_urlsafe(32)
    row = ExternalAccessToken(
        name=name[:64],
        token_hash=_hash_token(raw),
        token_prefix=raw[:8],
        scopes=list(dict.fromkeys(scopes)),  # 去重保序
        rate_limit_per_min=rate_limit_per_min,
        expires_at=expires_at,
        owner_note=(owner_note or "")[:255],
        enabled=True,
        created_by=(created_by or "")[:64],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return raw, row


def revoke_token(db: Session, token_id: int, revoked_by: str | None = None) -> bool:
    """吊销(软删 enabled=false); 不存在返回 False"""
    row = db.get(ExternalAccessToken, token_id)
    if not row:
        return False
    row.enabled = False
    row.revoked_by = (revoked_by or "")[:64]
    db.commit()
    return True


def enable_token(db: Session, token_id: int, restored_by: str | None = None) -> ExternalAccessToken | None:
    """恢复已吊销 token(enabled=true); 不存在返回 None。

    A-20260910-003 (#43) v1.3 增量:
      - 把吊销从「终态」改为「可逆状态」, 误吊销无需重建 token + 重新分发。
      - 幂等: 已启用的 token 再调用不报错, 状态保持不变。
      - `revoked_by` 清空(当前态为「启用」, 恢复人写入 operation_logs 审计链)。
    """
    row = db.get(ExternalAccessToken, token_id)
    if not row:
        return None
    row.enabled = True
    # 清空吊销人: 该字段语义是"当前处于吊销态的吊销者", 恢复后不应残留
    row.revoked_by = None
    db.commit()
    db.refresh(row)
    logger.info("[external] token 恢复启用: id=%s name=%s by=%s", row.id, row.name, restored_by or "-")
    return row


def rotate_token(db: Session, token_id: int) -> tuple[str, ExternalAccessToken] | None:
    """重新生成明文(**覆盖式**, 非回看): 旧 token 立即失效。

    A-20260910-003 (#43) v1.3: 明文"仅显示一次"是安全底线(库中只存 hash),
    唯一合法的补救路径不是"回看历史明文", 而是"换一把新锁"。
    返回 (新明文, 行对象); 不存在返回 None。
    """
    row = db.get(ExternalAccessToken, token_id)
    if not row:
        return None
    raw = secrets.token_urlsafe(32)
    row.token_hash = _hash_token(raw)
    row.token_prefix = raw[:8]
    db.commit()
    db.refresh(row)
    logger.info("[external] token 换钥(旧明文立即失效): id=%s name=%s", row.id, row.name)
    return raw, row


def find_token_by_hash(db: Session, token_hash: str) -> ExternalAccessToken | None:
    return db.scalar(select(ExternalAccessToken).where(ExternalAccessToken.token_hash == token_hash))


def authenticate(db: Session, raw_token: str | None) -> ExternalAccessToken:
    """校验 Bearer token: 无效/吊销/过期 → 401; 功能未启用 → 503。

    Raises:
        HTTPException 503(未启用)/ 401(无效)
    """
    if not settings.EXTERNAL_API_ENABLED:
        raise HTTPException(503, "外部只读接口未启用(EXTERNAL_API_ENABLED=false)")
    if not raw_token:
        raise HTTPException(401, "未提供认证令牌")
    row = find_token_by_hash(db, _hash_token(raw_token))
    if not row or not hmac.compare_digest(row.token_hash, _hash_token(raw_token)):
        raise HTTPException(401, "令牌无效")
    if not row.enabled:
        raise HTTPException(401, "令牌已吊销")
    if row.expires_at is not None and row.expires_at <= _now_cn():
        raise HTTPException(401, "令牌已过期")
    return row


def require_scope(row: ExternalAccessToken, scope: str) -> None:
    """token 无该 scope → 403"""
    if scope not in (row.scopes or []):
        raise HTTPException(403, f"令牌无 {scope} 权限范围")


def touch_last_used(db: Session, row: ExternalAccessToken) -> None:
    """轻量更新 last_used_at(每 token 每分钟至多写一次, 避免高频写库)"""
    now = _now_cn()
    # 60s 内已更新过则跳过(限频已是分钟级, 这里进一步降写频)
    if row.last_used_at is not None and (now - row.last_used_at).total_seconds() < 60:
        return
    row.last_used_at = now
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


# ─────────────────────────────────────────────────────────────────────
# Redis 固定窗口限频
# ─────────────────────────────────────────────────────────────────────
def _is_unlimited(row: ExternalAccessToken) -> bool:
    """token 显式 rate_limit_per_min=0 → 不限制(免除限频)。

    v1.2 三值语义: 0=不限制 / NULL=用全局默认 / >0=个性化。
    """
    return row.rate_limit_per_min == 0


def _token_quota(row: ExternalAccessToken) -> int:
    """返回该 token 的限频阈值(次/分钟)。

    仅处理 >0(个性化) 与 NULL(全局默认); 0=不限制由 check_rate_limit 提前短路。
    """
    if row.rate_limit_per_min is not None and row.rate_limit_per_min > 0:
        return row.rate_limit_per_min
    return settings.EXTERNAL_RATE_LIMIT_DEFAULT_PER_MIN or 60


def check_rate_limit(row: ExternalAccessToken) -> None:
    """固定窗口: rate:ext:{token_hash 前16}:{YYYYMMDDHHmm} → INCR, 首次 EXPIRE 60。

    v1.2: rate_limit_per_min=0 → 不限(直接 return, 不做 INCR/计数)。
    Redis 不可用 → 放行(降级, 不阻塞只读业务)。
    """
    if _is_unlimited(row):
        return
    r = get_redis()
    if r is None:
        return
    key = f"rate:ext:{row.token_hash[:16]}:{_now_cn().strftime('%Y%m%d%H%M')}"
    try:
        cnt = r.incr(key)
        if cnt == 1:
            r.expire(key, 60)
        if cnt > _token_quota(row):
            raise HTTPException(429, "请求过于频繁, 已达频率限制")
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001  Redis 异常降级放行
        logger.debug("rate_limit incr 失败, 降级放行")


# ─────────────────────────────────────────────────────────────────────
# 审计(复用 system_events, 低频记录外部拉取)
# ─────────────────────────────────────────────────────────────────────
def record_external_access(db: Session, row: ExternalAccessToken, endpoint: str,
                           status: str = "success") -> None:
    """每次外部拉取写一条 system_event(category=external, 低频可接受)。

    仅在开启时记录, 写入失败不影响主流程(system_event_service 内部已兜底)。
    """
    try:
        from app.services.system_event_service import record_system_event

        record_system_event(
            db,
            category="external",
            action="external_read",
            title=f"外部拉取: {row.name} → {endpoint}",
            summary=f"调用方: {row.name}",
            status=status,
            commit=True,
        )
    except Exception:  # noqa: BLE001
        logger.debug("external access 审计写入失败(忽略)")
