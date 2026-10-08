# -*- coding: utf-8 -*-
"""O-5 (#276) risk_alert_settings 配置表化: 配置读取 + 默认值兜底 + 缓存语义"""

import uuid as _uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.cs_record import CsRecord
from app.models.device import Device
from app.models.risk_alert_settings import (
    DEFAULT_COMPLAINT_DAYS,
    DEFAULT_CONTACT_DAYS,
    DEFAULT_EXTERNAL_DAYS,
    DEFAULT_LEVEL_CRITICAL,
    DEFAULT_LEVEL_HIGH,
    DEFAULT_LEVEL_MEDIUM,
    DEFAULT_LOW_FLOW_GB,
    DEFAULT_RECHARGE_DAYS,
    DEFAULT_RECHARGE_HIGH_AMOUNT,
    DEFAULT_WEIGHT_CONTACT,
    DEFAULT_WEIGHT_LOW_FLOW,
    DEFAULT_WEIGHT_RECHARGE,
    DEFAULT_WEIGHT_REPEAT,
    RiskAlertSettings,
)
from app.services import risk_alert_settings_service as svc
from app.services.complaint_risk_service import compute_complaint_risk_priority_list

_DEFAULTS = {
    "weight_recharge": DEFAULT_WEIGHT_RECHARGE,
    "weight_low_flow": DEFAULT_WEIGHT_LOW_FLOW,
    "weight_contact": DEFAULT_WEIGHT_CONTACT,
    "weight_repeat": DEFAULT_WEIGHT_REPEAT,
    "recharge_days": DEFAULT_RECHARGE_DAYS,
    "contact_days": DEFAULT_CONTACT_DAYS,
    "complaint_days": DEFAULT_COMPLAINT_DAYS,
    "external_days": DEFAULT_EXTERNAL_DAYS,
    "low_flow_gb": DEFAULT_LOW_FLOW_GB,
    "recharge_high_amount": DEFAULT_RECHARGE_HIGH_AMOUNT,
    "level_critical": DEFAULT_LEVEL_CRITICAL,
    "level_high": DEFAULT_LEVEL_HIGH,
    "level_medium": DEFAULT_LEVEL_MEDIUM,
}


@pytest.fixture(autouse=True)
def _clean_cache_and_settings(db):
    """每个用例前后清 60s 缓存; 收尾把配置行还原为默认值。

    必须还原: update_settings/get_settings 会 db.commit()(与 scheduler_settings
    同模式), 配置改动会持久化到测试库, 不还原将污染同库后续用例
    (如 test_complaint_risk 依赖默认权重的断言)。
    """
    svc.reset_cache_for_tests()
    yield
    row = svc.get_settings(db)
    for field, value in _DEFAULTS.items():
        setattr(row, field, value)
    row.updated_by = None
    db.commit()
    svc.reset_cache_for_tests()


def _uniq(prefix: str) -> str:
    return f"{prefix}-{_uuid.uuid4().hex[:10]}"


def _mk_device(db, sn: str) -> Device:
    dev = db.get(Device, sn)
    if dev:
        return dev
    dev = Device(sn=sn)
    db.add(dev)
    db.flush()
    return dev


def _mk_cs(db, sn: str) -> CsRecord:
    now = datetime.now(UTC)
    r = CsRecord(
        device_sn=sn, operation_time=now, operator="企微-测试",
        problem_category="网络问题", problem_type=_uniq("PT"),
        consult_time=now, problem_create_time=now,
    )
    db.add(r)
    db.flush()
    return r


# ── 默认值兜底 ──

def test_get_settings_creates_default_row(db):
    """空表 → 自动补默认行, 值 = #250 v1 拍板值(保证升级前后评分不变)。"""
    row = svc.get_settings(db)
    assert row.id == 1
    assert row.weight_contact == DEFAULT_WEIGHT_CONTACT
    assert row.low_flow_gb == DEFAULT_LOW_FLOW_GB
    assert row.recharge_high_amount == DEFAULT_RECHARGE_HIGH_AMOUNT
    assert (row.level_critical, row.level_high, row.level_medium) == (
        DEFAULT_LEVEL_CRITICAL, DEFAULT_LEVEL_HIGH, DEFAULT_LEVEL_MEDIUM,
    )


def test_to_config_dict_shape(db):
    """to_config_dict 键名与 compute_* 参数对齐, cutoffs 为三元组。"""
    cfg = svc.to_config_dict(svc.get_settings(db))
    assert cfg["weights"] == {"recharge": 25, "low_flow": 25, "contact": 20, "repeat": 30}
    assert cfg["recharge_days"] == 90
    assert cfg["contact_days"] == 30
    assert cfg["complaint_days"] == 90
    assert cfg["external_days"] == 180
    assert cfg["low_flow_gb"] == 5.0
    assert cfg["recharge_high_amount"] == Decimal("500")
    assert cfg["level_cutoffs"] == (70, 45, 25)


# ── 缓存语义: 改库即生效(60s 内) ──

def test_cache_serves_stale_within_ttl_and_refresh_after_invalidate(db):
    """缓存 TTL 内读旧值(不查库); 失效后读到新值——验证'改库经缓存失效生效'。"""
    cfg1 = svc.get_cached_config(db)
    # 模拟 DBA 直接改库(绕过服务层)
    row = svc.get_settings(db)
    row.weight_contact = 99
    db.flush()
    cfg2 = svc.get_cached_config(db)
    assert cfg2["weights"]["contact"] == cfg1["weights"]["contact"]  # TTL 内仍是旧值
    svc.reset_cache_for_tests()  # 等效 TTL 到期
    cfg3 = svc.get_cached_config(db)
    assert cfg3["weights"]["contact"] == 99


# ── 写入校验 ──

def test_update_settings_validates_range(db):
    with pytest.raises(HTTPException) as ei:
        svc.update_settings(db, weight_contact=101)
    assert ei.value.status_code == 400


def test_update_settings_validates_level_order(db):
    with pytest.raises(HTTPException) as ei:
        svc.update_settings(db, level_medium=50)  # 50 > level_high=45, 破坏递减
    assert ei.value.status_code == 400
    assert "等级线" in str(ei.value.detail)


def test_update_settings_rejects_unknown_field(db):
    with pytest.raises(HTTPException) as ei:
        svc.update_settings(db, no_such_field=1)
    assert ei.value.status_code == 400


def test_update_settings_persists_and_invalidates_cache(db):
    svc.get_cached_config(db)  # 先填充缓存
    out = svc.update_settings(db, weight_contact=77, actor="tester")
    assert out["weights"]["contact"] == 77
    # 缓存已失效: 再次读取立即拿到新值(无需 reset)
    assert svc.get_cached_config(db)["weights"]["contact"] == 77
    assert db.get(RiskAlertSettings, 1).updated_by == "tester"


# ── 端到端: 评分引擎读配置 ──

def test_compute_uses_config_weights_and_cutoffs(db):
    """改配置后评分结果随之变化(权重 + 等级线均走配置表)。"""
    sn = _uniq("SN")
    _mk_device(db, sn)
    _mk_cs(db, sn)  # 1 次接待 → contact 维 50 分, 其余维 0/中性
    # flow 维无记录=30 分(中性), 会按 weight_low_flow 计入, 两维一并断言

    svc.update_settings(db, weight_contact=20, weight_low_flow=25,
                        level_critical=70, level_high=45, level_medium=25)
    r1 = compute_complaint_risk_priority_list(db, limit=500)
    item1 = next(i for i in r1["items"] if i["device_sn"] == sn)
    # total = (50*20 + 30*25)/100 = 17.5 → low
    assert item1["total"] == 17.5
    assert item1["level"] == "low"

    # 调高 contact 权重 + 压低等级线 → 总分与等级都变
    svc.update_settings(db, weight_contact=100, level_medium=10)
    r2 = compute_complaint_risk_priority_list(db, limit=500)
    item2 = next(i for i in r2["items"] if i["device_sn"] == sn)
    # total = (50*100 + 30*25)/100 = 57.5
    assert item2["total"] == 57.5
    assert item2["level"] == "high"


def test_compute_explicit_params_override_config(db):
    """显式传参优先于配置表(测试/查询参数覆盖路径不回退)。"""
    sn = _uniq("SN")
    _mk_device(db, sn)
    _mk_cs(db, sn)
    svc.update_settings(db, weight_contact=100)
    r = compute_complaint_risk_priority_list(
        db, limit=500, weights={"recharge": 25, "low_flow": 25, "contact": 20, "repeat": 30},
    )
    item = next(i for i in r["items"] if i["device_sn"] == sn)
    assert item["total"] == 17.5  # 走显式权重, 非配置的 100
