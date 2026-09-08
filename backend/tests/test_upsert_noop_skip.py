# -*- coding: utf-8 -*-
"""upsert 只更新变化行测试(2026-09-08, #36 完善)

问题: 共享文档/外部推送每轮全量 upsert, on_conflict_do_update 无条件全字段覆盖,
历史行内容未变也整表重写(updated 虚高为整表行数)。

改动: 加 WHERE 业务字段 IS DISTINCT FROM 入库值, 未变行不更新; RETURNING(xmax=0)
精确区分 insert/update。

本测试锁定:
1. 首次同步 → inserted 计数正确
2. 同内容再同步 → updated=0(内容未变不更新)
3. 修改一个业务字段再同步 → updated=1(变化才更新)
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.external_complaint import ExternalComplaint
from app.services import complaint_sync as cs
from app.services.upsert import _sync_devices

_TZ = timezone.utc


def _mk_row(idx: int, problem: str = "测试问题") -> dict:
    """构造一条 external_complaint 待写行(带 dedup_hash)"""
    return {
        "dedup_hash": f"hash-{idx}",
        "complaint_date": datetime(2026, 9, 1, 7, 0, tzinfo=_TZ),
        "complaint_no": f"NO{idx}",
        "complainant_phone": f"1380000{idx:04d}",
        "device_sn": f"SN{idx:08d}",
        "problem": problem,
        "complaint_category": "退款/售后",
        "platform": "12315",
    }


def _sync(db, rows):
    """同步: 先建设备外键空壳, 再批量 upsert"""
    _sync_devices(rows, "device_sn", db)
    return cs._batch_upsert(db, ExternalComplaint, rows, cs.SyncRowResult())


def test_first_sync_inserts(db):
    """首次同步: 全部插入, inserted 计数正确"""
    result = cs.SyncRowResult()
    rows = [_mk_row(1), _mk_row(2)]
    _sync_devices(rows, "device_sn", db)
    cs._batch_upsert(db, ExternalComplaint, rows, result)
    assert result.inserted == 2
    assert result.updated == 0
    # 本批 hash-* 应有 2 条(库内其他存量不影响)
    n = db.query(ExternalComplaint).filter(ExternalComplaint.dedup_hash.like("hash-%")).count()
    assert n == 2


def test_same_content_no_update(db):
    """同内容再次同步: updated=0(内容未变不更新)"""
    result = cs.SyncRowResult()
    rows = [_mk_row(1)]
    _sync_devices(rows, "device_sn", db)
    cs._batch_upsert(db, ExternalComplaint, rows, result)
    assert result.inserted == 1

    result2 = cs.SyncRowResult()
    cs._batch_upsert(db, ExternalComplaint, [_mk_row(1)], result2)
    assert result2.inserted == 0
    assert result2.updated == 0  # 关键: 内容未变不重写
    n = db.query(ExternalComplaint).filter(ExternalComplaint.dedup_hash.like("hash-%")).count()
    assert n == 1


def test_changed_content_updates(db):
    """业务字段变化再同步: updated=1"""
    rows = [_mk_row(1)]
    _sync_devices(rows, "device_sn", db)
    cs._batch_upsert(db, ExternalComplaint, rows, cs.SyncRowResult())
    result = cs.SyncRowResult()
    # 修改 problem(业务字段)
    changed = _mk_row(1, problem="问题已修改")
    cs._batch_upsert(db, ExternalComplaint, [changed], result)
    assert result.inserted == 0
    assert result.updated == 1
    # 本批 hash-1 仍是 1 条且 problem 已更新
    row = db.query(ExternalComplaint).filter(ExternalComplaint.dedup_hash == "hash-1").one()
    assert row.problem == "问题已修改"
