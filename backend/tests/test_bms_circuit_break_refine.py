# -*- coding: utf-8 -*-
"""bms_fetcher 登录类错误熔断细化测试(2026-09-07)

背景(生产事故): device_combo 单源凭证被误填成本系统账号 tanzk(与组凭证 YKM-TZK
不同)。手动采集时 device_combo 作为组内首源用 tanzk 登录 BMS → 5 秒报
"用户名不存在或密码错误" → 旧逻辑把任意"登录类错误"写入组级熔断记忆
group_login_failed_err → 同组其余 5 个本可用组凭证 YKM-TZK 的源全部被
[同域登录熔断复用] 跳过 → 6 源连锁失败(仅独立域名闪猫成功)。

修复: 登录类错误按"凭证来源"区分处置(纯函数 should_circuit_break_group_on_login_error):
  - 本源用**组/共享凭证**(credential_is_own=False) 登录失败 → 共享账号坏 → 熔断整组
  - 本源用**单源独立凭证**(credential_is_own=True) 登录失败 → 只是该源自己的账号坏,
    不连坐同组 → 让后续源用组凭证重新登录
"""

from __future__ import annotations

from app.services.bms_fetcher import (
    is_login_class_error,
    should_circuit_break_group_on_login_error,
)


# ── is_login_class_error ─────────────────────────────────────────────
def test_login_class_error_detects_account_text():
    """"用户名不存在或密码错误"(BMS 页面原文) 应判定为登录类错误。"""
    assert is_login_class_error("登录失败: 用户名不存在或密码错误!") is True
    assert is_login_class_error("登录失败: 账号或密码错误") is True
    assert is_login_class_error("验证码自动识别登录失败(xx); 请手动登录") is True


def test_login_class_error_ignores_page_crashed():
    """浏览器崩溃(Page crashed) 不应判为登录类错误。"""
    assert is_login_class_error("Page crashed: something", page_crashed=True) is False


def test_login_class_error_ignores_download_timeout():
    """导出下载超时等非登录错误 → 不判为登录类(避免误熔断)。"""
    assert is_login_class_error("导出下载 3 次均失败: Timeout") is False


# ── should_circuit_break_group_on_login_error ────────────────────────
def test_group_credential_failure_circuit_breaks_group():
    """组/共享凭证登录失败 → 熔断整组(既有行为, 同组同账号必失败)。"""
    assert should_circuit_break_group_on_login_error(
        "登录失败: 用户名不存在或密码错误!", credential_is_own=False,
    ) is True


def test_own_credential_failure_does_not_circuit_break_group():
    """单源独立凭证登录失败 → 不熔断整组(核心修复: 不连坐同组其它源)。"""
    assert should_circuit_break_group_on_login_error(
        "登录失败: 用户名不存在或密码错误!", credential_is_own=True,
    ) is False


def test_non_login_error_never_circuit_breaks():
    """非登录类错误(下载超时等) → 无论凭证来源都不熔断(走 logged_in 复用逻辑)。"""
    assert should_circuit_break_group_on_login_error(
        "导出下载 3 次均失败", credential_is_own=False,
    ) is False
    assert should_circuit_break_group_on_login_error(
        "导出下载 3 次均失败", credential_is_own=True,
    ) is False


def test_page_crashed_never_circuit_breaks():
    """浏览器崩溃 → 即使文案含"登录/密码"也不熔断。"""
    assert should_circuit_break_group_on_login_error(
        "Page crashed 导致登录页无法加载", credential_is_own=False, page_crashed=True,
    ) is False


def test_empty_error_never_circuit_breaks():
    """空错误文本 → 不熔断。"""
    assert should_circuit_break_group_on_login_error("", credential_is_own=False) is False
