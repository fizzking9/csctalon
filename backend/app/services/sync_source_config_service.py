"""BMS / SCRM 同步源配置服务

职责:
  1. ensure_sync_sources_registered: 启动时幂等 seed sync_source_configs(默认值来自 种子配置,
     首次把 .env 凭证加密灌入各源作为初始值 + 写入默认 options)
  2. get_sync_source_config: 运行时读取单个源配置(解密凭证 + 合并 DB options / 种子 options),
     供 bms_sync / scrm_sync_service / fetcher 使用
  3. list_sync_source_configs: API 用,凭证掩码返回,带 options/fetch_mode/captcha_type 给前端展示
  4. update_sync_source_config: API 用,更新配置(凭证加密入库 + 支持保存 options/fetch_mode 等)

设计:
  - 已存在的源不覆盖(用户可能在 UI 改过 URL/账号/开关/fetch_mode);仅新建缺失的源
  - 新增 options(JSONB) 存扩展参数;种子 cfg.options 为默认值,DB 行 options 覆盖默认
  - 运行时 _resolve_effective_options 合并:DB_options + 种子_defaults(缺失 key 补种子值)
  - fetch_mode 优先级:用户在 UI 保存(DB) > 种子默认
  - 凭证用 Fernet 加密(见 app/core/crypto.py)
"""

import copy
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


# ── 运行时 helpers ──────────────────────────────────────────────────
def _is_scrm_type(data_type: str) -> bool:
    return data_type.startswith("scrm_")


def _seed_of(data_type: str) -> dict:
    return BMS_DATA_SOURCES.get(data_type) or SCRM_DATA_SOURCES.get(data_type, {})


def _resolve_effective_options(db_options: dict | None, seed_cfg: dict) -> dict:
    """合并 options: DB 行 options 覆盖种子 options;缺失 key 补种子默认值

    返回的 dict 至少包含:fetch_mode, captcha_type, 以及各种选择器
    """
    seed_opts = copy.deepcopy(seed_cfg.get("options") or {})
    db_opts = copy.deepcopy(db_options or {})
    # 合并:种子先填,DB 覆盖
    effective = {**seed_opts, **db_opts}

    # ── 顶层 seed 的 fetch_mode / requires_captcha 也同步到 effective(兼容老种子写法) ──
    if "fetch_mode" not in effective:
        effective["fetch_mode"] = seed_cfg.get(
            "fetch_mode", "openapi" if _is_scrm_type(seed_cfg.get("data_type", "")) else "playwright"
        )
    if "captcha_type" not in effective:
        # 推导: BMS requires_captcha=True → "text"(闪猫); SCRM playwright 模式 → "math"; 否则 "none"
        if seed_cfg.get("requires_captcha"):
            effective["captcha_type"] = "text"
        elif _is_scrm_type(seed_cfg.get("data_type", "")) and effective["fetch_mode"] in ("playwright", "dual"):
            effective["captcha_type"] = "math"
        else:
            effective["captcha_type"] = "none"
    return effective


def _requires_captcha_from_options(effective_options: dict) -> bool:
    """根据 effective_options 推导是否需要验证码登录(给前端展示用)"""
    mode = effective_options.get("fetch_mode") or ""
    ctype = effective_options.get("captcha_type") or "none"
    # 只有 playwright/dual 模式 且 验证码类型不是 none 才需要
    return mode in ("playwright", "dual") and ctype != "none"


# ── Seed 注册 ────────────────────────────────────────────────────────
def ensure_sync_sources_registered(db: Session) -> dict:
    """幂等注册同步源配置:缺失的插入,已存在的保留(不覆盖用户编辑).

    **字段级补丁(2026-08-06)**: 针对 ticket 等少数「我们后续发现原默认种子值有误」的字段,
    即使行已存在也会强制把「已知错误旧值」回写成最新种子值(仅限 time_labels/base_url/page_path 这类
    跟上游 BMS/SCRM 标签/页面地址强绑定的字段),否则用户改完也不会生效. 对应上游标签/路由变更时
    也能通过重启服务自动修复. 用户在 UI 里自定义过的值(≠旧默认错误值)则不动,保留用户修改.
    """
    created = 0
    updated_options = 0  # 已存在的源如果 options 列缺失(旧行迁移),补种子 defaults
    patched_fields = 0   # 已存在的源因"旧错误值"被补丁升级的次数

    # ── 字段级补丁: 当 DB 里的值是已知错误旧值时,强制替换为最新种子值 ──
    #    key = (data_type, 字段名), value = 旧错误值列表(命中任一则强制升级到最新种子值)
    _FORCE_PATCH_OLD_VALUES: dict[tuple[str, str], tuple[object, ...]] = {
        # ticket 之前默认 time_labels = 按"完成时间"同步 → 严重漏当日未完成工单 + 老工单被批量覆盖计入今日
        # 详见 docs/数据时间字段使用规范.md 对 ticket.finish_time 不可靠的说明,以及本次修复记录.
        ("ticket", "time_labels"): (
            ["起始完成时间", "结束完成时间"],
            ["开始完成时间", "结束完成时间"],
            ["起始完成日期", "结束完成日期"],
            ["完成时间开始", "完成时间结束"],
        ),
    }

    # .env 凭证作为初始值灌入(仅对新建的源;用户之后可在 UI 改成各子域真实账号)
    env_user = getattr(settings, "BMS_USERNAME", "") or ""
    env_pwd = getattr(settings, "BMS_PASSWORD", "") or ""
    env_scrm_client = getattr(settings, "SCRM_CLIENT_ID", "") or ""
    env_scrm_secret = getattr(settings, "SCRM_CLIENT_SECRET", "") or ""
    env_scrm_base = getattr(settings, "SCRM_BASE_URL", "") or ""

    all_sources = _all_data_sources()
    for idx, (data_type, cfg) in enumerate(all_sources.items()):
        existing = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
        default_options = copy.deepcopy(cfg.get("options") or {})
        # 顶层 fetch_mode 也同步进 options(兼容单一写法)
        if "fetch_mode" not in default_options and cfg.get("fetch_mode"):
            default_options["fetch_mode"] = cfg["fetch_mode"]
        if existing is None:
            is_scrm = _is_scrm_type(data_type)
            if is_scrm:
                # SCRM: username = Fernet(clientId), password = Fernet(clientSecret)
                init_user = encrypt_secret(env_scrm_client) if env_scrm_client else None
                init_pwd = encrypt_secret(env_scrm_secret) if env_scrm_secret else None
                init_base = cfg.get("base_url") or env_scrm_base
            else:
                init_user = encrypt_secret(env_user) if env_user else None
                init_pwd = encrypt_secret(env_pwd) if env_pwd else None
                init_base = cfg.get("base_url", "")
            db.add(
                SyncSourceConfig(
                    data_type=data_type,
                    name=cfg["name"],
                    base_url=init_base,
                    page_path=cfg.get("page_path", ""),
                    time_labels=cfg.get("time_labels"),
                    username=init_user,
                    password=init_pwd,
                    options=default_options or None,  # 空 dict 存 None 省空间
                    default_sync=cfg.get("default_sync", True),
                    enabled=True,
                    sort_order=idx,
                )
            )
            created += 1
        else:
            # ── 字段级补丁:对 time_labels/base_url/page_path 这种"和上游 BMS 标签/路由强绑定"的字段,
            # 如果 DB 存的是我们之前默认种子里就写错的旧值,强制升级到最新种子值.
            for field_name in ("time_labels", "base_url", "page_path"):
                patch_key = (data_type, field_name)
                if patch_key not in _FORCE_PATCH_OLD_VALUES:
                    continue
                current_val = getattr(existing, field_name, None)
                desired_val = cfg.get(field_name)
                if desired_val is None:
                    continue  # 最新种子里没给值的话不强制
                if current_val in _FORCE_PATCH_OLD_VALUES[patch_key]:
                    try:
                        setattr(existing, field_name, desired_val)
                        patched_fields += 1
                        logger.info(
                            f"[sync_source_configs] {data_type}.{field_name} 已从旧错误值 "
                            f"{current_val!r} 强制补丁升级到 {desired_val!r}"
                        )
                    except Exception as e:  # pragma: no cover
                        logger.warning(
                            f"补丁更新 sync_source_configs.{data_type}.{field_name} 失败: {e}"
                        )
            # 兼容老行迁移:如果该行 DB 列 options 为 None(旧版本新增列后为 NULL),
            # 则把种子默认 options 灌入(不覆盖用户已有值,仅补默认)
            if not existing.options and default_options:
                # 注意:只在 existing.options 为 None/{} 时才补,保留用户后续可能的修改
                try:
                    existing.options = default_options
                    updated_options += 1
                except Exception:
                    # 某些旧版本 SQLAlchemy 对属性 setattr 有限制,忽略
                    pass
    if created or updated_options or patched_fields:
        db.commit()
    total = len(all_sources)
    logger.info(
        f"同步源配置注册完成: 新建 {created} 个, 补默认 options {updated_options} 个, "
        f"旧错误值强制补丁 {patched_fields} 个, 共 {total} 个(BMS+SCRM)"
    )
    return {"created": created, "updated_options": updated_options,
            "patched_fields": patched_fields, "total": total}


# ── 读取 (运行时 + API) ──────────────────────────────────────────────
def _row_to_config(row: SyncSourceConfig) -> dict:
    """DB 行 → 运行配置 dict(解密凭证 + 合并 options,并抛出顶层关键字段)"""
    seed = _seed_of(row.data_type)
    seed_with_dt = {**seed, "data_type": row.data_type}
    effective_options = _resolve_effective_options(row.options, seed_with_dt)
    fetch_mode = effective_options.get(
        "fetch_mode", "openapi" if _is_scrm_type(row.data_type) else "playwright"
    )
    captcha_type = effective_options.get("captcha_type", "none")
    requires_captcha = _requires_captcha_from_options(effective_options)
    return {
        "data_type": row.data_type,
        "name": row.name,
        "base_url": row.base_url,
        "page_path": row.page_path,
        "time_labels": row.time_labels,
        "username": decrypt_secret(row.username),
        "password": decrypt_secret(row.password),
        "options": effective_options,
        # ── 顶层关键字段(便于下游直接使用) ──
        "fetch_mode": fetch_mode,
        "captcha_type": captcha_type,
        "requires_captcha": requires_captcha,
        # ── 通用开关 ──
        "default_sync": row.default_sync,
        "enabled": row.enabled,
    }


def get_sync_source_config(db: Session, data_type: str) -> dict | None:
    """读取单个源运行配置(解密凭证);DB 缺失时回退到种子 + .env 凭证"""
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
    if row:
        return _row_to_config(row)
    # 回退(理论上 seed 后不会走到)
    cfg = _seed_of(data_type)
    if not cfg:
        return None
    is_scrm = _is_scrm_type(data_type)
    if is_scrm:
        env_user = getattr(settings, "SCRM_CLIENT_ID", "") or ""
        env_pwd = getattr(settings, "SCRM_CLIENT_SECRET", "") or ""
    else:
        env_user = getattr(settings, "BMS_USERNAME", "") or ""
        env_pwd = getattr(settings, "BMS_PASSWORD", "") or ""
    effective_options = _resolve_effective_options(None, {**cfg, "data_type": data_type})
    return {
        "data_type": data_type,
        "name": cfg["name"],
        "base_url": cfg.get("base_url", getattr(settings, "SCRM_BASE_URL", "") if is_scrm else cfg.get("base_url", "")),
        "page_path": cfg.get("page_path", ""),
        "time_labels": cfg.get("time_labels"),
        "username": env_user or None,
        "password": env_pwd or None,
        "options": effective_options,
        "fetch_mode": effective_options.get("fetch_mode", "openapi" if is_scrm else "playwright"),
        "captcha_type": effective_options.get("captcha_type", "none"),
        "requires_captcha": _requires_captcha_from_options(effective_options),
        "default_sync": cfg.get("default_sync", True),
        "enabled": True,
    }


def list_sync_source_configs(db: Session) -> list[dict]:
    """列出所有同步源配置(API 用,凭证掩码;带 fetch_mode/captcha_type/options 给前端表单)"""
    rows = db.scalars(select(SyncSourceConfig).order_by(SyncSourceConfig.sort_order, SyncSourceConfig.id)).all()
    result = []
    for row in rows:
        seed = _seed_of(row.data_type)
        effective_options = _resolve_effective_options(row.options, {**seed, "data_type": row.data_type})
        fetch_mode = effective_options.get(
            "fetch_mode", "openapi" if _is_scrm_type(row.data_type) else "playwright"
        )
        captcha_type = effective_options.get("captcha_type", "none")
        requires_captcha = _requires_captcha_from_options(effective_options)
        result.append(
            {
                "data_type": row.data_type,
                "name": row.name,
                "base_url": row.base_url,
                "page_path": row.page_path,
                "time_labels": row.time_labels,
                "username": row.username,  # 加密原文,前端判断是否已配置(空=未改过)
                "password": row.password,  # 同上
                "has_credentials": bool(row.username and row.password),
                "default_sync": row.default_sync,
                "enabled": row.enabled,
                "sort_order": row.sort_order,
                # ── 新增: 抓取模式 & 验证码类型 & options(给前端编辑表单) ──
                "options": effective_options,
                "fetch_mode": fetch_mode,
                "captcha_type": captcha_type,
                "requires_captcha": requires_captcha,
            }
        )
    return result


# ── 更新 (API) ──────────────────────────────────────────────────────
def _merge_options_update(db_options: dict | None, patch: dict | None) -> dict | None:
    """合并 options 更新:用户只改了 fetch_mode → 仅覆盖那个 key,其他保留 DB 原 options"""
    if not patch:
        return db_options
    base = copy.deepcopy(db_options or {})
    for k, v in patch.items():
        if v is None:
            base.pop(k, None)  # None = 删除该 key
        else:
            base[k] = v
    return base or None  # 空 dict → None 省空间


def update_sync_source_config(db: Session, data_type: str, payload: dict) -> dict | None:
    """更新单个源配置;

    支持字段:
      - 基础字段: name / base_url / page_path / time_labels / default_sync / enabled
      - 凭证字段: username / password (空字符串=不改,有值才加密覆盖)
      - 抓取模式: fetch_mode (直接写入 options.fetch_mode)
      - 验证码类型: captcha_type (写入 options.captcha_type)
      - 自定义 options: options dict(传入的是 merge patch, 不是整体替换——key 级 merge)
      - 其他任何 options 子 key: 也可直接通过顶层传,会被自动合并进 options
    """
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

    # ── options 合并:顶层特殊 key (fetch_mode/captcha_type) + options.patch + 其他未知顶层 key ──
    options_patch: dict = {}
    # 1) 直接传的 fetch_mode / captcha_type 优先
    if payload.get("fetch_mode") in ("openapi", "playwright", "dual", "http"):
        options_patch["fetch_mode"] = payload["fetch_mode"]
    if payload.get("captcha_type") in ("none", "text", "math"):
        options_patch["captcha_type"] = payload["captcha_type"]
    # 2) options 整体 patch (前端表单直接 options 展开提交)
    if isinstance(payload.get("options"), dict):
        options_patch.update({k: v for k, v in payload["options"].items() if v is not None})
    # 3) 其他任何 options 已知子 key (顶层传了就合并,避免前端必须包 options.)
    known_option_keys = {
        "login_url_path", "captcha_img_selector", "username_placeholder", "password_placeholder",
        "captcha_input_placeholder", "submit_button_text", "web_operator_entry_path",
        "web_session_entry_path",
    }
    for k in known_option_keys:
        if k in payload and payload[k] is not None:
            options_patch[k] = payload[k]
    if options_patch:
        row.options = _merge_options_update(row.options, options_patch)

    db.commit()
    db.refresh(row)
    return _row_to_config(row)


# ---------- 兼容别名 (旧代码引用) ----------
def get_all_configs(db: Session) -> list[dict]:
    """兼容旧导入名: get_all_configs → list_sync_source_configs"""
    return list_sync_source_configs(db)
