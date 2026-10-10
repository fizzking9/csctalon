"""跨站免密票据签发（csctalon → netmgr 网络管理面板，工单 #187）

方向：**本系统是签发方，netmgr 面板是消费方**。
  ① 管理员在管理端点入口 → 本模块签一张 60 秒一次性票据
  ② 浏览器跳到 netmgr `/sso/land#ticket=…&next=…`
  ③ netmgr 用同一共享密钥验签 + 核销 jti + 建立自己的会话

为什么是这个方向：netmgr 面板持有 `docker.sock`（等价宿主 root），不适合当业务系统的
身份权威；反过来由有真实用户/角色体系的 csctalon 签发、面板消费，信任链更短。

票据格式（与 netmgr `src/admin-panel/routes/sso.js` 逐字符对齐，**改一处必须改两处**）：
    base64url(payload) + "." + base64url(HMAC-SHA256(secret, payload))
    payload = {"sub": 用户名, "aud": "netmgr", "exp": 秒级时间戳, "jti": 16 位 hex}

安全约束：
  - 只允许 ADMIN 角色调用（见 api/v1/auth.py 的 AdminUser 依赖）
  - 60 秒过期；`aud` 绑定 netmgr（为其它系统签的票不能用于面板）
  - `jti` 一次性，由**消费方**核销 —— 签发方无状态、不落库
  - 密钥只从 settings 读，禁止硬编码（AGENTS.md 绝对禁止 3）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.core.config import settings

AUDIENCE = "netmgr"


def _b64url(raw: bytes) -> str:
    """base64url 无填充编码（与 netmgr 侧 Buffer.toString('base64url') 一致）。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def issue_ticket(sub: str, ttl_seconds: int | None = None, audience: str = AUDIENCE) -> str:
    """签发一张一次性票据。

    Args:
        sub: 身份标识（用调用者的用户名，仅作审计与展示，消费方不据此做授权判断）
        ttl_seconds: 有效期；缺省取 settings.SSO_TICKET_TTL_SECONDS
        audience: 受众绑定，缺省 netmgr
    """
    ttl = int(ttl_seconds if ttl_seconds is not None else settings.SSO_TICKET_TTL_SECONDS)
    claims = {
        "sub": str(sub),
        "aud": audience,
        "exp": int(time.time()) + ttl,
        # 16 位 hex，与消费方 jti 校验正则 ^[a-f0-9]{16,64}$ 对齐
        "jti": secrets.token_hex(8),
    }
    # separators 去空格：payload 与签名是逐字符绑定关系，格式必须稳定
    payload = _b64url(json.dumps(claims, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    signature = _b64url(
        hmac.new(settings.SSO_SHARED_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{payload}.{signature}"


def build_netmgr_entry_url(sub: str, next_path: str = "/dashboard") -> str:
    """拼出跳 netmgr 落地页的 URL（票据与 next 都放 fragment，不进服务端日志）。"""
    ticket = issue_ticket(sub)
    base = settings.SSO_NETMGR_URL.rstrip("/")
    return f"{base}/sso/land#ticket={ticket}&next={next_path}"
