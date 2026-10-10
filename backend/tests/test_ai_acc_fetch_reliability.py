# -*- coding: utf-8 -*-
"""工单#2 / A-20260904-002 3.1 错误分级重试 —— 单元测试。

覆盖:
1. _chunk_time_range: 分片边界相接 / 超过7天自动切分 / 按1天切分
2. _classify_http_error: HTTP>=500→transient, 429→transient(带Retry-After),
   JSON code>=500→transient, 400/401/403→permanent
3. _fetch_one_chunk: transient 重试至成功 / permanent 不重试 /
   transient 重试耗尽返回失败
4. fetch_one: 单片成功 / 分片部分失败返回 partial_failures /
   整窗全失败 success=False / 按日降级重试成功

说明: 纯逻辑单测, 不依赖数据库; 抓取用 monkeypatch 替换 _export_chunk。
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pandas as pd
import pytest

from app.core.config import settings
from app.services.ai_acc_fetcher import (
    ClassifiedFetchError,
    FetchErrorClass,
    _chunk_time_range,
    _classify_http_error,
    _fetch_one_chunk,
    fetch_one,
)

TZ_CN = None  # datetime 均为 naive, 与 fetcher 内部一致


# ───────────────────────── 1. 时间分片 ─────────────────────────


def test_chunk_time_range_boundary_joined():
    """分片边界相接(不重不漏)。"""
    # 9/1 ~ 9/9 = 8 天 > 7 天上限 → 2 片
    start = datetime(2026, 9, 1)
    end = datetime(2026, 9, 9)
    chunks = _chunk_time_range(start, end, max_days=7)
    assert len(chunks) == 2
    assert chunks[0] == (datetime(2026, 9, 1), datetime(2026, 9, 8))
    assert chunks[1] == (datetime(2026, 9, 8), datetime(2026, 9, 9))
    # 边界相接: chunk[i].end == chunk[i+1].start
    for i in range(len(chunks) - 1):
        assert chunks[i][1] == chunks[i + 1][0]


def test_chunk_time_range_exact_7_days_single():
    """正好 7 天 → 单片(API 允许)。"""
    start = datetime(2026, 9, 1)
    end = datetime(2026, 9, 8)
    chunks = _chunk_time_range(start, end, max_days=7)
    assert len(chunks) == 1


def test_chunk_time_range_single_day():
    """按 1 天切分(按日降级用)。"""
    start = datetime(2026, 9, 1, 0, 0, 0)
    end = datetime(2026, 9, 3, 0, 0, 0)
    chunks = _chunk_time_range(start, end, max_days=1)
    assert len(chunks) == 2
    assert chunks[0] == (datetime(2026, 9, 1), datetime(2026, 9, 2))
    assert chunks[1] == (datetime(2026, 9, 2), datetime(2026, 9, 3))


def test_chunk_time_range_invalid():
    assert _chunk_time_range(datetime(2026, 9, 2), datetime(2026, 9, 1)) == []


# ───────────────────────── 2. 错误分类 ─────────────────────────


def _mk_resp(status: int, body: bytes = b"", headers: dict | None = None):
    req = httpx.Request("GET", "http://x/export")
    return httpx.Response(status, content=body, headers=headers or {}, request=req)


def test_classify_http_500_transient():
    err = _classify_http_error(_mk_resp(500, b"server error"))
    assert err.error_class == FetchErrorClass.TRANSIENT


def test_classify_http_429_transient_with_retry_after():
    err = _classify_http_error(_mk_resp(429, b"too many", headers={"retry-after": "30"}))
    assert err.error_class == FetchErrorClass.TRANSIENT
    assert err.retry_after == 30.0


def test_classify_http_json500_transient():
    """HTTP 200 但 body 是 {"code":500} —— 9/2 事故根因形态 → transient。"""
    err = _classify_http_error(
        _mk_resp(200, b'{"code":500,"msg":"server boom"}', headers={"content-type": "application/json"})
    )
    assert err.error_class == FetchErrorClass.TRANSIENT


def test_classify_http_401_permanent():
    err = _classify_http_error(_mk_resp(401, b'unauthorized'))
    assert err.error_class == FetchErrorClass.PERMANENT


def test_classify_http_400_permanent():
    err = _classify_http_error(_mk_resp(400, b'{"code":400,"msg":"bad"}',
                                        headers={"content-type": "application/json"}))
    assert err.error_class == FetchErrorClass.PERMANENT


# ───────────────────────── 3. 分片级重试 ─────────────────────────


class _FakeClient:
    """最小 fake: 仅用于占位, _fetch_one_chunk 内未真正使用 client 方法(已 patch export)。"""


@pytest.fixture()
def _fast_retry(monkeypatch):
    """把退避 sleep 缩短, 加速测试。"""
    monkeypatch.setattr(settings, "AI_ACC_RETRY_BASE_S", 0.01)
    monkeypatch.setattr(settings, "AI_ACC_RETRY_CAP_S", 0.05)
    monkeypatch.setattr("app.services.ai_acc_fetcher._backoff_sleep", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_fetch_one_chunk_transient_then_success(monkeypatch, _fast_retry):
    """transient 失败 1 次后重试成功。"""
    calls = {"n": 0}

    async def fake_export(client, base_url, token, start, end):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ClassifiedFetchError(FetchErrorClass.TRANSIENT, "boom 500")
        buf = __import__("io").BytesIO()
        pd.DataFrame({"a": [1, 2]}).to_excel(buf, index=False)
        return buf.getvalue()

    monkeypatch.setattr("app.services.ai_acc_fetcher._export_chunk", fake_export)
    df, failure = await _fetch_one_chunk(
        _FakeClient(), "http://x", "tok", "cs_record", 1, 1,
        datetime(2026, 9, 1), datetime(2026, 9, 2),
    )
    assert failure is None
    assert df is not None and len(df) == 2
    assert calls["n"] == 2  # 1 次失败 + 1 次成功


@pytest.mark.asyncio
async def test_fetch_one_chunk_permanent_no_retry(monkeypatch, _fast_retry):
    """permanent 失败不重试, 立即返回失败。"""
    calls = {"n": 0}

    async def fake_export(client, base_url, token, start, end):
        calls["n"] += 1
        raise ClassifiedFetchError(FetchErrorClass.PERMANENT, "401 bad cred")

    monkeypatch.setattr("app.services.ai_acc_fetcher._export_chunk", fake_export)
    df, failure = await _fetch_one_chunk(
        _FakeClient(), "http://x", "tok", "cs_record", 1, 1,
        datetime(2026, 9, 1), datetime(2026, 9, 2),
    )
    assert df is None
    assert failure is not None
    assert failure["error_class"] == "permanent"
    assert calls["n"] == 1  # 不重试


@pytest.mark.asyncio
async def test_fetch_one_chunk_transient_exhausted(monkeypatch, _fast_retry):
    """transient 重试耗尽(AI_ACC_RETRY_MAX 次)返回失败。"""
    calls = {"n": 0}
    max_retry = settings.AI_ACC_RETRY_MAX  # 3

    async def fake_export(client, base_url, token, start, end):
        calls["n"] += 1
        raise ClassifiedFetchError(FetchErrorClass.TRANSIENT, "still 500")

    monkeypatch.setattr("app.services.ai_acc_fetcher._export_chunk", fake_export)
    df, failure = await _fetch_one_chunk(
        _FakeClient(), "http://x", "tok", "cs_record", 1, 1,
        datetime(2026, 9, 1), datetime(2026, 9, 2),
    )
    assert df is None
    assert failure is not None
    assert failure["error_class"] == "transient"
    assert calls["n"] == max_retry + 1  # 初始 1 + 重试 max_retry 次


# ───────────────────────── 4. fetch_one 集成 ─────────────────────────


@pytest.mark.asyncio
async def test_fetch_one_single_chunk_success(monkeypatch):
    """单窗口成功 → success=True, 无 partial_failures。"""
    import io

    def make_xlsx():
        buf = io.BytesIO()
        pd.DataFrame({"col": ["a", "b", "c"]}).to_excel(buf, index=False)
        return buf.getvalue()

    async def fake_login(*a, **k):
        return "tok"

    async def fake_export(*a, **k):
        return make_xlsx()

    monkeypatch.setattr("app.services.ai_acc_fetcher._login", fake_login)
    monkeypatch.setattr("app.services.ai_acc_fetcher._export_chunk", fake_export)
    monkeypatch.setattr(settings, "UPLOAD_DIR", "/tmp/ai_acc_test_uploads")

    task = {
        "data_type": "cs_record",
        "start": datetime(2026, 9, 1, 10, 0),
        "end": datetime(2026, 9, 2, 10, 0),  # 该窗口 <7 天 → 单片
        "config": {"base_url": "http://x", "username": "u", "password": "p"},
    }
    # 该窗口 <7 天 → 单片
    fr = await fetch_one(task)
    assert fr.success is True
    assert fr.total_rows == 3
    assert fr.partial_failures is None
    assert fr.file_path and fr.filename


@pytest.mark.asyncio
async def test_fetch_one_chunk_partial_failure(monkeypatch, _fast_retry):
    """多片: 一片永久失败 → 部分成功 + partial_failures 记录失败范围。"""
    import io

    def make_xlsx():
        buf = io.BytesIO()
        pd.DataFrame({"col": ["a"]}).to_excel(buf, index=False)
        return buf.getvalue()

    async def fake_login(*a, **k):
        return "tok"

    monkeypatch.setattr("app.services.ai_acc_fetcher._login", fake_login)
    monkeypatch.setattr(settings, "UPLOAD_DIR", "/tmp/ai_acc_test_uploads")

    real_export_path = "app.services.ai_acc_fetcher._export_chunk"
    call_n = {"n": 0}

    # 用闭包: 第一片(整窗唯一片的子区间)成功, 但我们手动构造 2 天窗口切成 2 片?
    # 简化: 构造 8 天窗口 → 7+1 两片, 第一片永久失败, 第二片成功
    async def fake_export(client, base_url, token, start, end):
        call_n["n"] += 1
        # 永久失败首片(覆盖 9/1~9/8), 第二片(9/8~9/9)成功
        if call_n["n"] == 1:
            raise ClassifiedFetchError(FetchErrorClass.PERMANENT, "403 forbidden")
        return make_xlsx()

    monkeypatch.setattr(real_export_path, fake_export)
    task = {
        "data_type": "cs_record",
        "start": datetime(2026, 9, 1),
        "end": datetime(2026, 9, 9),  # 8 天 → 2 片(7+1)
        "config": {"base_url": "http://x", "username": "u", "password": "p"},
    }
    fr = await fetch_one(task)
    # 第二片成功 → 部分成功(success=True + partial_failures)
    assert fr.success is True
    assert fr.partial_failures is not None
    assert len(fr.partial_failures) == 1
    assert fr.partial_failures[0]["error_class"] == "permanent"


@pytest.mark.asyncio
async def test_fetch_one_all_fail(monkeypatch, _fast_retry):
    """所有片失败 → success=False + error_class=permanent(含 permanent 片)。"""
    async def fake_login(*a, **k):
        return "tok"

    monkeypatch.setattr("app.services.ai_acc_fetcher._login", fake_login)

    async def fake_export(client, base_url, token, start, end):
        raise ClassifiedFetchError(FetchErrorClass.PERMANENT, "401 bad")

    monkeypatch.setattr("app.services.ai_acc_fetcher._export_chunk", fake_export)
    task = {
        "data_type": "cs_record",
        "start": datetime(2026, 9, 1),
        "end": datetime(2026, 9, 3),
        "config": {"base_url": "http://x", "username": "u", "password": "p"},
    }
    fr = await fetch_one(task)
    assert fr.success is False
    assert fr.error_class == "permanent"


@pytest.mark.asyncio
async def test_fetch_one_day_degrade_recovers(monkeypatch, _fast_retry):
    """7天窗口整窗 transient 失败 → 按日降级重试成功(9/2 事故场景模拟)。

    第一轮 7 天整窗恒 500(模拟上游范围过大瞬时故障) → 按 1 天切分后逐日成功。
    """
    import io

    def make_xlsx():
        buf = io.BytesIO()
        pd.DataFrame({"col": ["x"]}).to_excel(buf, index=False)
        return buf.getvalue()

    async def fake_login(*a, **k):
        return "tok"

    monkeypatch.setattr("app.services.ai_acc_fetcher._login", fake_login)
    monkeypatch.setattr(settings, "UPLOAD_DIR", "/tmp/ai_acc_test_uploads")
    call_log: list[str] = []

    async def fake_export(client, base_url, token, start, end):
        span_days = (end - start).days
        # 首轮(7天整窗)恒失败; 按日降级(1天)成功
        if span_days >= 7:
            raise ClassifiedFetchError(FetchErrorClass.TRANSIENT, "range too large 500")
        call_log.append(f"{start:%Y-%m-%d}")
        return make_xlsx()

    monkeypatch.setattr("app.services.ai_acc_fetcher._export_chunk", fake_export)
    task = {
        "data_type": "cs_record",
        "start": datetime(2026, 9, 1),
        "end": datetime(2026, 9, 8),  # 正好 7 天 → 单片(不触发>7天分片)
        "config": {"base_url": "http://x", "username": "u", "password": "p"},
    }
    fr = await fetch_one(task)
    assert fr.success is True
    # 7 个按日片全部成功 → 无 partial_failures
    assert len(call_log) == 7
    assert fr.partial_failures is None
