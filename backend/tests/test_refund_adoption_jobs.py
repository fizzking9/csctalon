# -*- coding: utf-8 -*-
"""TR-6.1~6.3 治理作业并发互斥与调度接入 (#332 T6)

- TR-6.1: 另一会话持锁时, 作业返回 skipped 且 std 表不被触碰; 释放后可正常运行
- TR-6.2: 调度接入源码漂移守卫(_do_scheduled_sync 尾部调用 + 两作业触发顺序)
- TR-6.3: 运行留痕 system_event 字段完整(trigger/五路计数/分段/overlap/fake/耗时)
"""

from __future__ import annotations

import inspect
import json
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.governance.jobs import (
    LOCK_KEY_REFUND_ADOPTION,
    run_adoption_job,
    run_dim_device_merchant_job,
)

_TS = "2026-10-16 10:00:00+08"


def _seed_c(db) -> None:
    """C 路一行(防御性清理: 作业内部 commit 落持久测试库, 先删自身键)。"""
    db.execute(text("DELETE FROM ods.shanmao_refunds WHERE refund_no = 'RC-JOB1'"))
    db.execute(text("""
        INSERT INTO ods.shanmao_refunds
            (refund_no, status, actual_amount, refund_amount, refund_time)
        VALUES ('RC-JOB1', '已退款', 9.90, 10.00, :ts)
    """), {"ts": _TS})
    db.flush()


# ── TR-6.1 并发占用锁 → skipped; std 表无损坏 ───────────────────────────────
def test_adoption_job_skipped_when_locked(db):
    _seed_c(db)
    # 另一会话持会话级锁(与事务级锁同 key 互斥), 模拟「作业正在运行」
    other = Session(bind=db.get_bind())
    try:
        got = other.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY_REFUND_ADOPTION}
        ).scalar()
        assert got, "测试前提: 另一会话应能拿到锁"

        res = run_adoption_job(db, routes="C", trigger="test_concurrent")
        assert res["skipped"] is True
        assert res["reason"] == "already_running"
        assert "routes" not in res  # 未执行, 无作业结果
        # 被跳过时不写成功留痕
        n = db.execute(text("""
            SELECT count(*) FROM system_events
            WHERE category = 'governance' AND action = 'refund_adoption_run'
              AND status = 'success' AND detail LIKE '%test_concurrent%'
        """)).scalar()
        assert n == 0
    finally:
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY_REFUND_ADOPTION})
        other.close()

    # 锁释放后同一作业可正常跑通, std 有该行
    res2 = run_adoption_job(db, routes="C", trigger="test_after_unlock")
    assert "routes" in res2 and res2["routes"]["C"]["adopted"] >= 1
    cnt = db.execute(text(
        "SELECT count(*) FROM std_refund_shanmao WHERE refund_no = 'RC-JOB1'"
    )).scalar()
    assert cnt == 1


def test_dim_job_skipped_when_locked(db):
    from app.services.governance.jobs import LOCK_KEY_DIM_DEVICE_MERCHANT
    other = Session(bind=db.get_bind())
    try:
        other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY_DIM_DEVICE_MERCHANT})
        res = run_dim_device_merchant_job(db, trigger="test_concurrent")
        assert res["skipped"] is True
    finally:
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY_DIM_DEVICE_MERCHANT})
        other.close()


# ── TR-6.2 调度接入源码漂移守卫 ─────────────────────────────────────────────
def test_scheduler_hook_drift_guard():
    import app.services.bms_scheduler as sched

    src = inspect.getsource(sched)
    # ① _do_scheduled_sync 尾部必须触发治理作业(函数体在缺口记账之后)
    m = re.search(r"async def _do_scheduled_sync\(.*?\n(.*?)\n    def ", src, re.S)
    assert m, "_do_scheduled_sync 未找到"
    body = m.group(1)
    assert "record_from_sync_results" in body, "缺口记账调用缺失(基线锚点)"
    assert "_run_refund_governance" in body, "治理作业触发缺失: 同步成功后未调用"
    assert body.index("_run_refund_governance") > body.index("record_from_sync_results"), \
        "治理作业必须在缺口记账之后触发"
    # ② 触发顺序: 采纳作业 → dim 重建
    gov = inspect.getsource(sched.BmsScheduler._run_refund_governance)
    assert gov.index("run_adoption_job") < gov.index("run_dim_device_merchant_job"), \
        "触发顺序被改: 必须先采纳作业, 后 dim_device_merchant 重建"
    # ③ 五路源码集合锚点(改名即失败)
    for code in ('"refund"', '"wallet"', '"shanmao_refund"', '"manual_refund"', '"refund_record_report"'):
        assert code in body, f"退款路源码 {code} 从触发集合中消失"


# ── TR-6.3 运行留痕字段完整 ─────────────────────────────────────────────────
def test_adoption_job_event_trail(db):
    _seed_c(db)
    res = run_adoption_job(db, routes="C", trigger="test_trail")
    assert res["trigger"] == "test_trail" and res["elapsed_ms"] >= 0

    row = db.execute(text("""
        SELECT title, summary, status, detail FROM system_events
        WHERE category = 'governance' AND action = 'refund_adoption_run'
          AND detail LIKE '%test_trail%' AND status = 'success'
        ORDER BY id DESC LIMIT 1
    """)).one()
    detail = json.loads(row.detail)
    assert detail["trigger"] == "test_trail"
    assert detail["started_at"] and detail["finished_at"]
    assert detail["elapsed_ms"] >= 0
    c = detail["routes"]["C"]
    # 五路计数语义: total/adopted/excluded/pending/std_rows/std_amount 齐全
    for k in ("total", "adopted", "excluded", "pending", "std_rows", "std_amount", "elapsed_ms"):
        assert k in c, f"留痕缺字段 {k}"
    assert "C=" in row.summary and "adopt" in row.summary


def test_dim_job_event_trail(db):
    res = run_dim_device_merchant_job(db, trigger="test_trail")
    assert res["trigger"] == "test_trail"
    row = db.execute(text("""
        SELECT summary, status, detail FROM system_events
        WHERE category = 'governance' AND action = 'dim_device_merchant_rebuild'
          AND detail LIKE '%test_trail%' AND status = 'success'
        ORDER BY id DESC LIMIT 1
    """)).one()
    detail = json.loads(row.detail)
    for k in ("segments", "devices", "overlap_devices", "arbiter_picks", "fake_sns", "elapsed_ms"):
        assert k in detail, f"维表留痕缺字段 {k}"
    assert "分段" in row.summary and "假SN" in row.summary
