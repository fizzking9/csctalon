"""敏感配置加解密 - Fernet 对称加密

用于 sync_source_configs 表中 BMS 账号密码的加密存储。
key 来源:settings.CONFIG_ENCRYPTION_KEY(优先) → 否则从 JWT_SECRET_KEY 派生(SHA256)。
解密失败时回退原值,兼容历史明文/手动填入的数据。
"""

import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings


def _get_fernet() -> Fernet:
    raw = settings.CONFIG_ENCRYPTION_KEY or settings.JWT_SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str | None) -> str | None:
    """加密明文,空值返回 None"""
    if not plaintext:
        return None
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str | None) -> str | None:
    """解密密文,空值返回 None;解密失败(明文/历史值)回退原值"""
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception:
        # 非加密明文(历史数据或手动填入)直接返回
        return ciphertext
