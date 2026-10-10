# -*- coding: utf-8 -*-
"""注册表能力位主表 + 启动完整性自检测试（工单 #210 / S-P0-2，阶段 A）。

锁定点：
1. 现状零误报：validate_registry_integrity() 对当前四个注册表不抛错
   （等价于 create_app() 启动自检通过——main.py 模块级 app=create_app() 已隐含执行）。
2. 能力位主表汇聚正确：code 全集 = SEEDS ∪ DATA_TYPE_REGISTRY；
   裁定①~③ 的能力位（不上树 / scrm 通道 / internal / 可分析全集 13 张）逐项落实。
3. 失配 fail-fast 可测：构造 DISPATCH 缺映射、_MODEL_REGISTRY 漂移、
   name_cn 重名、无登记失配、冗余登记五类用例断言 RegistryIntegrityError。
"""

from __future__ import annotations

import pytest

from app.services import registry_index as ri
from app.services.analysis_engine import _MODEL_REGISTRY
from app.services.data_source_registry import DATA_SOURCE_SEEDS
from app.services.ingest_service import DATA_TYPE_REGISTRY
from app.services.upsert._runner import UPSERT_DISPATCH


def test_current_registries_pass_validation():
    """现状四个注册表必须零误报通过（启动自检的放行基线）。"""
    ri.validate_registry_integrity()  # 不抛错即通过


def test_registry_index_covers_all_codes():
    index = ri.build_registry_index()
    seed_codes = {s["code"] for s in DATA_SOURCE_SEEDS}
    ingest_codes = set(DATA_TYPE_REGISTRY.keys())
    assert set(index) == seed_codes | ingest_codes
    # 每个 SEEDS 项都解析出物理表名（code↔table_name 桥）
    for code in seed_codes:
        assert index[code].table_name, f"{code} 未解析出 table_name"


def test_capability_bits_match_rulings():
    """裁定①~③的能力位逐项落实（防登记被误删）。"""
    index = ri.build_registry_index()
    # 裁定①: device_profile / merchant 能入库但不上查看树
    for code in ("device_profile", "merchant"):
        e = index[code]
        assert e.ingest == code and e.in_dispatch
        assert not e.in_view_tree and e.note
    # 裁定③: SCRM 三表走专用通道，不经通用采集/UPSERT_DISPATCH
    for code in ("scrm_operator", "scrm_session", "scrm_session_transfer"):
        e = index[code]
        assert e.in_view_tree and e.ingest is None and e.channel == "scrm"
        assert not e.in_dispatch
    # internal 物化宽表: 不采集、不直接分析
    for code in ("cash_flow_wide", "refund_wide"):
        e = index[code]
        assert e.internal and e.channel == "internal" and e.ingest is None
    # 裁定②: 可分析表恰为 _MODEL_REGISTRY 全集（当前 13 张）
    analyzable = {e.code for e in index.values() if e.analyzable}
    assert {index[c].table_name for c in analyzable} == set(_MODEL_REGISTRY)
    assert len(analyzable) == 13


def test_missing_dispatch_mapping_fails(monkeypatch):
    """DATA_TYPE_REGISTRY 有、UPSERT_DISPATCH 无 → fail-fast。"""
    monkeypatch.delitem(UPSERT_DISPATCH, "refund")
    with pytest.raises(ri.RegistryIntegrityError, match="refund"):
        ri.validate_registry_integrity()


def test_extra_dispatch_mapping_fails(monkeypatch):
    """UPSERT_DISPATCH 有、DATA_TYPE_REGISTRY 无 → fail-fast。"""
    monkeypatch.setitem(UPSERT_DISPATCH, "ghost_type", UPSERT_DISPATCH["refund"])
    with pytest.raises(ri.RegistryIntegrityError, match="ghost_type"):
        ri.validate_registry_integrity()


def test_model_registry_drift_fails(monkeypatch):
    """_MODEL_REGISTRY 多出主表没有的表 → fail-fast。"""
    from app.models.device import Device

    monkeypatch.setitem(_MODEL_REGISTRY, Device.__tablename__, Device)
    with pytest.raises(ri.RegistryIntegrityError, match=Device.__tablename__):
        ri.validate_registry_integrity()


def test_duplicate_name_cn_fails(monkeypatch):
    """name_cn 全树唯一（铁律 13）：构造重名 → fail-fast。"""
    first = DATA_SOURCE_SEEDS[0]
    original = first["name_cn"]
    monkeypatch.setitem(first, "name_cn", DATA_SOURCE_SEEDS[1]["name_cn"])
    with pytest.raises(ri.RegistryIntegrityError, match="name_cn 重复"):
        ri.validate_registry_integrity()
    assert first["name_cn"] != original  # 确认确实改成了重名（防误写通过）


def test_unregistered_mismatch_fails(monkeypatch):
    """无登记失配 → fail-fast：临时清掉一条例外登记后，对应 code 必须被报出。"""
    monkeypatch.delitem(ri.CAPABILITY_NOTES, "device_profile")
    with pytest.raises(ri.RegistryIntegrityError, match="device_profile"):
        ri.validate_registry_integrity()


def test_redundant_note_fails(monkeypatch):
    """冗余登记防腐化：给无失配的 code 登记 note → fail-fast。"""
    monkeypatch.setitem(ri.CAPABILITY_NOTES, "refund", "冗余登记测试")
    with pytest.raises(ri.RegistryIntegrityError, match="冗余登记"):
        ri.validate_registry_integrity()
