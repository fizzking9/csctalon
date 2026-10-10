# -*- coding: utf-8 -*-
"""B2-6 (#241 / R-P1-16): 采集「尝试心跳 vs 成功水位」分离。

锁定:
- due_wait_minutes: 成功按 interval(下限 5); failed 取 min(interval, 失败重试上限),
  60min 源封到 30min(下一高频槽位重试), 20min 源不被拖慢。
- is_due: 基准 last_attempt_at(回退 last_sync_at); 首次恒到期; failed 提前到期。
- stamp_attempt: 成败都刷 attempt; 只有非 failed 刷成功水位 last_sync_at。
- 集成: due_doc_sources 对 failed 源按 30min 重试(旧实现要等满 60min)。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.doc_sync_source import DocSyncSource
from app.services import shared_doc_sync_service as sdss
from app.services.sync_watermark import due_wait_minutes, is_due, stamp_attempt

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
CAP = 30


# ── due_wait_minutes ─────────────────────────────────────────────────
@pytest.mark.parametrize("status,interval,expected", [
    ("success", 60, 60),
    ("partial", 60, 60),
    ("skipped", 60, 60),
    (None, 60, 60),          # 无状态(等同正常节流)
    (None, None, 60),        # interval 缺失 → 兜底 60
    ("success", 1, 5),       # interval 下限 5
    ("failed", 60, 30),      # 关键: 60min 源失败后封顶 30min
    ("failed", 120, 30),     # 更长档同样封到 30
    ("failed", 20, 20),      # 20min 档不被失败拖慢(min(20,30)=20)
    ("failed", 1, 5),        # 下限仍生效
])
def test_due_wait_minutes(status, interval, expected):
    assert due_wait_minutes(status, interval, CAP) == expected


def test_due_wait_failed_respects_smaller_cap():
    # cap 配得比 interval/默认都小(且不低于 5 下限)时以 cap 为准
    assert due_wait_minutes("failed", 60, 10) == 10
    assert due_wait_minutes("failed", 60, 1) == 5  # cap 也有 5 下限


# ── is_due ───────────────────────────────────────────────────────────
def test_is_due_first_run_when_no_watermark():
    assert is_due(last_attempt_at=None, last_sync_at=None, last_status=None,
                  interval_minutes=60, now=NOW, failed_retry_cap_minutes=CAP) is True


def test_is_due_prefers_attempt_over_sync():
    # attempt 是 1 分钟前(未到 60), 即便 sync 是很久以前也不到期(节流看尝试心跳)
    assert is_due(last_attempt_at=NOW - timedelta(minutes=1),
                  last_sync_at=NOW - timedelta(days=2), last_status="success",
                  interval_minutes=60, now=NOW, failed_retry_cap_minutes=CAP) is False


def test_is_due_falls_back_to_sync_when_attempt_missing():
    # 老行/回填边界: attempt 为空时回退成功水位判定
    assert is_due(last_attempt_at=None, last_sync_at=NOW - timedelta(minutes=61),
                  last_status=None, interval_minutes=60, now=NOW,
                  failed_retry_cap_minutes=CAP) is True
    assert is_due(last_attempt_at=None, last_sync_at=NOW - timedelta(minutes=10),
                  last_status=None, interval_minutes=60, now=NOW,
                  failed_retry_cap_minutes=CAP) is False


def test_is_due_failed_retires_earlier_than_success():
    # 同是 31 分钟前的一次尝试 + 60min 档:
    # failed → 已过 30min 失败重试上限 → 到期(旧实现按 60min 不会到期 = 静默延迟)
    args = dict(last_attempt_at=NOW - timedelta(minutes=31),
                last_sync_at=NOW - timedelta(days=1), interval_minutes=60,
                now=NOW, failed_retry_cap_minutes=CAP)
    assert is_due(last_status="failed", **args) is True
    assert is_due(last_status="success", **args) is False
    # 29 分钟尚未到失败重试窗口
    assert is_due(last_status="failed",
                  last_attempt_at=NOW - timedelta(minutes=29),
                  last_sync_at=NOW - timedelta(days=1), interval_minutes=60,
                  now=NOW, failed_retry_cap_minutes=CAP) is False


# ── stamp_attempt ────────────────────────────────────────────────────
def _row(**kw):
    base = dict(last_attempt_at=None, last_sync_at=None, last_sync_status=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_stamp_success_updates_both_watermarks():
    r = _row(last_sync_at=NOW - timedelta(days=1))
    stamp_attempt(r, "success", NOW)
    assert r.last_attempt_at == NOW
    assert r.last_sync_at == NOW  # 成功水位前进


@pytest.mark.parametrize("status", ["partial", "skipped"])
def test_stamp_non_failed_statuses_advance_sync(status):
    r = _row(last_sync_at=NOW - timedelta(days=1))
    stamp_attempt(r, status, NOW)
    assert r.last_attempt_at == NOW and r.last_sync_at == NOW


def test_stamp_failed_keeps_last_success_watermark():
    old_success = NOW - timedelta(hours=2)
    r = _row(last_sync_at=old_success)
    stamp_attempt(r, "failed", NOW)
    assert r.last_attempt_at == NOW       # 尝试心跳刷新
    assert r.last_sync_at == old_success  # 成功水位不被失败污染


def test_stamp_failed_with_never_succeeded_leaves_sync_none():
    r = _row(last_sync_at=None)
    stamp_attempt(r, "failed", NOW)
    assert r.last_attempt_at == NOW
    assert r.last_sync_at is None


# ── 集成: shared_doc due_doc_sources 真接线 ──────────────────────────
@pytest.fixture(autouse=True)
def _no_commit(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)


def _make_source(db, **kw):
    row = DocSyncSource(
        platform=sdss.PLATFORM_TENCENT,
        doc_url="https://docs.qq.com/sheet/B26WATERMARK0001",
        doc_name="水位测试(测试)",
        target_type=sdss.TARGET_COMPLAINT,
        enabled=True,
        sync_interval_minutes=60,
    )
    for k, v in kw.items():
        setattr(row, k, v)
    db.add(row)
    db.flush()
    return row


def test_due_failed_source_retries_at_30min(db):
    """60min 档源失败后: 31min 到期(下一槽位重试), 10min 未到期。"""
    src = _make_source(
        db,
        last_sync_status="failed",
        last_attempt_at=datetime.now(timezone.utc) - timedelta(minutes=31),
        last_sync_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    assert any(x.id == src.id for x in sdss.due_doc_sources(db)) is True

    src.last_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.flush()
    assert any(x.id == src.id for x in sdss.due_doc_sources(db)) is False


def test_due_success_source_still_uses_full_interval(db):
    """对照: 成功源 31min 未到 60min interval → 不到期(只有 failed 才提前)。"""
    src = _make_source(
        db,
        last_sync_status="success",
        last_attempt_at=datetime.now(timezone.utc) - timedelta(minutes=31),
        last_sync_at=datetime.now(timezone.utc) - timedelta(minutes=31),
    )
    assert any(x.id == src.id for x in sdss.due_doc_sources(db)) is False
