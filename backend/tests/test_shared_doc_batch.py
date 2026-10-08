# -*- coding: utf-8 -*-
"""B3-9 (#248) 共享文档批次台账测试"""

from app.models.upload import UploadRecord
from app.services.shared_doc_sync_service import _record_shared_doc_batch


class TestSharedDocBatchRecord:
    """共享表格通道质量留痕与手工通道对等"""

    def test_batch_record_written(self, db):
        rec = _record_shared_doc_batch(
            db, data_type="complaint", source_file="投诉表.xlsx",
            total=10, inserted=8, updated=2, failed=0,
            status="success", errors=None,
        )
        db.flush()
        assert rec.id is not None
        assert rec.source == "shared_doc"
        assert rec.status == "imported"
        assert rec.total_rows == 10 and rec.inserted_rows == 8 and rec.updated_rows == 2

    def test_batch_record_failed_status(self, db):
        rec = _record_shared_doc_batch(
            db, data_type="manual_refund", source_file="退款表.xlsx",
            total=5, inserted=0, updated=0, failed=5,
            status="failed", errors=["row 2 invalid"],
        )
        db.flush()
        assert rec.status == "failed"
        assert rec.error_detail == {"errors": ["row 2 invalid"]}

    def test_query_by_source(self, db):
        _record_shared_doc_batch(
            db, data_type="complaint", source_file="a.xlsx",
            total=1, inserted=1, updated=0, failed=0,
            status="success", errors=None,
        )
        db.flush()
        rows = db.query(UploadRecord).filter(UploadRecord.source == "shared_doc").all()
        assert len(rows) == 1 and rows[0].filename == "a.xlsx"
