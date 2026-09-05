# -*- coding: utf-8 -*-
"""bms_fetcher 下载超时/外层预算测试(2026-09-05 修复)

背景: 生产「点导出后卡到超时, 无重试日志」。
根因1: BMS 五源扩容新增源(refund_record_report/device_profile/bms_order/return_tracking
       /ticket_finish)不在 _DOWNLOAD_TIMEOUT_MS 白名单 → 走默认 120s, 大导出易超时。
根因2: fetch_all/fetch_one 外层 asyncio.wait_for 超时 = 内层 _download_with_retry 单次
       下载超时(同值) → 内层第 1 次卡到单次超时时外层同时取消 → 内层重试永远没机会执行。

本测试锁定:
1. 新源已补超时/重试配置(不走默认)
2. _fetch_total_budget_sec 外层预算 恒 > 单次下载超时(保证重试窗口)
3. 外层预算足够容纳 内层下载最坏总耗时(单次 × (重试+1) + 限流等待)
"""

from __future__ import annotations

from app.services.bms_fetcher import (
    _DOWNLOAD_MAX_RETRIES,
    _DOWNLOAD_TIMEOUT_MS,
    _fetch_total_budget_sec,
)

# BMS 五源扩容新增源(2026-09-05, A-20260905-001) 必须显式配置, 不能走默认 120s
_NEW_BMS_SOURCES = [
    "refund_record_report",
    "bms_order",
    "return_tracking",
    "device_profile",
    "ticket_finish",  # ticket 双维度补抓
]


def test_new_sources_have_explicit_download_timeout():
    """新源必须有显式下载超时(不能落默认 120s)"""
    for dt in _NEW_BMS_SOURCES:
        assert dt in _DOWNLOAD_TIMEOUT_MS, f"{dt} 缺少 _DOWNLOAD_TIMEOUT_MS 配置"
        assert _DOWNLOAD_TIMEOUT_MS[dt] > 120_000, f"{dt} 超时应 > 默认120s, 实际 {_DOWNLOAD_TIMEOUT_MS[dt]}"


def test_new_sources_have_retry():
    """新源必须有至少 1 次下载重试(兜底偶发超时/限流)"""
    for dt in _NEW_BMS_SOURCES:
        assert dt in _DOWNLOAD_MAX_RETRIES, f"{dt} 缺少 _DOWNLOAD_MAX_RETRIES 配置"
        assert _DOWNLOAD_MAX_RETRIES[dt] >= 1


def test_outer_budget_greater_than_single_timeout():
    """外层总预算必须 > 单次下载超时(否则内层重试被外层吞掉 → 本次修复的核心)"""
    for dt in list(_DOWNLOAD_TIMEOUT_MS) + ["unknown_type"]:
        budget = _fetch_total_budget_sec(dt)
        single = _DOWNLOAD_TIMEOUT_MS.get(dt, 120_000) // 1000
        assert budget > single, f"{dt}: 外层预算 {budget}s 应 > 单次下载超时 {single}s"


def test_outer_budget_covers_inner_worst_case():
    """外层预算必须 ≥ 内层 _download_with_retry 最坏总耗时(单次×重试次数 + 限流等待)。

    内层最坏: 每次尝试最多 timeout_ms, 尝试 (retries+1) 次; 每次重试前可能限流多等 60s。
    外层预算(helper) = 下载秒×(retries+1) + 60×retries + 前置裕量90s → 必 ≥ 内层最坏。
    """
    for dt in list(_DOWNLOAD_TIMEOUT_MS) + ["unknown"]:
        timeout_sec = _DOWNLOAD_TIMEOUT_MS.get(dt, 120_000) // 1000
        retries = _DOWNLOAD_MAX_RETRIES.get(dt, 1)
        inner_worst = timeout_sec * (retries + 1) + 60 * retries + 2  # 2s 重试间隔
        budget = _fetch_total_budget_sec(dt)
        assert budget >= inner_worst, (
            f"{dt}: 外层预算 {budget}s 应 ≥ 内层最坏 {inner_worst}s "
            f"(timeout={timeout_sec}s × {retries+1}次 + 限流{60*retries}s)"
        )


def test_budget_specific_values():
    """抽查典型源预算合理性(防未来误改)"""
    # refund: 180s×2次 + 60限流 + 90前置 = 510s
    assert _fetch_total_budget_sec("refund") == 180 * 2 + 60 + 90
    # wallet: 600s×3次 + 120限流 + 90前置 = 2010s
    assert _fetch_total_budget_sec("wallet") == 600 * 3 + 120 + 90
    # refund_record_report(新源): 180s×2次 + 60限流 + 90前置 = 510s
    assert _fetch_total_budget_sec("refund_record_report") == 180 * 2 + 60 + 90
