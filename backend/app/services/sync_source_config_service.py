"""BMS 同步源配置服务

职责:
  1. ensure_sync_sources_registered: 启动时幂等 seed sync_source_configs(默认值来自 BMS_DATA_SOURCES,
     首次把 .env 的 BMS_USERNAME/BMS_PASSWORD 加密灌入各源作为初始值)
  2. get_sync_source_config: 运行时读取单个源配置(解密凭证),供 bms_sync/fetcher 使用
  3. list_sync_source_configs: API 用,凭证掩码返回
  4. update_sync_source_config: API 用,更新配置(凭证加密入库)

设计:
  - 已存在的源不覆盖(用户可能在 UI 改过 URL/账号/开关);仅新建缺失的源
  - 凭证用 Fernet 加密(见 app/core/crypto.py)
  - DB 行缺失时回退到 BMS_DATA_SOURCES 默认 + settings.BMS_USERNAME/PASSWORD(向后兼容)
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.sync_source_config import SyncSourceConfig
from app.services.bms_fetcher import BMS_DATA_SOURCES
from app.services.scrm_client import SCRM_DATA_SOURCES

logger = logging.getLogger(__name__)


def _all_data_sources() -> dict[str, dict]:
    """合并 BMS + SCRM 所有数据源(BMS 原始顺序在前,SCRM 追加在后,统一注册)"""
    merged: dict[str, dict] = {}
    merged.update(BMS_DATA_SOURCES)
    merged.update(SCRM_DATA_SOURCES)
    return merged


def ensure_sync_sources_registered(db: Session) -> dict:
    """幂等注册同步源配置:缺失的插入,已存在的保留(不覆盖用户编辑)"""
    created = 0
    # .env 凭证作为初始值灌入(仅对新建的源;用户之后可在 UI 改成各子域真实账号)
    env_user = getattr(settings, "BMS_USERNAME", "") or ""
    env_pwd = getattr(settings, "BMS_PASSWORD", "") or ""
    env_scrm_client = getattr(settings, "SCRM_CLIENT_ID", "") or ""
    env_scrm_secret = getattr(settings, "SCRM_CLIENT_SECRET", "") or ""
    env_scrm_base = getattr(settings, "SCRM_BASE_URL", "") or ""

    all_sources = _all_data_sources()
    for idx, (data_type, cfg) in enumerate(all_sources.items()):
        existing = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
        if existing:
            continue
        is_scrm = data_type.startswith("scrm_")
        if is_scrm:
            # SCRM: username = Fernet(clientId), password = Fernet(clientSecret)
            init_user = encrypt_secret(env_scrm_client) if env_scrm_client else None
            init_pwd = encrypt_secret(env_scrm_secret) if env_scrm_secret else None
            init_base = cfg.get("base_url") or env_scrm_base
        else:
            init_user = encrypt_secret(env_user) if env_user else None
            init_pwd = encrypt_secret(env_pwd) if env_pwd else None
            init_base = cfg["base_url"]
        db.add(
            SyncSourceConfig(
                data_type=data_type,
                name=cfg["name"],
                base_url=init_base,
                page_path=cfg.get("page_path", ""),
                time_labels=cfg.get("time_labels"),
                username=init_user,
                password=init_pwd,
                default_sync=cfg.get("default_sync", True),
                enabled=True,
                sort_order=idx,
            )
        )
        created += 1
    db.commit()
    total = len(all_sources)
    logger.info(f"同步源配置注册完成: 新建 {created} 个, 共 {total} 个(BMS+SCRM)")
    return {"created": created, "total": total}


def _row_to_config(row: SyncSourceConfig) -> dict:
    """DB 行 → 运行配置 dict(解密凭证)"""
    # fetch_mode / requires_captcha 不在 DB 表里,从种子补:BMS 走 playwright, SCRM 走 openapi
    seed = BMS_DATA_SOURCES.get(row.data_type) or SCRM_DATA_SOURCES.get(row.data_type, {})
    return {
        "data_type": row.data_type,
        "name": row.name,
        "base_url": row.base_url,
        "page_path": row.page_path,
        "time_labels": row.time_labels,
        "username": decrypt_secret(row.username),
        "password": decrypt_secret(row.password),
        "default_sync": row.default_sync,
        "enabled": row.enabled,
        "fetch_mode": seed.get("fetch_mode", "playwright"),
        "requires_captcha": seed.get("requires_captcha", False),
    }


def get_sync_source_config(db: Session, data_type: str) -> dict | None:
    """读取单个源运行配置(解密凭证);DB 缺失时回退到 种子 + .env 凭证"""
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
    if row:
        return _row_to_config(row)
    # 回退(理论上 seed 后不会走到)
    cfg = BMS_DATA_SOURCES.get(data_type) or SCRM_DATA_SOURCES.get(data_type)
    if not cfg:
        return None
    is_scrm = data_type.startswith("scrm_")
    if is_scrm:
        env_user = getattr(settings, "SCRM_CLIENT_ID", "") or ""
        env_pwd = getattr(settings, "SCRM_CLIENT_SECRET", "") or ""
    else:
        env_user = getattr(settings, "BMS_USERNAME", "") or ""
        env_pwd = getattr(settings, "BMS_PASSWORD", "") or ""
    return {
        "data_type": data_type,
        "name": cfg["name"],
        "base_url": cfg.get("base_url", getattr(settings, "SCRM_BASE_URL", "") if is_scrm else cfg["base_url"]),
        "page_path": cfg.get("page_path", ""),
        "time_labels": cfg.get("time_labels"),
        "username": env_user or None,
        "password": env_pwd or None,
        "default_sync": cfg.get("default_sync", True),
        "enabled": True,
        "fetch_mode": cfg.get("fetch_mode", "openapi" if is_scrm else "playwright"),
        "requires_captcha": cfg.get("requires_captcha", False),
    }


def list_sync_source_configs(db: Session) -> list[dict]:
    """列出所有同步源配置(API 用,凭证掩码)"""
    rows = db.scalars(select(SyncSourceConfig).order_by(SyncSourceConfig.sort_order, SyncSourceConfig.id)).all()
    result = []
    for row in rows:
        seed = BMS_DATA_SOURCES.get(row.data_type) or SCRM_DATA_SOURCES.get(row.data_type, {})
        result.append(
            {
                "data_type": row.data_type,
                "name": row.name,
                "base_url": row.base_url,
                "page_path": row.page_path,
                "time_labels": row.time_labels,
                "username": row.username,  # 加密原文,前端判断是否已配置
                "has_credentials": bool(row.username and row.password),
                "default_sync": row.default_sync,
                "enabled": row.enabled,
                "sort_order": row.sort_order,
                "fetch_mode": seed.get("fetch_mode", "playwright"),
                "requires_captcha": seed.get("requires_captcha", False),
            }
        )
    return result


def update_sync_source_config(db: Session, data_type: str, payload: dict) -> dict | None:
    """更新单个源配置;username/password 为空或不传时保留原值"""
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
    if not row:
        return None

    if "name" in payload and payload["name"]:
        row.name = payload["name"]
    if "base_url" in payload and payload["base_url"]:
        row.base_url = payload["base_url"]
    if "page_path" in payload and payload["page_path"]:
        row.page_path = payload["page_path"]
    if "time_labels" in payload:
        row.time_labels = payload["time_labels"] or None
    if "default_sync" in payload:
        row.default_sync = bool(payload["default_sync"])
    if "enabled" in payload:
        row.enabled = bool(payload["enabled"])
    # 凭证:仅当显式传非空字符串时更新(前端清空表示不改)
    if payload.get("username"):
        row.username = encrypt_secret(payload["username"])
    if payload.get("password"):
        row.password = encrypt_secret(payload["password"])
    db.commit()
    db.refresh(row)
    return _row_to_config(row)


# ---------- 兼容别名 (旧代码引用) ----------
def get_all_configs(db: Session) -> list[dict]:
    """兼容旧导入名: get_all_configs → list_sync_source_configs"""
    return list_sync_source_configs(db)
