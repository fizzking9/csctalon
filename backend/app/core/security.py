"""安全模块:密码哈希与 JWT 令牌

直接使用 bcrypt 库(而非 passlib),避免 passlib 1.7.4 与 bcrypt 4.x
在初始化阶段 detect_wrap_bug 触发 "password cannot be longer than 72 bytes" 的兼容性错误。
passlib 自 2020 年起已停更,直接用 bcrypt 更稳定。
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

ALGORITHM = settings.JWT_ALGORITHM

# bcrypt 密文长度上限为 72 字节,超出截断(防 bcrypt 4.x 抛错)
_BCRYPT_MAX = 72


def _truncate(secret: str) -> bytes:
    """编码并截断到 bcrypt 允许的最大长度"""
    return secret.encode("utf-8")[:_BCRYPT_MAX]


def hash_password(password: str) -> str:
    """生成密码哈希(bcrypt)"""
    return bcrypt.hashpw(_truncate(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码"""
    try:
        return bcrypt.checkpw(_truncate(plain_password), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: str | int,
    extra: dict[str, Any] | None = None,
    token_version: int | None = None,
) -> str:
    """生成 JWT access token

    Args:
        subject: 用户ID(字符串形式)
        extra: 额外载荷(如 role, username)
        token_version: JWT 吊销版本号(M7); 写入 tkv, 校验时与 user.token_version 比对,
            不一致即失效。改密/登出时 +1 使旧 token 失效。
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "iat": now,
    }
    if token_version is not None:
        to_encode["tkv"] = token_version
    if extra:
        to_encode.update(extra)
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """解码 JWT,失败返回 None"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
