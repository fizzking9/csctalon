# -*- coding: utf-8 -*-
"""工单#2 / A-20260904-002 3.2 熔断优化 + 半开探测 —— 单元测试。

覆盖 circuit_breaker:
1. record_failure: 连续失败达阈值才触发熔断(未达阈值返回 False)
2. reset_circuit: 成功清零计数(连续失败语义)
3. should_probe/mark_probed: 半开探测间隔节流
4. Redis 路径与内存降级路径(monkeypatch get_redis 返回 None)
"""

from __future__ import annotations

import time

import pytest

from app.core.config import settings
from app.services import circuit_breaker as cb


@pytest.fixture(autouse=True)
def _clean_mem():
    """每个用例前清空进程内存兜底, 保证隔离。"""
    cb._mem_fail.clear()
    cb._mem_probe.clear()
    yield
    cb._mem_fail.clear()
    cb._mem_probe.clear()


@pytest.fixture()
def _redis_down(monkeypatch):
    """强制 Redis 不可用 → 走内存降级路径(确定性)。"""
    monkeypatch.setattr(cb, "get_redis", lambda: None)


@pytest.fixture()
def _low_threshold(monkeypatch):
    monkeypatch.setattr(settings, "CIRCUIT_FAIL_THRESHOLD", 3)
    monkeypatch.setattr(settings, "CIRCUIT_WINDOW_SECONDS", 3600)
    monkeypatch.setattr(settings, "CIRCUIT_HALF_OPEN_INTERVAL_SECONDS", 2 * 3600)


# ──────────── 1. 内存降级路径(Redis 不可用) ────────────


def test_failure_below_threshold_not_tripped(_redis_down, _low_threshold):
    """连续失败 2 次(<阈值3) → 不熔断。"""
    assert cb.record_failure("2026-09-04", "refund") is False
    assert cb.record_failure("2026-09-04", "refund") is False


def test_failure_reaches_threshold_trips(_redis_down, _low_threshold):
    """连续失败 3 次(=阈值) → 触发熔断。"""
    assert cb.record_failure("2026-09-04", "refund") is False
    assert cb.record_failure("2026-09-04", "refund") is False
    assert cb.record_failure("2026-09-04", "refund") is True


def test_success_resets_counter(_redis_down, _low_threshold):
    """成功清零: 失败2次→成功→再失败2次都不熔断(连续失败被打断)。"""
    cb.record_failure("2026-09-04", "ticket")
    cb.record_failure("2026-09-04", "ticket")
    cb.reset_circuit("2026-09-04", "ticket")  # 成功清零
    assert cb.record_failure("2026-09-04", "ticket") is False
    assert cb.record_failure("2026-09-04", "ticket") is False


def test_different_type_isolated(_redis_down, _low_threshold):
    """不同源/不同业务日计数隔离。"""
    cb.record_failure("2026-09-04", "refund")
    cb.record_failure("2026-09-04", "refund")
    assert cb.record_failure("2026-09-04", "ticket") is False  # ticket 首次失败
    assert cb.record_failure("2026-09-03", "refund") is False  # 前一天首次失败


# ──────────── 2. 半开探测节流 ────────────


def test_should_probe_first_time(_redis_down):
    """首次(无历史) → 应探测。"""
    assert cb.should_probe("2026-09-04", "refund") is True


def test_probe_throttle(_redis_down):
    """探测后间隔内不再探测; 超过间隔重新允许。"""
    cb.mark_probed("2026-09-04", "refund")
    assert cb.should_probe("2026-09-04", "refund") is False  # 刚探过, 节流中

    # 手动把上次探测时间拨回超过间隔
    interval = settings.CIRCUIT_HALF_OPEN_INTERVAL_SECONDS
    cb._mem_probe[("2026-09-04", "refund")] = time.time() - interval - 1
    assert cb.should_probe("2026-09-04", "refund") is True


# ──────────── 3. Redis 路径 ────────────


class _FakeRedis:
    """最小 Redis fake: incr/expire/get/set/delete。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    def expire(self, key: str, seconds: int) -> bool:
        self.ttl[key] = seconds
        return True

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, ex=None) -> bool:
        self.store[key] = value
        if ex:
            self.ttl[key] = ex
        return True

    def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture()
def _fake_redis(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr(cb, "get_redis", lambda: fr)
    return fr


def test_redis_path_trip_and_reset(_fake_redis, _low_threshold):
    """Redis 路径: 达阈值熔断 + reset 清零。"""
    assert cb.record_failure("2026-09-04", "wallet") is False
    assert cb.record_failure("2026-09-04", "wallet") is False
    assert cb.record_failure("2026-09-04", "wallet") is True  # 达阈值
    # TTL 已设置(滑动窗口)
    assert cb._key("2026-09-04", "wallet") in _fake_redis.ttl
    cb.reset_circuit("2026-09-04", "wallet")
    assert cb._key("2026-09-04", "wallet") not in _fake_redis.store  # 已删


def test_redis_probe_throttle(_fake_redis, _low_threshold):
    """Redis 路径: mark_probed 后节流。"""
    cb.mark_probed("2026-09-04", "cs_record")
    assert cb.should_probe("2026-09-04", "cs_record") is False
    # 直接改 fake store 把上次探测时间拨回超过间隔
    interval = settings.CIRCUIT_HALF_OPEN_INTERVAL_SECONDS
    _fake_redis.store[cb._probe_key("2026-09-04", "cs_record")] = str(time.time() - interval - 1)
    assert cb.should_probe("2026-09-04", "cs_record") is True
