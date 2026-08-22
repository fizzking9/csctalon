"""共享文档(腾讯文档)投诉数据同步服务

流程(对应需求: 设置链接 → 点击授权 → 持续监测文件变化 → 录入系统):
  1. ensure_doc_sync_configs  启动时幂等注册各平台配置行
  2. 授权: build_authorize_url → 用户点击授权 → callback 换 token 存库(Fernet 加密)
  3. 同步: sync_tencent_doc 读取共享表格两个 Sheet(315/卡商),
     转成 complaint_sync 期望的 payload, 复用其清洗/指纹去重/SN关联工单管线
  4. 轮询: 调度器定期调用 should_sync_tencent_doc() 判断是否到点,
     到点则执行 sync_tencent_doc_safe()
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import SessionLocal
from app.models.doc_sync_config import DocSyncConfig
from app.services import tencent_doc_client as tdc
from app.services.complaint_sync import sync_complaints_from_payload

logger = logging.getLogger(__name__)

PLATFORM_TENCENT = "tencent"
PLATFORM_WPS = "wps"  # 预留(金山文档)

# 各平台默认配置
_DEFAULT_CONFIGS = {
    PLATFORM_TENCENT: {"name": "腾讯文档"},
    PLATFORM_WPS: {"name": "金山文档"},
}


# ── 配置存取 ─────────────────────────────────────────────────────────
def ensure_doc_sync_configs(db: Session) -> int:
    """幂等注册 doc_sync_configs 行(缺失则创建, 已存在保留用户配置)"""
    created = 0
    for platform, cfg in _DEFAULT_CONFIGS.items():
        row = db.scalar(select(DocSyncConfig).where(DocSyncConfig.platform == platform))
        if row is None:
            db.add(DocSyncConfig(platform=platform, name=cfg["name"]))
            created += 1
    if created:
        db.commit()
    return created


def _get_row(db: Session, platform: str = PLATFORM_TENCENT) -> DocSyncConfig:
    row = db.scalar(select(DocSyncConfig).where(DocSyncConfig.platform == platform))
    if row is None:
        row = DocSyncConfig(
            platform=platform,
            name=_DEFAULT_CONFIGS.get(platform, {}).get("name", platform),
        )
        db.add(row)
        db.flush()
    return row


def get_doc_config(db: Session, platform: str = PLATFORM_TENCENT) -> dict:
    """读取运行配置(敏感字段解密)"""
    row = _get_row(db, platform)
    return {
        "platform": row.platform,
        "name": row.name,
        "app_id": decrypt_secret(row.app_id),
        "app_secret": decrypt_secret(row.app_secret),
        "redirect_uri": row.redirect_uri,
        "access_token": decrypt_secret(row.access_token),
        "refresh_token": decrypt_secret(row.refresh_token),
        "open_id": row.open_id,
        "token_expires_at": row.token_expires_at,
        "doc_url": row.doc_url,
        "file_id": row.file_id,
        "external_sheet_name": row.external_sheet_name,
        "card_sheet_name": row.card_sheet_name,
        "enabled": row.enabled,
        "sync_interval_minutes": row.sync_interval_minutes,
        "last_sync_at": row.last_sync_at,
        "last_sync_status": row.last_sync_status,
        "last_sync_summary": row.last_sync_summary,
        "last_error": row.last_error,
    }


def save_app_credentials(
    db: Session,
    app_id: str,
    app_secret: str,
    redirect_uri: str,
    platform: str = PLATFORM_TENCENT,
) -> None:
    """保存开放平台应用凭证(ClientID/Secret, Fernet 加密)"""
    row = _get_row(db, platform)
    if app_id:
        row.app_id = encrypt_secret(app_id.strip())
    if app_secret:
        row.app_secret = encrypt_secret(app_secret.strip())
    if redirect_uri:
        row.redirect_uri = redirect_uri.strip()
    db.commit()


def save_oauth_tokens(
    db: Session,
    access_token: str,
    refresh_token: str | None,
    expires_in: int,
    open_id: str,
    platform: str = PLATFORM_TENCENT,
) -> None:
    """保存用户 OAuth 授权 token(Fernet 加密)"""
    row = _get_row(db, platform)
    row.access_token = encrypt_secret(access_token)
    if refresh_token:
        row.refresh_token = encrypt_secret(refresh_token)
    row.open_id = open_id
    row.token_expires_at = tdc.token_expires_at(expires_in)
    row.last_error = None
    db.commit()


def save_doc_config(
    db: Session,
    doc_url: str | None,
    external_sheet_name: str | None,
    card_sheet_name: str | None,
    enabled: bool | None,
    sync_interval_minutes: int | None,
    platform: str = PLATFORM_TENCENT,
) -> None:
    """保存共享文档配置(链接/Sheet名/开关/轮询间隔)"""
    row = _get_row(db, platform)
    if doc_url is not None:
        row.doc_url = doc_url.strip() or None
        row.file_id = None  # 链接变了, 清空缓存的 fileID
    if external_sheet_name is not None:
        row.external_sheet_name = external_sheet_name.strip() or None
    if card_sheet_name is not None:
        row.card_sheet_name = card_sheet_name.strip() or None
    if enabled is not None:
        row.enabled = bool(enabled)
    if sync_interval_minutes is not None:
        row.sync_interval_minutes = max(int(sync_interval_minutes), 5)
    db.commit()


def clear_oauth_tokens(db: Session, platform: str = PLATFORM_TENCENT) -> None:
    """解除授权(清空 token)"""
    row = _get_row(db, platform)
    row.access_token = None
    row.refresh_token = None
    row.open_id = None
    row.token_expires_at = None
    db.commit()


def get_doc_status(db: Session, platform: str = PLATFORM_TENCENT) -> dict:
    """平台接入状态(前端展示用, 不泄露明文 token)"""
    cfg = get_doc_config(db, platform)
    return {
        "platform": cfg["platform"],
        "name": cfg["name"],
        "app_configured": bool(cfg["app_id"] and cfg["app_secret"]),
        "app_id": cfg["app_id"],  # 非敏感, 前端回填显示
        "redirect_uri": cfg["redirect_uri"],
        "authorized": bool(cfg["access_token"] and cfg["open_id"]),
        "open_id": cfg["open_id"],
        "token_expired": bool(
            cfg["access_token"]
            and cfg["token_expires_at"]
            and cfg["token_expires_at"] <= datetime.now(timezone.utc)
        ),
        "doc_configured": bool(cfg["doc_url"]),
        "doc_url": cfg["doc_url"],
        "file_id": cfg["file_id"],
        "external_sheet_name": cfg["external_sheet_name"],
        "card_sheet_name": cfg["card_sheet_name"],
        "enabled": cfg["enabled"],
        "sync_interval_minutes": cfg["sync_interval_minutes"],
        "last_sync_at": cfg["last_sync_at"],
        "last_sync_status": cfg["last_sync_status"],
        "last_sync_summary": cfg["last_sync_summary"],
        "last_error": cfg["last_error"],
    }


# ── token 生命周期 ───────────────────────────────────────────────────
def _ensure_valid_token(db: Session, cfg: dict) -> dict:
    """access_token 过期/接近过期 → 用 refresh_token 刷新并落库; 返回最新 cfg"""
    if not cfg["access_token"]:
        raise tdc.DocApiError("尚未授权腾讯文档, 请先完成授权")
    if cfg["token_expires_at"] and cfg["token_expires_at"] <= datetime.now(timezone.utc):
        if not cfg["refresh_token"]:
            raise tdc.DocApiError("访问令牌已过期且无刷新令牌, 请重新授权")
        data = tdc.refresh_access_token(cfg["app_id"], cfg["app_secret"], cfg["refresh_token"])
        cfg["access_token"] = data["access_token"]
        cfg["token_expires_at"] = tdc.token_expires_at(data.get("expires_in"))
        row = _get_row(db)
        row.access_token = encrypt_secret(cfg["access_token"])
        row.token_expires_at = cfg["token_expires_at"]
        db.commit()
        logger.info("[doc_sync] access_token 已自动刷新")
    return cfg


# ── 同步主流程 ───────────────────────────────────────────────────────
def _pick_sheets(sheets: list[tdc.TencentSheetInfo], wanted: str | None):
    """按 Sheet 名精确查找; 未配置/未命中时回退到第一张有数据宽度的表"""
    if wanted:
        for s in sheets:
            if s.title.strip() == wanted.strip():
                return s
    return sheets[0] if sheets else None


def sync_tencent_doc(db: Session, trigger: str = "manual", platform: str = PLATFORM_TENCENT) -> dict:
    """同步腾讯文档投诉数据(315/卡商)到本地

    流程: 校验配置 → 确保 token → 解析链接(fileID) → 读两个 Sheet →
          构建 payload → 复用 complaint_sync 清洗/去重/关联入库 → 更新状态。
    失败时在配置行记录 last_sync_status=failed + last_error, 然后向上抛出。
    返回统计 dict。
    """
    cfg = get_doc_config(db, platform)
    if not cfg["app_id"] or not cfg["app_secret"]:
        raise tdc.DocApiError("尚未配置开放平台应用凭证(ClientID/Secret)")
    if not cfg["doc_url"]:
        raise tdc.DocApiError("尚未配置共享文档链接")
    cfg = _ensure_valid_token(db, cfg)

    access_token, open_id, client_id = cfg["access_token"], cfg["open_id"], cfg["app_id"]

    try:
        # 解析 fileID(缓存避免重复转换)
        file_id = cfg["file_id"]
        if not file_id:
            encoded = tdc.parse_share_url(cfg["doc_url"])
            if not encoded:
                raise tdc.DocApiError(f"无法从链接解析文档ID: {cfg['doc_url']}")
            file_id = tdc.convert_encoded_id(client_id, open_id, access_token, encoded)
            row = _get_row(db, platform)
            row.file_id = file_id
            db.commit()
            logger.info(f"[doc_sync] 已转换 fileID: {encoded} → {file_id}")

        # 子表列表
        sheets = tdc.get_sheets_info(client_id, open_id, access_token, file_id)
        ext_sheet = _pick_sheets(sheets, cfg["external_sheet_name"])
        card_sheet = _pick_sheets(sheets, cfg["card_sheet_name"])
        if not ext_sheet and not card_sheet:
            raise tdc.DocApiError("共享文档中没有可读取的子表")

        # 读取数据(每张表: 二维数组 → 表头映射 records)
        def _read(sheet: tdc.TencentSheetInfo | None) -> list[dict]:
            if sheet is None:
                return []
            rows = tdc.read_sheet_rows(client_id, open_id, access_token, file_id, sheet)
            return tdc.rows_to_records(rows)

        ext_records = _read(ext_sheet)
        card_records = _read(card_sheet)

        # 元信息(文件名, 用于 source_file 展示)
        meta = tdc.fetch_doc_meta(client_id, open_id, access_token, file_id)
        doc_name = (meta.get("title") or meta.get("name") or cfg["doc_url"].split("/")[-1] or "腾讯文档表格")[:200]

        payload: dict[str, Any] = {
            "source_file": f"{doc_name}",
            "external_complaints": ext_records,
            "card_complaints": card_records,
        }
        stats = sync_complaints_from_payload(db, payload, source_file=doc_name, sync_source="tencent_doc")

        # 更新平台同步状态
        row = _get_row(db, platform)
        row.last_sync_at = datetime.now(timezone.utc)
        row.last_sync_status = stats["status"]
        row.last_sync_summary = {
            "external": {k: stats["external"][k] for k in ("total", "inserted", "updated", "failed", "matched")},
            "card": {k: stats["card"][k] for k in ("total", "inserted", "updated", "failed", "matched")},
        }
        row.last_error = None
        db.commit()
        logger.info(
            f"[doc_sync] {trigger} 同步完成: 315={stats['external']['total']}(+{stats['external']['inserted']}/"
            f"~{stats['external']['updated']}) 卡商={stats['card']['total']}(+{stats['card']['inserted']}/"
            f"~{stats['card']['updated']}) status={stats['status']}"
        )
        return stats
    except Exception as e:
        try:
            row = _get_row(db, platform)
            row.last_sync_status = "failed"
            row.last_error = str(e)[:2000]
            row.last_sync_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        raise


def sync_tencent_doc_safe(trigger: str = "scheduled", platform: str = PLATFORM_TENCENT) -> dict | None:
    """调度器安全入口: 独立 session, 失败记录到配置行而不是抛给调用方"""
    db = SessionLocal()
    try:
        return sync_tencent_doc(db, trigger=trigger, platform=platform)
    except Exception as e:
        logger.exception(f"[doc_sync] {trigger} 同步失败: {e}")
        try:
            row = _get_row(db, platform)
            row.last_sync_status = "failed"
            row.last_error = str(e)[:2000]
            row.last_sync_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return None
    finally:
        db.close()


def should_sync_tencent_doc(db: Session | None = None, platform: str = PLATFORM_TENCENT) -> bool:
    """判断是否到点需要同步(调度器轮询用)

    条件: enabled + app凭证 + 已授权 + doc_url 已配 + 距上次同步 >= interval
    出错视为不需要同步(避免频繁刷错误日志)。
    """
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        row = db.scalar(select(DocSyncConfig).where(DocSyncConfig.platform == platform))
        if not row or not row.enabled:
            return False
        if not (row.app_id and row.app_secret and row.access_token and row.doc_url):
            return False
        # 采集方式总开关: monitor 关闭则不轮询
        from app.services.ingest_mode_service import is_ingest_mode_enabled

        if not is_ingest_mode_enabled(db, "monitor"):
            return False
        if row.last_sync_at is None:
            return True  # 首次
        due_at = row.last_sync_at + timedelta(minutes=max(row.sync_interval_minutes, 5))
        return datetime.now(timezone.utc) >= due_at
    except Exception:  # noqa: BLE001
        return False
    finally:
        if close:
            db.close()
