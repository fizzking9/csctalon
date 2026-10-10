# -*- coding: utf-8 -*-
"""拒收行(未采纳数据)服务/API 测试(A-20260908-003, #40)

覆盖:
1. 服务: 写入拒收行(open) → 列表可按状态/类型筛 → 人工判定 keep/discard
2. 统计: open 计数 / 各源分布
3. 语义: 未判定(open)/discard 默认不纳入(供统计过滤器使用的前提)
"""

from __future__ import annotations

import pytest
from sqlalchemy import and_, func, select

from tests._db_guard import safe_wipe


def _write(db, **kw):
    from app.services.ingest_rejected_service import add_rejected_row

    defaults = dict(data_type="external_complaint", source="workbuddy_pull",
                    reject_category="validate_fail", reject_reason="行解析失败",
                    raw_row={"编号": "X1", "手机号": "13800000000"}, business_key="X1")
    defaults.update(kw)
    return add_rejected_row(db, **defaults)


def test_add_and_list_rejected(db):
    """写入 open 拒收行 → 列表可见(默认未判定)。"""
    from app.services.ingest_rejected_service import list_rejected_rows

    _write(db, business_key="RF-A1")
    _write(db, business_key="RF-A2", source="upload", reject_category="parse_fail")
    db.flush()

    res = list_rejected_rows(db)
    assert res["total"] >= 2
    # 本次新写入的都是 open(测试库可能含其它测试历史 keep/discard 行, 只验证本条)
    new_items = [x for x in res["items"] if x["business_key"] in ("RF-A1", "RF-A2")]
    assert all(x["status"] == "open" for x in new_items)

    # 按类型/源筛选
    res2 = list_rejected_rows(db, source="upload")
    assert all(x["source"] == "upload" for x in res2["items"])
    # raw_row 完整保留
    target = next(x for x in res2["items"] if x["business_key"] == "RF-A2")
    assert target["raw_row"]["编号"] == "X1"


def test_review_keep_and_discard(db):
    """人工判定: keep(采纳)/discard(不纳入)。"""
    from app.services.ingest_rejected_service import list_rejected_rows, review_rejected_row

    row = _write(db, business_key="RF-B1")
    db.flush()
    rid = row.id

    # keep
    updated = review_rejected_row(db, rid, "keep", reviewer="tester")
    assert updated["status"] == "keep"
    assert updated["reviewed_by"] == "tester"

    # discard 另一条
    row2 = _write(db, business_key="RF-B2")
    db.flush()
    review_rejected_row(db, row2.id, "discard", reviewer="tester")

    by_status = list_rejected_rows(db, status="keep")
    assert any(x["id"] == rid for x in by_status["items"])


def test_invalid_action_rejected(db):
    """非法 action 抛 ValueError。"""
    from app.services.ingest_rejected_service import review_rejected_row

    row = _write(db)
    db.flush()
    with pytest.raises(ValueError):
        review_rejected_row(db, row.id, "invalid", reviewer="tester")


def test_rejected_stats(db):
    """统计: 各源/状态计数。"""
    from app.services.ingest_rejected_service import rejected_stats

    _write(db, business_key="RF-C1", source="workbuddy_pull")
    _write(db, business_key="RF-C2", source="upload")
    db.flush()

    stats = rejected_stats(db)
    assert stats["open"] >= 2
    assert stats["by_source"].get("workbuddy_pull", 0) >= 1
    assert stats["by_source"].get("upload", 0) >= 1


def test_complaint_sync_writes_rejected_on_parse_fail(_engine):
    """投诉推送遇非对象行 → 落拒收表(parse_fail)。

    注意: sync_complaints_from_payload 内部 commit, 会真实落库,
    必须用独立 session + teardown 精确清理, 避免污染共享表
    (曾污染 complaint_sync_logs 导致 test_data_arrival_health 误判)。
    """
    from sqlalchemy.orm import Session

    from app.models.complaint_sync_log import ComplaintSyncLog
    from app.models.external_complaint import ExternalComplaint
    from app.models.ingest_rejected_row import IngestRejectedRow

    session = Session(_engine, autocommit=False, autoflush=False)
    try:
        payload = {"external_complaints": ["不是对象"]}
        from app.services.complaint_sync import sync_complaints_from_payload

        sync_complaints_from_payload(session, payload, source_file="test-rejected.xlsx", sync_source="workbuddy")

        cnt = session.scalar(
            select(func.count()).select_from(IngestRejectedRow).where(
                IngestRejectedRow.data_type == "external_complaint",
                IngestRejectedRow.reject_category == "parse_fail",
            )
        ) or 0
        assert cnt >= 1
    finally:
        # 精确清理本次测试创建的数据(避免污染共享表)
        try:
            safe_wipe(
                session, IngestRejectedRow,
                where=and_(
                    IngestRejectedRow.source_ref.is_(None),
                    IngestRejectedRow.source_row_no == 0,
                ),
            )
            safe_wipe(session, ComplaintSyncLog, where=ComplaintSyncLog.source_file == "test-rejected.xlsx")
            # 本次只推了"不是对象"非 dict 行, 无成功 external 行入库; 若失败仍有残留做兜底清理
            safe_wipe(session, ExternalComplaint, where=ExternalComplaint.dedup_hash.like("test-rejected%"))
            session.commit()
        except Exception:
            session.rollback()
        session.close()


def test_upload_run_upsert_writes_rejected_on_bad_row(_engine):
    """上传(run_upsert)遇坏退费单号(带 raw data) → 落拒收表 validate_fail。

    run_upsert 内部 commit, 用独立 session + teardown 精确清理(防污染共享表)。
    """
    from sqlalchemy.orm import Session

    from app.models.ingest_rejected_row import IngestRejectedRow
    from app.models.refund import Refund

    session = Session(_engine, autocommit=False, autoflush=False)
    try:
        import pandas as pd

        from app.services.data_parser import suggest_mapping
        from app.services.upsert import run_upsert

        # 坏退费单号(含中文) → upsert_refunds add_error 且带 data
        df = pd.DataFrame([
            {"退费单号": "RF-坏单号-测试", "退费金额": "10.00", "操作人": "张三"},
        ])
        mapping = suggest_mapping("refund", list(df.columns))
        result = run_upsert(df, "refund", mapping, session)

        assert result.failed >= 1
        cnt = session.scalar(
            select(func.count()).select_from(IngestRejectedRow).where(
                IngestRejectedRow.data_type == "refund",
                IngestRejectedRow.reject_category == "validate_fail",
                IngestRejectedRow.source == "upload",
            )
        ) or 0
        assert cnt >= 1, "坏退费单号应落拒收表"
    finally:
        # 精确清理(该坏单号不会入库; 兜底清理可能写入的拒收行)
        try:
            safe_wipe(
                session, IngestRejectedRow,
                where=and_(
                    IngestRejectedRow.data_type == "refund",
                    IngestRejectedRow.source == "upload",
                ),
            )
            safe_wipe(session, Refund, where=Refund.refund_no.like("RF-坏单号%"))
            session.commit()
        except Exception:
            session.rollback()
        session.close()
