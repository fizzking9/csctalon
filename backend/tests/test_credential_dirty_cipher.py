# -*- coding: utf-8 -*-
"""凭证解析"脏密文回退组级"防御测试(2026-09-06)

背景(生产事故): device_combo 单源凭证是用**旧密钥**加密的脏数据(Fernet 密文),
密钥轮换后 decrypt_secret 解密失败会**原样返回密文**(兼容历史明文的容错设计),
导致 resolve_credentials 把 `gAAAA...` 密文当有效账号 → 登录报"用户名不存在或
密码错误", 且作为组内首源连累同组其它源被熔断跳过。

修复:
  - crypto.is_fernet_ciphertext: 识别字符串是否为 Fernet 密文(gAAAA 前缀)
  - source_group_service._safe_decrypt: 解密结果若仍是 Fernet 密文(密钥不匹配) →
    视为不可用返回 None, 让调用方回退组级凭证 / 提示重配; 历史明文仍原样返回(兼容)

本测试锁定: 脏密文识别 / _safe_decrypt 对脏密文返 None / 对明文与正常密文不误伤。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.crypto import decrypt_secret, encrypt_secret, is_fernet_ciphertext
from app.services.source_group_service import _safe_decrypt, resolve_credentials


def _mk_fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def test_is_fernet_ciphertext_recognizes_ciphertext():
    """Fernet 密文(gAAAA 前缀)应被识别; 明文/空不被误判。"""
    ct = encrypt_secret("some-secret")
    assert ct and ct.startswith("gAAAA")
    assert is_fernet_ciphertext(ct) is True
    assert is_fernet_ciphertext("YKM-TZK") is False       # 普通明文账号
    assert is_fernet_ciphertext("") is False              # 空
    assert is_fernet_ciphertext(None) is False            # None


def test_safe_decrypt_dirty_ciphertext_returns_none():
    """脏密文(旧密钥加密, 当前密钥解不开) → decrypt_secret 原样返回密文,
    _safe_decrypt 必须识别并返回 None(回退组级), 绝不当明文账号用。"""
    # 用"旧密钥"加密(模拟密钥轮换前的脏数据)
    old_fernet = _mk_fernet("old-secret-key-that-is-no-longer-used")
    dirty_user = old_fernet.encrypt(b"YKM-TZK").decode()
    dirty_pwd = old_fernet.encrypt(b"P@ssw0rd-old").decode()

    # decrypt_secret 容错: 返回原密文(这是设计, 兼容历史明文)
    assert decrypt_secret(dirty_user).startswith("gAAAA")
    # _safe_decrypt 兜底: 识别为不可用 → None
    assert _safe_decrypt(dirty_user) is None
    assert _safe_decrypt(dirty_pwd) is None


def test_safe_decrypt_normal_plaintext_passthrough():
    """历史明文(非密文)仍原样返回(不破坏兼容)。"""
    assert _safe_decrypt("YKM-TZK") == "YKM-TZK"


def test_safe_decrypt_valid_ciphertext_decrypts():
    """当前密钥加密的正常密文 → 正确解密为明文。"""
    ct = encrypt_secret("correct-account")
    assert _safe_decrypt(ct) == "correct-account"


def test_safe_decrypt_empty_returns_none():
    """空值 → None。"""
    assert _safe_decrypt(None) is None
    assert _safe_decrypt("") is None


# ── resolve_credentials 集成: 脏单源凭证应回退组级 ──────────────────────
def _make_group(db, group_key, user="YKM-TZK", pwd="P@ssw0rd123"):
    """插入组级凭证(用当前密钥加密 = 正确可解密); 需传唯一 group_key 防 seed 冲突。"""
    from app.models.source_group import SourceGroup

    g = SourceGroup(
        group_key=group_key,
        group_name=f"组-{group_key}",
        base_url="https://wifi.cloud-cat.cn/bms",
        username=encrypt_secret(user),
        password=encrypt_secret(pwd),
    )
    db.add(g)
    db.flush()
    return g


def _make_source(db, data_type, group_key, username=None, password=None):
    """插入单源配置(username/password 若传原始密文则原样入库, 模拟脏数据)。"""
    from app.models.sync_source_config import SyncSourceConfig

    row = SyncSourceConfig(
        data_type=data_type,
        name=data_type,
        base_url="https://wifi.cloud-cat.cn/bms",
        page_path=f"/index.html#/x/{data_type}",
        group_key=group_key,
        username=username,
        password=password,
        default_sync=True,
        enabled=True,
    )
    db.add(row)
    db.flush()
    return row


def test_resolve_credentials_dirty_source_falls_back_to_group(db):
    """脏单源凭证(旧密钥加密, 当前密钥解不开) → resolve_credentials 回退组级正确凭证。

    这是 2026-09-06 device_combo 生产事故的核心场景:
    旧实现把脏密文当有效账号(own_user 非空) → 登录报错 + 连累同组其它源。
    修复后必须回退到组级 YKM-TZK。
    """
    gk = "bms-wifi-test-dirty"
    _make_group(db, gk)
    # 构造"旧密钥"脏密文: 用当前密钥解不开 → decrypt_secret 原样返回密文
    old_fernet = _mk_fernet("old-secret-key-not-used-anymore")
    dirty_user = old_fernet.encrypt(b"OLD-USER").decode()
    dirty_pwd = old_fernet.encrypt(b"OLD-PWD").decode()
    row = _make_source(db, "dev_test_device_combo", gk, username=dirty_user, password=dirty_pwd)

    user, pwd = resolve_credentials(db, row)
    assert user == "YKM-TZK", f"脏单源应回退组级账号, 实际={user!r}"
    assert pwd == "P@ssw0rd123"


def test_resolve_credentials_valid_source_preferred_over_group(db):
    """单源凭证正常(当前密钥可解密) → 仍优先用单源, 不误伤正常源。"""
    gk = "bms-wifi-test-valid"
    _make_group(db, gk, user="GROUP-USER", pwd="GROUP-PWD")
    row = _make_source(
        db, "dev_test_refund", gk,
        username=encrypt_secret("YKM-TZK"),
        password=encrypt_secret("SOURCE-PWD"),
    )
    user, pwd = resolve_credentials(db, row)
    assert user == "YKM-TZK"
    assert pwd == "SOURCE-PWD"


def test_resolve_credentials_no_source_uses_group(db):
    """单源无凭证 → 直接用组级。"""
    gk = "bms-wifi-test-nosrc"
    _make_group(db, gk, user="GROUP-USER", pwd="GROUP-PWD")
    row = _make_source(db, "dev_test_bms_order", gk)  # 单源无 username/password
    user, pwd = resolve_credentials(db, row)
    assert user == "GROUP-USER"
    assert pwd == "GROUP-PWD"
