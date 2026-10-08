# -*- coding: utf-8 -*-
"""文档采集「尝试心跳 vs 成功水位」分离的共享逻辑 (R-P1-16 / #241)。

背景(为什么要拆):
    历史上 doc_sync_sources / doc_sync_configs 的 `last_sync_at` 一个字段同时当
    「最近一次尝试(调度节流基准/心跳)」和「最近一次成功(看板数据新鲜度水位)」。
    失败路径也刷新它 → ① 调度把失败当成功, 失败源要静默等满整个 interval 才重试;
    ② 看板把失败误读成"数据刚更新"。

本模块把两件事拆清, 供 shared_doc_sync_service / doc_sync_service 共用(口径单一来源):
    - `last_attempt_at`: 最近一次**尝试**时间(无论成败) —— 调度节流基准。
    - `last_sync_at`:    最近一次**成功**时间(success/partial/skipped) —— 真实数据水位;
                         failed 时保留上一次成功值, 不被污染。

对 row 用鸭子类型: 两个模型都具备 last_attempt_at / last_sync_at / last_sync_status 字段,
本模块不 import 具体模型, 避免互相耦合。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

# 完全失败(异常 / 本轮无任何有效入库); 其余状态(success/partial/skipped)都视为水位前进
STATUS_FAILED = "failed"

# interval 下限(分钟): 与两路径历史行为一致, 防止配置成 0/负数导致空转打爆 playwright
_MIN_INTERVAL_MINUTES = 5
_FALLBACK_INTERVAL_MINUTES = 60


class _WatermarkRow(Protocol):
    last_attempt_at: datetime | None
    last_sync_at: datetime | None
    last_sync_status: str | None


def stamp_attempt(row: Any, status: str, now: datetime) -> None:
    """记录一次采集尝试的时间水位(直接改 ORM 行, 调用方负责 commit)。

    - last_attempt_at 恒刷新(无论成败) —— 这是调度节流的心跳基准。
    - last_sync_at 仅在**非 failed** 时刷新 —— 失败保留上一次成功水位,
      让看板的"数据新鲜度"不被失败掩盖。
    """
    row.last_attempt_at = now
    if status != STATUS_FAILED:
        row.last_sync_at = now


def due_wait_minutes(
    status: str | None,
    interval_minutes: int | None,
    failed_retry_cap_minutes: int,
) -> int:
    """计算距上次尝试后, 下一次到期需等待的分钟数。

    - 正常(非 failed): 源自身 interval(下限 5min)。
    - 上一轮 failed: min(正常 interval, 失败重试上限)。失败不应比正常等得更久,
      长间隔源(如 60min)封顶到失败重试上限(默认 30min = 下一高频槽位)快速重试。
    """
    normal = max(interval_minutes or _FALLBACK_INTERVAL_MINUTES, _MIN_INTERVAL_MINUTES)
    if status == STATUS_FAILED:
        cap = max(failed_retry_cap_minutes, _MIN_INTERVAL_MINUTES)
        return min(normal, cap)
    return normal


def is_due(
    *,
    last_attempt_at: datetime | None,
    last_sync_at: datetime | None,
    last_status: str | None,
    interval_minutes: int | None,
    now: datetime,
    failed_retry_cap_minutes: int,
) -> bool:
    """判断采集源是否到点。

    基准取 last_attempt_at(新字段); 老行为/存量回填前可能为 None, 则回退 last_sync_at。
    两者皆空(从未跑过) → 首次恒到期。failed 档走失败重试上限, 其余走源 interval。
    """
    ref = last_attempt_at or last_sync_at
    if ref is None:
        return True
    wait = due_wait_minutes(last_status, interval_minutes, failed_retry_cap_minutes)
    return now >= ref + timedelta(minutes=wait)
