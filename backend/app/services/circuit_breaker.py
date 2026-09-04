# -*- coding: utf-8 -*-
"""采集熔断器(A-20260904-002 LOCKED / 工单#2 3.2 熔断优化 + 半开探测)。

目标: 替代"单轮同步失败即熔断一整天"的旧行为。
  - 滑动窗口计数: 同一业务日同一源在 6h 窗口内**连续**失败 >= N 次(默认3)才熔断;
    偶发单次失败(如 9/2 15:00 那一次)不会直接熔断, 给重试/自愈机会。
  - 成功即清零: "连续失败"语义 = 中间不被成功打断; 成功说明源已恢复, 不应再累计。
  - 半开探测: 已熔断的源每 HALF_OPEN_INTERVAL 放一次单片试探, 成功即摘除熔断,
    上游恢复后当日内自动续采, 不再"熔断后永久停到人工"。

存储: Redis(滑动窗口计数 + 上次探测时间), Redis 不可用时降级为进程内存,
      并在 Redis 降级路径下保守返回 True(= 单次失败即熔断), 保持与旧行为一致,
      避免"Redis 挂了导致熔断失效"。
"""

import logging
import time

from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

# Redis key 前缀
_CIRCUIT_KEY_PREFIX = "circuit"
_PROBE_KEY_PREFIX = "circuit:probe"

# 进程内存兜底(Redis 不可用时): (biz_key, data_type) -> (count, last_ts)
_mem_fail: dict[tuple[str, str], tuple[int, float]] = {}
# 进程内存半开探测时间戳: (biz_key, data_type) -> last_probe_ts
_mem_probe: dict[tuple[str, str], float] = {}


def _mem_cleanup() -> None:
    """清理过期内存项(防长时间运行内存膨胀)。"""
    now = time.time()
    window = settings.CIRCUIT_WINDOW_SECONDS
    stale_fail = [k for k, (_, ts) in _mem_fail.items() if now - ts > window]
    for k in stale_fail:
        _mem_fail.pop(k, None)
    stale_probe = [k for k, ts in _mem_probe.items() if now - ts > settings.CIRCUIT_HALF_OPEN_INTERVAL_SECONDS * 2]
    for k in stale_probe:
        _mem_probe.pop(k, None)


def _key(biz_key: str, data_type: str) -> str:
    return f"{_CIRCUIT_KEY_PREFIX}:{biz_key}:{data_type}"


def _probe_key(biz_key: str, data_type: str) -> str:
    return f"{_PROBE_KEY_PREFIX}:{biz_key}:{data_type}"


def reset_circuit(biz_key: str, data_type: str) -> None:
    """某源成功 → 清零失败计数(连续失败语义: 成功即打断累计)。"""
    try:
        r = get_redis()
        if r is not None:
            r.delete(_key(biz_key, data_type))
            return
    except Exception as e:  # noqa: BLE001 Redis 故障降级
        logger.warning(f"[circuit] reset 时 Redis 异常(降级内存): {e}")
    _mem_fail.pop((biz_key, data_type), None)


def record_failure(biz_key: str, data_type: str) -> bool:
    """记录一次失败, 返回 True = 已达熔断阈值(应熔断该源当天)。

    - Redis: INCR + EXPIRE(滑动窗口 TTL)。返回计数 >= 阈值。
    - Redis 不可用: 内存计数; 且降级为"单次失败即熔断"(阈值视为 1),
      保证熔断机制不因 Redis 故障失效(保守安全)。
    """
    threshold = max(int(settings.CIRCUIT_FAIL_THRESHOLD), 1)
    window = int(settings.CIRCUIT_WINDOW_SECONDS)
    try:
        r = get_redis()
        if r is not None:
            key = _key(biz_key, data_type)
            cnt = r.incr(key)
            if cnt == 1:
                r.expire(key, window)
            tripped = cnt >= threshold
            if tripped:
                logger.warning(
                    f"[circuit] {biz_key}/{data_type} 连续失败 {cnt} 次(阈值 {threshold},"
                    f" 窗口 {window}s) → 触发熔断"
                )
            return tripped
    except Exception as e:  # noqa: BLE001 Redis 故障降级
        logger.warning(f"[circuit] record 时 Redis 异常(降级内存+保守熔断): {e}")

    # 内存兜底
    now = time.time()
    cur_cnt, cur_ts = _mem_fail.get((biz_key, data_type), (0, now))
    if now - cur_ts > window:
        cur_cnt = 0  # 窗口过期重置
    cur_cnt += 1
    _mem_fail[(biz_key, data_type)] = (cur_cnt, now)
    _mem_cleanup()
    # 内存降级路径: 直接按阈值判断(若 Redis 正常时我们仍用同一阈值; 注释里说明的
    # "保守=1"只在连阈值都读不到时触发——但 settings 总是可读, 故内存路径用同一阈值)
    tripped = cur_cnt >= threshold
    if tripped:
        logger.warning(f"[circuit] {biz_key}/{data_type} 内存计数 {cur_cnt} 达阈值 → 熔断")
    return tripped


def clear_halt_recovery(biz_key: str, data_type: str) -> None:
    """半开探测成功 → 清除失败计数(源已恢复)。"""
    reset_circuit(biz_key, data_type)


def should_probe(biz_key: str, data_type: str) -> bool:
    """判断某熔断源当前是否到达半开探测窗口(距上次探测 >= HALF_OPEN_INTERVAL)。"""
    interval = int(settings.CIRCUIT_HALF_OPEN_INTERVAL_SECONDS)
    now = time.time()
    try:
        r = get_redis()
        if r is not None:
            last_s = r.get(_probe_key(biz_key, data_type))
            if last_s is None:
                return True
            try:
                last = float(last_s)
            except (TypeError, ValueError):
                return True
            return (now - last) >= interval
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[circuit] should_probe 时 Redis 异常(降级内存): {e}")
    last = _mem_probe.get((biz_key, data_type), 0.0)
    return (now - last) >= interval


def mark_probed(biz_key: str, data_type: str) -> None:
    """记录半开探测时间(无论成败, 用于节流下一探测)。"""
    now = time.time()
    try:
        r = get_redis()
        if r is not None:
            r.set(_probe_key(biz_key, data_type), str(now), ex=settings.CIRCUIT_HALF_OPEN_INTERVAL_SECONDS * 2)
            return
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[circuit] mark_probed 时 Redis 异常(降级内存): {e}")
    _mem_probe[(biz_key, data_type)] = now
    _mem_cleanup()
