# -*- coding: utf-8 -*-
"""采集日志来源归类测试(2026-09-08, #33 完善)

共享文档在线采集(shared_doc/tencent_doc)与 workbuddy 外部 JSON 推送是两类采集方式,
统一采集日志不应把共享文档采集混标为"外部推送"。
"""

from __future__ import annotations

from app.api.v1.collection_logs import _classify_sync_source


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
