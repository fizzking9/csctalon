# -*- coding: utf-8 -*-
"""source_groups._resolve_login_url 登录页 URL 推导测试

背景(2026-09-05 生产故障): 「BMS 组测试登录」报"未找到账号/密码输入框"。
根因: BMS 主平台登录页是 SPA hash 路由 base_url + /index.html#/login,
而旧代码对所有源统一拼 base_url + /login → nginx 404(无任何输入框)。

本测试锁定 URL 推导规则, 防止回归:
  - 非 scrm_ 源(BMS 主平台): 必须用 /index.html#/login
  - scrm_ 源: 用 options.login_url_path(默认 /login)
"""

from __future__ import annotations

from app.api.v1.source_groups import _resolve_login_url

BMS_BASE = "https://wifi.cloud-cat.cn/bms"


def test_bms_source_uses_hash_login_route():
    """BMS(非 scrm_)登录页必须走 SPA hash 路由, 不能是 /login(会 404)"""
    for dt in ("refund", "ticket", "wallet", "device_combo", "bms_order"):
        assert _resolve_login_url(BMS_BASE, dt) == f"{BMS_BASE}/index.html#/login", dt


def test_bms_with_custom_options_ignores_login_url_path():
    """BMS 即使 options 里误配了 login_url_path 也不该用它(统一 hash 路由)"""
    opts = {"login_url_path": "/login"}
    assert _resolve_login_url(BMS_BASE, "refund", opts) == f"{BMS_BASE}/index.html#/login"


def test_scrm_uses_login_url_path():
    """SCRM 网页源仍用 options.login_url_path(默认 /login)"""
    base = "https://gateway.bilinl.com"
    assert _resolve_login_url(base, "scrm_operator") == f"{base}/login"
    assert _resolve_login_url(base, "scrm_operator", {"login_url_path": "/login"}) == f"{base}/login"
    # 自定义路径 + 未以 / 开头也正确拼接
    assert _resolve_login_url(base, "scrm_session_private", {"login_url_path": "auth/login"}) == f"{base}/auth/login"


def test_trailing_slash_on_base_url_normalized():
    """base_url 末尾带 / 不影响推导"""
    assert _resolve_login_url(f"{BMS_BASE}/", "refund") == f"{BMS_BASE}/index.html#/login"
    assert _resolve_login_url("https://gateway.bilinl.com/", "scrm_operator") == "https://gateway.bilinl.com/login"
