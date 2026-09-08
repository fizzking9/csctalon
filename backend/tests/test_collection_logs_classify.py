# -*- coding: utf-8 -*-
"""采集日志来源归类测试(2026-09-08, #33 完善)

共享文档在线采集(shared_doc/tencent_doc)与 workbuddy 外部 JSON 推送是两类采集方式,
统一采集日志不应把共享文档采集混标为"外部推送"。
#36 追加: 同步日志 error(repr 字符串)收敛为友好摘要, warning 不再整段 JSON 展示。
"""

from __future__ import annotations

from app.api.v1.collection_logs import _classify_sync_source, _extract_error_detail


def test_classify_shared_doc():
    """shared_doc → 共享文档采集来源"""
    assert _classify_sync_source("shared_doc") == ("shared_doc", "共享文档采集")


def test_classify_tencent_doc():
    """tencent_doc(旧版腾讯文档API采集) → 共享文档采集来源"""
    assert _classify_sync_source("tencent_doc") == ("shared_doc", "共享文档采集")


def test_classify_workbuddy_push():
    """workbuddy 外部推送 → 外部推送来源"""
    assert _classify_sync_source("workbuddy") == ("push", "外部推送")


def test_classify_manual_or_none():
    """manual / None(历史默认) → 外部推送来源(与旧语义一致)"""
    assert _classify_sync_source("manual") == ("push", "外部推送")
    assert _classify_sync_source(None) == ("push", "外部推送")
    assert _classify_sync_source("") == ("push", "外部推送")

# ── error 收敛展示(#36) ────────────────────────────────────
def test_error_summary_warning_only():
    """仅 warning(清洗提示) → 收敛为提示计数, 不再整段 JSON"""
    raw = "[{'row': 0, 'level': 'warning', 'message': '备注为示例的模板数据行已跳过', 'data': None}, {'row': 1, 'level': 'warning', 'message': '登记人格式异常', 'data': None}]"
    out = _extract_error_detail(raw)
    assert out == "2 条清洗提示(行已保留)"


def test_error_summary_with_error():
    """含 error(硬失败) → 提示错误数 + 清洗提示数"""
    raw = "[{'row': 0, 'level': 'error', 'message': '缺少登记日期或订单号,无法去重定位(跳过)', 'data': None}, {'row': 1, 'level': 'warning', 'message': '登记人格式异常', 'data': None}]"
    out = _extract_error_detail(raw)
    assert "1 条错误" in out
    assert "缺少登记日期" in out
    assert "1 条清洗提示" in out


def test_error_summary_non_list_string():
    """非结构化文本(如异常栈) → 截断保留"""
    out = _extract_error_detail("psycopg2.errors.CardinalityViolation ON CONFLICT ...")
    assert out.startswith("psycopg2.errors")


def test_error_summary_none():
    assert _extract_error_detail(None) is None
    assert _extract_error_detail("") is None
