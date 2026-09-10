# -*- coding: utf-8 -*-
"""统一入库接口测试 (#42, 2026-09-10)

背景: 7 类采集入口原先各调各的函数, 清洗双份实现、返回值 3 种结构混用。
本模块提供统一入口(services/ingest_service), 统一「入口形状 + 返回值 + 上下文」,
使所有入口改调后返回值结构一致、来源可追溯。

本测试锁定:
1. 注册表: 已注册类型可取规格; 未注册类型必须报错(强制新数据源先注册)
2. IngestResult 统一结构: to_dict 字段完整、列表截断 50、status 语义
3. ingest_table / ingest_complaints / ingest_manual_refunds: 正确透传既有管线结果
   (用 monkeypatch 替换底层, 避免依赖真实 DB 写入)
"""

from __future__ import annotations

import pytest

from app.services.ingest_service import (
    DATA_TYPE_REGISTRY,
    IngestResult,
    get_spec,
    ingest_complaints,
    ingest_manual_refunds,
    ingest_table,
)


# ---------- 1. 注册表 ----------
class TestRegistry:
    def test_registered_types_have_spec(self):
        """已注册类型能取到规格, 且 channel 合法"""
        for dt in ("refund", "ticket", "external_complaint", "card_complaint", "manual_refund"):
            spec = get_spec(dt)
            assert spec.channel in ("table", "complaint", "manual_refund")

    def test_unregistered_type_raises(self):
        """未注册类型必须报错(强制新数据源先注册, 防散落入库)"""
        with pytest.raises(ValueError, match="未注册的数据类型"):
            get_spec("no_such_type")

    def test_complaint_types_use_complaint_channel(self):
        assert get_spec("external_complaint").channel == "complaint"
        assert get_spec("card_complaint").channel == "complaint"
        assert get_spec("manual_refund").channel == "manual_refund"

    def test_registry_not_empty(self):
        assert len(DATA_TYPE_REGISTRY) >= 18


# ---------- 2. 统一返回值结构 ----------
class TestIngestResult:
    def test_to_dict_has_unified_keys(self):
        """to_dict 必须含全部统一字段(前端/日志统一消费)"""
        r = IngestResult(data_type="refund", source="upload", run_id="abc")
        d = r.to_dict()
        for k in ("data_type", "source", "run_id", "status", "total", "inserted",
                  "updated", "deleted", "skipped", "failed", "rejected",
                  "warnings", "errors", "extra"):
            assert k in d, f"缺字段 {k}"

    def test_lists_truncated_to_50(self):
        """明细列表截断 50, 防响应体过大"""
        r = IngestResult(data_type="refund", source="upload", run_id="abc")
        r.errors = [{"row": i} for i in range(120)]
        r.warnings = [{"row": i} for i in range(80)]
        d = r.to_dict()
        assert len(d["errors"]) == 50
        assert len(d["warnings"]) == 50

    def test_failed_is_explicit_field(self):
        """failed 为显式计数(不依赖 errors 长度, 因明细会截断)"""
        r = IngestResult(data_type="refund", source="upload", run_id="abc")
        r.failed = 137
        r.errors = [{"row": i} for i in range(50)]
        assert r.to_dict()["failed"] == 137

    def test_default_status_success(self):
        assert IngestResult(data_type="x", source="y", run_id="z").status == "success"


# ---------- 3. 三个入口函数透传既有管线结果 ----------
class _FakeImportResult:
    """模拟 upsert.ImportResult

    #42 修正: rejected(真正落拒收表的行) 与 failed(硬失败行数) **不同义** ——
    无 raw data 的失败行无法按行展示, 不计入 rejected。
    """

    def __init__(self):
        self.total, self.inserted, self.updated, self.failed = 10, 7, 2, 1
        self.rejected = 0  # 本行 errors 无 data → 不落拒收表
        self.errors = [{"row": 3, "message": "解析失败"}]
        self.warnings = [{"row": 5, "message": "姓名列被清洗"}]


class TestIngestTable:
    def test_maps_import_result(self, monkeypatch):
        """ingest_table: 把 upsert.ImportResult 映射为统一 IngestResult"""
        import app.services.upsert as up

        monkeypatch.setattr(up, "run_upsert", lambda df, dt, mp, db: _FakeImportResult())
        res = ingest_table(
            db=None, data_type="refund",
            df=None, mapping={}, source="upload", source_ref="upload_record:1",
        )
        assert (res.total, res.inserted, res.updated, res.failed) == (10, 7, 2, 1)
        # failed=1 但 rejected=0(该失败行无 raw data, 无法按行展示) → 二者不得混同
        assert res.failed == 1
        assert res.rejected == 0
        assert res.errors  # 硬失败明细保留
        assert res.status == "partial"    # 有失败 → partial
        assert res.extra["source_ref"] == "upload_record:1"
        assert res.extra["label"] == "退费单"

    def test_rejected_reported_separately(self, monkeypatch):
        """rejected 取真实拒收计数(与 failed 解耦)"""
        import app.services.upsert as up

        class _R(_FakeImportResult):
            def __init__(self):
                super().__init__()
                self.failed = 5
                self.rejected = 3  # 其中 3 条真正落拒收表

        monkeypatch.setattr(up, "run_upsert", lambda df, dt, mp, db: _R())
        res = ingest_table(db=None, data_type="refund", df=None, mapping={}, source="upload")
        assert res.failed == 5
        assert res.rejected == 3

    def test_success_when_no_failure(self, monkeypatch):
        """无失败 → status=success"""
        import app.services.upsert as up

        class _Ok(_FakeImportResult):
            def __init__(self):
                super().__init__()
                self.failed = 0
                self.errors = []

        monkeypatch.setattr(up, "run_upsert", lambda df, dt, mp, db: _Ok())
        res = ingest_table(db=None, data_type="refund", df=None, mapping={}, source="bms")
        assert res.status == "success"
        assert res.rejected == 0

    def test_unregistered_type_raises_before_run(self):
        """未注册类型在调用入库前即报错(不做事后补救)"""
        with pytest.raises(ValueError, match="未注册的数据类型"):
            ingest_table(db=None, data_type="ghost", df=None, mapping={}, source="upload")


class TestIngestComplaints:
    def test_maps_two_sheets(self, monkeypatch):
        """ingest_complaints: 汇总 315 + 卡商 两 sheet 统计"""
        import app.services.complaint_sync as cs

        def _fake_sync(db, payload, source_file=None, sync_source="workbuddy"):
            return {
                "log_id": 77, "status": "partial",
                "external": {"total": 5, "inserted": 4, "updated": 1, "failed": 0,
                             "filtered": 2, "matched": 3, "warnings": 0, "errors": []},
                "card": {"total": 3, "inserted": 3, "updated": 0, "failed": 1,
                         "filtered": 1, "matched": 0, "warnings": 0,
                         "errors": [{"row": 1, "level": "error"}]},
            }

        monkeypatch.setattr(cs, "sync_complaints_from_payload", _fake_sync)
        res = ingest_complaints(db=None, payload={}, source="workbuddy", source_file="315.xlsx")
        assert res.total == 8            # 5+3
        assert res.inserted == 7         # 4+3
        assert res.updated == 1
        assert res.skipped == 3          # 2+1 空行
        assert res.failed == 1
        assert res.status == "partial"
        assert res.extra["log_id"] == 77
        assert res.extra["external"]["total"] == 5

    def test_contract_fields_preserved(self, monkeypatch):
        """⚠️ 对外契约: external/card 必须含 warnings/errors(审查 CRITICAL 回归锁)

        workbuddy 外部工具依赖这些字段; 白名单遗漏会让其拿到 undefined。
        """
        import app.services.complaint_sync as cs

        def _fake_sync(db, payload, source_file=None, sync_source="workbuddy"):
            return {
                "log_id": 1, "status": "success",
                "external": {"total": 1, "inserted": 1, "updated": 0, "failed": 0,
                             "filtered": 0, "matched": 1, "warnings": 2,
                             "errors": [{"row": 9, "level": "warning"}]},
                "card": {"total": 0, "inserted": 0, "updated": 0, "failed": 0,
                         "filtered": 0, "matched": 0, "warnings": 0, "errors": []},
            }

        monkeypatch.setattr(cs, "sync_complaints_from_payload", _fake_sync)
        res = ingest_complaints(db=None, payload={}, source="workbuddy")
        for key in ("total", "inserted", "updated", "failed", "filtered",
                    "matched", "warnings", "errors"):
            assert key in res.extra["external"], f"external 缺契约字段 {key}"
            assert key in res.extra["card"], f"card 缺契约字段 {key}"
        assert res.extra["external"]["warnings"] == 2
        assert res.extra["external"]["errors"]


class TestIngestManualRefunds:
    def test_maps_result_and_keeps_full_sync(self, monkeypatch):
        """ingest_manual_refunds: 映射统计, 且不篡改调用方给的 full_sync"""
        import app.services.manual_refund_sync as mr

        captured = {}

        def _fake_sync(db, payload, source_file=None, sync_source="workbuddy"):
            captured["payload"] = payload
            captured["sync_source"] = sync_source
            return {
                "log_id": 9, "status": "success",
                "total": 4, "inserted": 3, "updated": 1, "deleted": 0,
                "failed": 0, "filtered": 2, "warnings": 0, "errors": [],
            }

        monkeypatch.setattr(mr, "sync_manual_refunds_from_payload", _fake_sync)
        payload = {"manual_refunds": [{"a": 1}], "full_sync": False}
        res = ingest_manual_refunds(db=None, payload=payload, source="shared_doc")
        assert (res.total, res.inserted, res.updated, res.skipped) == (4, 3, 1, 2)
        assert res.status == "success"
        # ⚠️ 安全约束: full_sync 必须原样透传(在线采集严禁全量对账删除)
        assert captured["payload"]["full_sync"] is False
        assert captured["sync_source"] == "shared_doc"
