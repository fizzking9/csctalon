"""跨子域 SSO 验票（netmgr 面板 → 本系统，反向于 sso_ticket.py）

方向：**本系统是消费方**，netmgr 面板（panel.kimirock.top）是身份源。
  面板 GET /sso/csctalon 签发 60 秒一次性 HMAC 票据 → 302 到本系统 /api/v1/auth/sso
  → 本模块验票 → 返回 sub（=面板固定映射的用户名）

票据格式（与 netmgr src/admin-panel/routes/sso.js 逐字符对齐，改一处必须改两处）：
    base64url(payload) + "." + base64url(HMAC-SHA256(secret, payload))
    payload = {"sub": 用户名, "exp": 秒级时间戳, "jti": 16 位 hex}

安全约束：
  - 60 秒过期（容忍 ±540 秒时钟偏差，防 exp 异常值）
  - 常量时间比较签名，防计时侧信道
  - 密钥只从 settings 读，禁止硬编码
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings

# 票据有效期（秒），须与面板侧 TTL_SECONDS 一致
TICKET_TTL_SECONDS = 60
# 容忍时钟偏差上限：签发后最多 10 分钟内有效（与面板 verifyTicket 的 exp-out-of-range 对齐）
_MAX_SKEW_SECONDS = 540


def _b64url_decode(s: str) -> bytes:
    """base64url 解码（自动补填充）。"""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_ticket(ticket: str) -> str | None:
    """验票。返回 sub 或 None（坏票/过期/签名不符/未配置密钥）。

    常量时间比较签名，防计时侧信道。
    """
    secret = settings.NETMGR_SSO_SECRET
    if not secret:
        return None
    if not isinstance(ticket, str) or len(ticket) < 10 or len(ticket) > 2048:
        return None
    try:
        payload_b64, sig_b64 = ticket.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        got = _b64url_decode(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected, got):
        return None
    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("exp"), int):
        return None
    now = int(time.time())
    if claims["exp"] < now:
        return None
    # 容忍时钟偏差：签发时间(exp - TTL)不能比 now + skew 还晚（防 exp 异常远未来值）
    if claims["exp"] - TICKET_TTL_SECONDS > now + _MAX_SKEW_SECONDS:
        return None
    return claims["sub"]
