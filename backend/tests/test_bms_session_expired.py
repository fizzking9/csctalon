# -*- coding: utf-8 -*-
"""bms_fetcher 会话失效防护测试(2026-09-05 生产事故修复)

背景: 生产批量同步时, 组内 refund 登录成功后, 下一源 ticket 隔 65s 冷却再跳目标页
发现 BMS 会话已被踢下线 → 页面落回 #/login?redirect=...。旧代码不校验, 把"登录"按钮
当查询按钮点, 各源盲目重试 180~600s 后报误导性的"未找到导出按钮", 整批 2/4 失败。

修复: 新增 BmsSessionExpired 异常 + _do_fetch_one_shared 复用登录态跳页后校验 URL,
若落回登录页则抛出, 由 fetch_all 组循环捕获并重置 logged_in_ok → 同组下一源重新登录。

本测试锁定:
1. BmsSessionExpired 是 RuntimeError 子类(fetch_all 能按类型捕获, 且不会被误判成
   账号密码错误的通用 Exception 分支吞掉语义)
2. 该异常不应触发"登录类熔断"关键词匹配(它不是账号/密码/验证码问题, 是会话过期)
"""

from __future__ import annotations

from app.services.bms_fetcher import BmsSessionExpired


def test_bms_session_expired_is_runtime_error():
    """必须是 RuntimeError 子类: asyncio.wait_for 会透传, 且与 TimeoutError 区分"""
    assert issubclass(BmsSessionExpired, RuntimeError)
    # 不是 TimeoutError(asyncio.wait_for 会自己抛 TimeoutError, 需区分)
    assert not issubclass(BmsSessionExpired, TimeoutError)


def test_bms_session_expired_message_clarity():
    """异常文案应清楚说明「会话过期、重新登录」, 便于日志排查(与本次事故区分)。

    注意: 该异常由 fetch_all 的 `except BmsSessionExpired` 专用分支先行捕获处理,
    **不会**落入通用 `except Exception` 的「登录类熔断」判断(那里才会匹配 登录/密码
    /账号 关键词并跳过整组)。因此文案可自由包含"登录页"等词用于可读性。
    """
    err = BmsSessionExpired("refund BMS 会话已过期(页面落回登录页), 同组下一源将重新登录")
    txt = str(err)
    assert "会话已过期" in txt
    assert "重新登录" in txt
    # 与"账号密码错误"区分开, 便于人工一眼判断不是凭证问题
    assert "账号或密码错误" not in txt
    assert "验证码" not in txt


def test_bms_session_expired_can_be_caught_by_type():
    """fetch_all 的 except BmsSessionExpired 分支能捕获到(不落入通用 Exception)"""
    captured = None
    try:
        raise BmsSessionExpired("x BMS 会话已过期(页面落回登录页)")
    except BmsSessionExpired as e:
        captured = e
    assert captured is not None
    assert isinstance(captured, BmsSessionExpired)
