# -*- coding: utf-8 -*-
"""abc-c 契约回归锁: 拒收/校验信息必须端到端可见 (#42)

## 背景
服务层早就能产出 `rejected`(未采纳行数)与 `validation`(入库前校验问题),
但**同步日志表未持久化** → 采集日志/推送页只能看「总行/新增/更新/失败」,
「有多少行被拒收待判定」「有多少校验告警」在历史记录里查不到(abc-c 缺口)。

## 本测试锁定三层接线(任一断开则前端必然显示不出来)
1. 模型层: 两张同步日志表有 rejected / validation_count / validation_severity 列
2. 写入层: 同步流程真的把这些值写进日志对象
3. 读出层: 列表接口真的把这些字段吐给前端(且存量 NULL 有兜底)
"""
from __future__ import annotations

import inspect


# ---------- 1. 模型层 ----------
class TestModelColumns:
    def test_complaint_log_has_fields(self):
        from app.models.complaint_sync_log import ComplaintSyncLog

        cols = {c.key for c in ComplaintSyncLog.__table__.columns}
        for f in ("rejected", "validation_count", "validation_severity"):
            assert f in cols, f"complaint_sync_logs 缺列 {f}"

    def test_manual_refund_log_has_fields(self):
        from app.models.manual_refund_sync_log import ManualRefundSyncLog

        cols = {c.key for c in ManualRefundSyncLog.__table__.columns}
        for f in ("rejected", "validation_count", "validation_severity"):
            assert f in cols, f"manual_refund_sync_logs 缺列 {f}"

    def test_columns_nullable(self):
        """必须可空: 存量行没有这些值, 加 NOT NULL 会导致迁移失败"""
        from app.models.complaint_sync_log import ComplaintSyncLog

        for name in ("rejected", "validation_count", "validation_severity"):
            col = ComplaintSyncLog.__table__.columns[name]
            assert col.nullable is True, f"{name} 必须可空(存量数据兼容)"


# ---------- 2. 迁移 ----------
class TestMigration:
    def test_migration_exists_and_is_idempotent(self):
        """迁移必须存在且用 information_schema 做幂等检查

        (测试库常由 Base.metadata.create_all 建表 → 列已存在,
         非幂等 add_column 会报 DuplicateColumn)
        """
        from pathlib import Path

        mig = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        f = mig / "e9f0a1b2c3d4_add_rejected_validation_to_sync_logs.py"
        assert f.exists(), "缺少 abc-c 迁移文件"
        src = f.read_text(encoding="utf-8")
        assert "information_schema.columns" in src, "迁移未做列存在性检查(非幂等)"
        assert "complaint_sync_logs" in src and "manual_refund_sync_logs" in src


# ---------- 3. 写入层 ----------
class TestWriteLayer:
    def test_complaint_sync_persists(self):
        """投诉同步必须把拒收/校验写入日志对象(而非只放返回值)"""
        from app.services import complaint_sync as cs

        src = inspect.getsource(cs)
        for f in ("log.rejected", "log.validation_count", "log.validation_severity"):
            assert f in src, f"complaint_sync 未写入 {f}"

    def test_manual_refund_sync_persists(self):
        from app.services import manual_refund_sync as mrs

        src = inspect.getsource(mrs)
        for f in ("log.rejected", "log.validation_count", "log.validation_severity"):
            assert f in src, f"manual_refund_sync 未写入 {f}"

    def test_validation_not_written_to_error_column(self):
        """★校验告警不得写进 error 列

        语义: error 是"同步失败原因"; 校验告警不是失败(推送可 success 且有告警)。
        混入会让"错误"列在成功记录里显示文本 —— #42 复审 N-2 曾因此回退。
        """
        from app.services import complaint_sync as cs

        src = inspect.getsource(cs)
        # 找到校验相关的赋值处, 其后不应出现 log.error = <validation...>
        assert "log.validation_count = len(_all_val)" in src
        assert "log.error = str(_all_val)" not in src


# ---------- 4. 读出层 ----------
class TestReadLayer:
    def test_sync_complaints_api_exposes(self):
        from app.api.v1 import sync_complaints as m

        src = inspect.getsource(m)
        for f in ("rejected", "validation_count", "validation_severity"):
            assert f'"{f}"' in src, f"投诉日志列表接口未返回 {f}"

    def test_sync_manual_refunds_api_exposes(self):
        from app.api.v1 import sync_manual_refunds as m

        src = inspect.getsource(m)
        for f in ("rejected", "validation_count", "validation_severity"):
            assert f'"{f}"' in src, f"手动退款日志接口未返回 {f}"

    def test_collection_logs_api_exposes(self):
        """统一采集日志接口(用户主要浏览入口)也要暴露"""
        from app.api.v1 import collection_logs as m

        src = inspect.getsource(m)
        for f in ("rejected_rows", "validation_count", "validation_block", "validation_warn"):
            assert f in src, f"采集日志接口未返回 {f}"

    def test_collection_logs_schema_fields(self):
        from app.api.v1.collection_logs import CollectionLogItem

        fields = set(CollectionLogItem.model_fields)
        for f in ("rejected_rows", "validation_count", "validation_block", "validation_warn"):
            assert f in fields, f"CollectionLogItem 缺字段 {f}"

    def test_null_fallback_for_legacy_rows(self):
        """★存量行该列为 NULL → 接口必须兜底为 0/{}, 否则前端拿到 null 显示异常"""
        from app.api.v1 import sync_complaints as m

        src = inspect.getsource(m)
        assert "log.rejected or 0" in src
        assert "log.validation_count or 0" in src
        assert "log.validation_severity or {}" in src


# ---------- 5. 语义分离 ----------
class TestSemantics:
    def test_rejected_and_validation_are_distinct(self):
        """拒收行(未采纳) 与 校验告警(已入库) 是两件事, 应分别计数

        - rejected: 行未进业务表, 落拒收表待人工判定, 不纳入统计
        - validation: 行已进业务表并打标 pending, 进入治理队列
        """
        from app.services.ingest_validator import SEV_BLOCK, SEV_WARN, summarize_severity

        sev = summarize_severity([
            {"rule": "R-必填", "severity": SEV_BLOCK},
            {"rule": "R-时间", "severity": SEV_WARN},
        ])
        assert sev == {"block": 1, "warn": 1}
