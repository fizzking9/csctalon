"""夜间投诉风险预警配置服务 - 读取/写入 + 60s 缓存 (#276, O-5 / S-P2-5)

职责:
  1. get_settings(db): 读配置行(缺失自动补默认行, 兼容旧库/手工删行)
  2. get_cached_config(db): 带 60s 缓存的配置读取 → dict(供评分引擎每次调用,
     改库最多 60s 生效, 免重启)
  3. update_settings(db, ...): 应用层校验 + 写入(非法 → 400; 直接改库场景由
     模型 CheckConstraint 兜底)
  4. reset_cache_for_tests(): 测试辅助清缓存

关键语义:
  - 配置项语义与默认值全部定义在 app.models.risk_alert_settings(单一事实来源);
    本服务只做 IO + 缓存, 不重复定义默认值。
  - 缓存用 time.monotonic(与 scheduler_settings_service 同模式), TTL 60s。
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.risk_alert_settings import (
    MAX_DAYS,
    MAX_LEVEL,
    MAX_WEIGHT,
    MIN_DAYS,
    MIN_LEVEL,
    MIN_WEIGHT,
    SINGLETON_ID,
    RiskAlertSettings,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# 读写
# ─────────────────────────────────────────────────────────────────────
def get_settings(db: Session) -> RiskAlertSettings:
    """读配置行; 缺失则创建默认行(兼容旧库/手工删行场景)。"""
    row = db.scalar(select(RiskAlertSettings).where(RiskAlertSettings.id == SINGLETON_ID))
    if row is None:
        row = RiskAlertSettings(id=SINGLETON_ID, updated_by="system")
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("[risk_alert_settings] 初始化默认配置行(id=1, #250 v1 拍板值)")
    return row


def to_config_dict(row: RiskAlertSettings) -> dict:
    """序列化为评分引擎直接可用的配置 dict。

    键名与 complaint_risk_service.compute_complaint_risk_priority_list 的
    参数名对齐, 调用方可直接解包传入。
    """
    return {
        "weights": {
            "recharge": row.weight_recharge,
            "low_flow": row.weight_low_flow,
            "contact": row.weight_contact,
            "repeat": row.weight_repeat,
        },
        "recharge_days": row.recharge_days,
        "contact_days": row.contact_days,
        "complaint_days": row.complaint_days,
        "external_days": row.external_days,
        "low_flow_gb": float(row.low_flow_gb),
        "recharge_high_amount": Decimal(row.recharge_high_amount),
        "level_cutoffs": (row.level_critical, row.level_high, row.level_medium),
    }


def update_settings(db: Session, *, actor: str | None = None, **fields) -> dict:
    """校验并写入配置(非法 → 400, 不静默取默认)。fields 为列名→新值。

    校验分两层: 应用层逐项范围校验(给出可读错误); 等级线递减等跨字段约束
    依赖 DB CheckConstraint 兜底(与 scheduler_settings 同模式)。
    """
    int_ranges = {
        "weight_recharge": (MIN_WEIGHT, MAX_WEIGHT),
        "weight_low_flow": (MIN_WEIGHT, MAX_WEIGHT),
        "weight_contact": (MIN_WEIGHT, MAX_WEIGHT),
        "weight_repeat": (MIN_WEIGHT, MAX_WEIGHT),
        "recharge_days": (MIN_DAYS, MAX_DAYS),
        "contact_days": (MIN_DAYS, MAX_DAYS),
        "complaint_days": (MIN_DAYS, MAX_DAYS),
        "external_days": (MIN_DAYS, MAX_DAYS),
        "level_critical": (MIN_LEVEL, MAX_LEVEL),
        "level_high": (MIN_LEVEL, MAX_LEVEL),
        "level_medium": (MIN_LEVEL, MAX_LEVEL),
    }
    row = get_settings(db)
    for key, value in fields.items():
        if key in int_ranges:
            lo, hi = int_ranges[key]
            if not (lo <= int(value) <= hi):
                raise HTTPException(400, f"{key} 需在 {lo}~{hi} 之间, 收到 {value}")
        elif key == "low_flow_gb":
            if float(value) <= 0:
                raise HTTPException(400, f"low_flow_gb 需 > 0, 收到 {value}")
        elif key == "recharge_high_amount":
            if Decimal(str(value)) < 0:
                raise HTTPException(400, f"recharge_high_amount 需 ≥ 0, 收到 {value}")
        elif not hasattr(row, key):
            raise HTTPException(400, f"未知配置项: {key}")
        setattr(row, key, value)

    # 等级线跨字段约束(应用层提前报可读错误, 不等 DB 约束拒绝)
    if not (row.level_critical > row.level_high > row.level_medium):
        raise HTTPException(
            400,
            f"等级线需满足 critical({row.level_critical}) > high({row.level_high})"
            f" > medium({row.level_medium})",
        )

    row.updated_by = (actor or "")[:64] or None
    db.commit()
    db.refresh(row)
    logger.info("[risk_alert_settings] 配置更新(by=%s): %s", actor or "-", fields)
    _invalidate_cache()
    return to_config_dict(row)


# ─────────────────────────────────────────────────────────────────────
# 缓存(供评分引擎每次调用读取, 避免每请求查库; 改库最多 60s 生效)
# ─────────────────────────────────────────────────────────────────────
_CACHE_TTL_S = 60.0
_cache: dict = {"at": 0.0, "value": None}


def _invalidate_cache() -> None:
    _cache["at"] = 0.0
    _cache["value"] = None


def get_cached_config(db: Session) -> dict:
    """带 60s 缓存的配置读取(返回 to_config_dict 结果)。"""
    now = time.monotonic()
    if _cache["value"] is not None and (now - _cache["at"]) < _CACHE_TTL_S:
        return _cache["value"]

    value = to_config_dict(get_settings(db))
    _cache["at"] = now
    _cache["value"] = value
    return value


def reset_cache_for_tests() -> None:
    """测试辅助: 清缓存(避免用例间串扰)。"""
    _invalidate_cache()
