# -*- coding: utf-8 -*-
"""分析引擎模型注册一致性测试(2026-09-06 审查补, #20)

背景: preset_views_seed 预设视图的 segment 引用 data_source(code), 引擎执行时按
DataSource.table_name 在 _MODEL_REGISTRY 反射列。曾出现 refund_record_report /
scrm_session / scrm_session_transfer 已注册 data_source 并被 preset 引用,
但引擎 _MODEL_REGISTRY 缺失 → 执行报"模型未注册"。

本测试锁定: 所有被预设视图引用的 data_source, 其模型必须已注册到引擎,
防止再次出现"预设视图在引擎侧不可执行"的缺口。
"""

from __future__ import annotations

from app.services.analysis_engine import _MODEL_REGISTRY
from app.services.data_source_registry import DATA_SOURCE_SEEDS
from app.services.preset_views_seed import PRESET_VIEWS


def _code_to_table() -> dict[str, str]:
    return {s["code"]: s["model"].__tablename__ for s in DATA_SOURCE_SEEDS}


def test_all_preset_sources_registered_in_engine():
    """预设视图引用的每个 data_source 模型都必须注册到引擎 _MODEL_REGISTRY"""
    code2table = _code_to_table()
    used_codes = {
        seg["source_code"]
        for v in PRESET_VIEWS
        for seg in v.get("segments", [])
    }
    missing = [
        code for code in sorted(used_codes)
        if code2table.get(code) not in _MODEL_REGISTRY
    ]
    assert not missing, f"预设视图引用但引擎未注册的 data_source: {missing}"


def test_refund_record_report_registered():
    """E 路退款记录报表源必须在引擎注册表(2026-09-05 E 路切换后依赖)"""
    code2table = _code_to_table()
    assert code2table["refund_record_report"] in _MODEL_REGISTRY


def test_registry_models_have_time_and_status_cols():
    """E 路模型列可被引擎反射: refund_time/refund_status/refund_amount 存在"""
    from app.models.refund_record_report import RefundRecordReport

    cols = {c.name for c in RefundRecordReport.__table__.columns}
    assert {"refund_time", "refund_status", "refund_amount", "merchant_id", "device_sn"} <= cols
