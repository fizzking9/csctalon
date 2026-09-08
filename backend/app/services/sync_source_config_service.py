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
from app.core.crypto import decrypt_secret, encrypt_secret, is_fernet_ciphertext
from app.models.sync_source_config import SyncSourceConfig
from app.services.bms_fetcher import BMS_DATA_SOURCES
from app.services.scrm_client import SCRM_DATA_SOURCES
from app.services.source_group_service import (
    ensure_source_groups_registered,
    get_group_credentials,
    resolve_credentials_with_source,
)

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
            # 2026-08-06 修复: SCRM 默认从 openapi → playwright (OpenAPI 会被平台风控/限流,
            # 且用户反馈 OpenAPI 抓不到今日增量, Playwright 直接模拟浏览器导出与后台页面一致)
            "fetch_mode", "playwright" if _is_scrm_type(seed_cfg.get("data_type", "")) else "playwright"
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

            # ── SCRM 模式补丁 (2026-08-06): 旧默认 openapi → 强制改成 playwright ──
            #   用户需求: SCRM 同步默认 Playwright。之前 scrm_client.SCRM_DATA_SOURCES
            #   4 个源的默认 fetch_mode = "openapi",DB 中已存在的老行 options.fetch_mode
            #   会停在旧默认 openapi。我们这里做补丁:如果当前 fetch_mode 是 None 或
            #   "openapi" (= 老默认值,不是用户主动选的其他模式),强制升级到 playwright。
            #   如果用户之前明确选了 dual/http 等非 openapi 的其他值,则保留用户选择不动。
            if _is_scrm_type(data_type):
                cur_opts = copy.deepcopy(existing.options or {})
                cur_fm = cur_opts.get("fetch_mode")
                if cur_fm in (None, "openapi", "OpenAPI", "openapi_v1"):
                    new_fetch_mode = (
                        cfg.get("fetch_mode")
                        or (cfg.get("options") or {}).get("fetch_mode")
                        or "playwright"
                    )
                    cur_opts["fetch_mode"] = new_fetch_mode
                    try:
                        existing.options = cur_opts or None
                        patched_fields += 1
                        logger.info(
                            f"[sync_source_configs] SCRM 模式自动升级: {data_type}.fetch_mode "
                            f"{cur_fm!r} → {new_fetch_mode!r} (旧默认 openapi 补丁)"
                        )
                    except Exception as e:
                        logger.warning(
                            f"SCRM fetch_mode 补丁更新 {data_type} 失败: {e}"
                        )
    if created or updated_options or patched_fields:
        db.commit()
    # 自动归组(按 base_url): 幂等, 仅对未归组源生效
    try:
        ensure_source_groups_registered(db)
    except Exception as e:
        logger.warning(f"数据源自动归组失败(不影响主流程): {e}")
    total = len(all_sources)
    logger.info(
        f"同步源配置注册完成: 新建 {created} 个, 补默认 options {updated_options} 个, "
        f"旧错误值强制补丁 {patched_fields} 个, 共 {total} 个(BMS+SCRM)"
    )
    return {"created": created, "updated_options": updated_options,
            "patched_fields": patched_fields, "total": total}


# ── 读取 (运行时 + API) ──────────────────────────────────────────────
def _decrypt_options_secrets(raw_opts: dict | None) -> dict:
    """把 options 里需要加密保存的敏感子 key(web_username/web_password)解密成明文。
    非敏感 key 原样返回;输入 None 返回空 dict。"""
    if not raw_opts:
        return {}
    opts = {**raw_opts}
    for k in ("web_username", "web_password"):
        if isinstance(opts.get(k), str) and opts[k]:
            try:
                opts[k] = decrypt_secret(opts[k])
            except Exception:
                # 旧值可能是明文(第一次升级前),解密失败就当原值使用(兼容)
                pass
    return opts


def _row_effective_source(db: Session | None, row: SyncSourceConfig) -> str:
    """DB 行 → 凭证生效来源标记(own/group/none)

    - own  : 单源凭证解密有效, 运行时实际用该源独立凭证
    - group: 单源为空/脏密文不可用 → 实际继承组凭证(或 .env/种子)
    - none : 无任何可用凭证
    """
    if db is not None:
        _u, _p, src = resolve_credentials_with_source(db, row)
        return src
    # 无 db 退化: 仅凭密文原文推断(与 _row_to_config 无 db 分支一致)
    u = decrypt_secret(row.username) if row.username else None
    p = decrypt_secret(row.password) if row.password else None
    if u and p and not is_fernet_ciphertext(u) and not is_fernet_ciphertext(p):
        return "own"
    if row.group_key:
        return "group"
    return "none"


def _own_cred_differs_group(db: Session | None, row: SyncSourceConfig) -> bool:
    """本源是否配置了与组账号不同的单源凭证(仅用于前端提示, 不泄露明文)

    判定条件: 单源凭证解密有效(source=own) 且 单源账号 != 组账号。
    - 若本源未配单源(继承组) → False
    - 若本源单源账号与组账号相同(冗余但等价) → False
    - 若本源单源账号与组账号不同(本次事故场景) → True → 前端标黄提示
    """
    if not row.username or not row.password:
        return False
    own_u = decrypt_secret(row.username) if row.username else None
    if not own_u or is_fernet_ciphertext(own_u):
        return False
    gu, _gp = get_group_credentials(db, row.group_key) if db is not None else (None, None)
    if not gu:
        return False  # 无组凭证可比, 不误报
    return own_u != gu


def _row_to_config(db: Session | None, row: SyncSourceConfig) -> dict:
    """DB 行 → 运行配置 dict(解密凭证 + 合并 options,并抛出顶层关键字段)

    凭证解析: 单源凭证 > 组凭证(source_groups) > 种子/.env。
    """
    seed = _seed_of(row.data_type)
    seed_with_dt = {**seed, "data_type": row.data_type}
    # ⚠️ 注意: _resolve_effective_options 的入参 row.options 仍是"加密状态的原始 options",
    #   解密 options 内敏感 key 需要放在 effective_options 解析后再做,
    #   否则 seed 默认值填充逻辑与加密判断会互相覆盖。
    effective_options = _resolve_effective_options(row.options, seed_with_dt)
    effective_options = _decrypt_options_secrets(effective_options)
    fetch_mode = effective_options.get(
        # 2026-08-06: SCRM fallback 默认从 openapi → playwright
        "fetch_mode", "playwright" if _is_scrm_type(row.data_type) else "playwright"
    )
    captcha_type = effective_options.get("captcha_type", "none")
    requires_captcha = _requires_captcha_from_options(effective_options)
    # 凭证: 单源优先, 空则继承组级凭证(同域共享登录态)
    # ⚠️ 2026-09-07: 增加 credential_source(own/group/none) 与 credential_is_own,
    #   供 bms_fetcher.fetch_all 熔断细化(单源凭证失败不连累同组)与前端提示使用。
    if db is not None:
        username, password, credential_source = resolve_credentials_with_source(db, row)
    else:
        # 无 db 退化分支: 直接解密 + 兜底识别"解密失败仍为密文"的脏数据(密钥轮换后)
        username = decrypt_secret(row.username) if row.username else None
        password = decrypt_secret(row.password) if row.password else None
        if is_fernet_ciphertext(username):
            username = None
        if is_fernet_ciphertext(password):
            password = None
        credential_source = "own" if (username and password) else "none"
    # 组级账号明文(供前端/熔断提示"本源与组账号是否一致"), 无组返回 None
    group_username = None
    if db is not None:
        gu, _gp = get_group_credentials(db, row.group_key)
        group_username = gu
    return {
        "data_type": row.data_type,
        "name": row.name,
        "base_url": row.base_url,
        "page_path": row.page_path,
        "time_labels": row.time_labels,
        "group_key": row.group_key,
        "username": username,
        "password": password,
        "credential_source": credential_source,
        "credential_is_own": credential_source == "own",
        "group_username": group_username,
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
    """读取单个源运行配置(解密凭证, 单源优先继承组凭证);DB 缺失时回退到种子 + .env 凭证"""
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
    if row:
        return _row_to_config(db, row)
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
    user = env_user or None
    pwd = env_pwd or None
    return {
        "data_type": data_type,
        "name": cfg["name"],
        "base_url": cfg.get("base_url", getattr(settings, "SCRM_BASE_URL", "") if is_scrm else cfg.get("base_url", "")),
        "page_path": cfg.get("page_path", ""),
        "time_labels": cfg.get("time_labels"),
        "username": user,
        "password": pwd,
        # 种子/.env 回退场景统一视为组/共享级来源(登录失败应熔断整组)
        "credential_source": "own" if user else "none",
        "credential_is_own": bool(user),
        "group_username": None,
        "options": effective_options,
        "fetch_mode": effective_options.get("fetch_mode", "playwright" if is_scrm else "playwright"),
        "captcha_type": effective_options.get("captcha_type", "none"),
        "requires_captcha": _requires_captcha_from_options(effective_options),
        "default_sync": cfg.get("default_sync", True),
        "enabled": True,
    }


def list_scheduled_bms_sources(db: Session) -> dict[str, list[str]]:
    """读取"应纳入定时采集"的 BMS 源集合, 按高频/低频分档(A-20260907-001 LOCKED / #31)。

    返回 {"all": [...], "high": [...], "low": [...]}:
      - all  = sync_source_configs 中 default_sync=true AND enabled=true 且非 SCRM 的源
              (data_type 不以 scrm_ 开头), 按 sort_order 排序 —— UI「纳入周期采集」开关的真源。
      - high = all ∩ 代码种子 DEFAULT_SYNC_TYPES(内置核心高频源: refund/ticket/wallet/cs_record)
               —— 白天每小时 + 5:00/8:00 兜底档跑这些。
      - low  = all − high —— 大表/报表/档案型低频源, 每天 1 次(低频档 6:00)跑。

    设计背景: 原调度器用代码常量 DEFAULT_SYNC_TYPES 决定定时源, 从不读 DB default_sync,
    导致 UI「纳入周期采集」开关(改 DB)不生效(生产 12 源 true 但定时只跑 4 个)。
    本函数把"调度源集合"收敛到 DB(default_sync=true), 高频/低频用"是否种子核心源"区分。

    注意: 内部延迟 import bms_sync.DEFAULT_SYNC_TYPES, 避免与 bms_sync 的循环依赖。
    """
    rows = db.scalars(
        select(SyncSourceConfig)
        .where(
            SyncSourceConfig.default_sync.is_(True),
            SyncSourceConfig.enabled.is_(True),
            ~SyncSourceConfig.data_type.like("scrm_%"),
        )
        .order_by(SyncSourceConfig.sort_order, SyncSourceConfig.data_type)
    ).all()
    # 派生源不纳入周期采集(#38 / #31):
    #   - device_tag(标签定义)不是独立 BMS 抓取源 —— 与 device_tag_binding 抓同一份打标明细 CSV,
    #     标签定义由 device_tag_binding 导入时按 tag_name 自动补插(bms_fetcher L121-126 注释)。
    #     device_tag 若被 UI 打开 default_sync, 调度会抓明细并按 tag_name upsert → 同批重复必炸
    #     (CardinalityViolation, 见 upsert.py upsert_device_tags)。此处显式剔除防复发。
    _DERIVED_NON_SCHEDULABLE = frozenset({"device_tag"})
    all_types = [r.data_type for r in rows if r.data_type not in _DERIVED_NON_SCHEDULABLE]
    # 延迟 import 规避循环依赖(bms_sync → sync_source_config_service)
    from app.services.bms_sync import DEFAULT_SYNC_TYPES

    high_set = set(DEFAULT_SYNC_TYPES)
    high = [dt for dt in all_types if dt in high_set]
    low = [dt for dt in all_types if dt not in high_set]
    return {"all": all_types, "high": high, "low": low}


def list_sync_source_configs(db: Session) -> list[dict]:
    """列出所有同步源配置(API 用,凭证掩码;带 fetch_mode/captcha_type/options 给前端表单)"""
    rows = db.scalars(select(SyncSourceConfig).order_by(SyncSourceConfig.sort_order, SyncSourceConfig.id)).all()
    result = []
    for row in rows:
        seed = _seed_of(row.data_type)
        seed_with_dt = {**seed, "data_type": row.data_type}
        # 1) 先解析 effective_options(合并种子默认值)
        effective_options = _resolve_effective_options(row.options, seed_with_dt)
        # 2) 这里是「列表/编辑态」接口,不应该把明文密码抛给前端浏览器;
        #    web_username/web_password:   - 先解密再丢弃明文,只保留 xxx_configured 布尔标志位给 UI 动态占位
        #                                 - 保证 openEdit 里 credentialsState.xxx_configured 判断正确
        configured_web_username = False
        configured_web_password = False
        opts_sanitized = _decrypt_options_secrets(effective_options)
        if isinstance(opts_sanitized.get("web_username"), str) and opts_sanitized["web_username"]:
            configured_web_username = True
        if isinstance(opts_sanitized.get("web_password"), str) and opts_sanitized["web_password"]:
            configured_web_password = True
        # 敏感 key 移除(不再返回给前端列表/编辑态)
        opts_sanitized.pop("web_username", None)
        opts_sanitized.pop("web_password", None)
        # 补上布尔标志位,让前端 openEdit 能给 placeholder 提示
        opts_sanitized["web_username_configured"] = bool(configured_web_username)
        opts_sanitized["web_password_configured"] = bool(configured_web_password)
        fetch_mode = opts_sanitized.get(
            # 2026-08-06: SCRM fallback 默认从 openapi → playwright (与 seed 一致)
            "fetch_mode", "playwright" if _is_scrm_type(row.data_type) else "playwright"
        )
        captcha_type = opts_sanitized.get("captcha_type", "none")
        requires_captcha = _requires_captcha_from_options(opts_sanitized)
        result.append(
            {
                "data_type": row.data_type,
                "name": row.name,
                "base_url": row.base_url,
                "page_path": row.page_path,
                "time_labels": row.time_labels,
                "group_key": row.group_key,
                "username": row.username,  # 加密原文,前端判断是否已配置(空=未改过)
                "password": row.password,  # 同上
                "has_credentials": bool(row.username and row.password),
                # ── 2026-09-07: 凭证来源标记(供前端提示"单源独立 vs 继承组") ──
                # 注意: has_credentials 是"密文原文是否非空"; credential_source 才是
                # "解密后实际生效来源"。脏密文(row.username 非空但解密失败)时 has_credentials=True
                # 但 credential_source 可能是 group(回退组凭证)——前端应以此为准提示用户清理。
                "credential_source": _row_effective_source(db, row),
                "own_cred_differs_group": _own_cred_differs_group(db, row),
                "default_sync": row.default_sync,
                "enabled": row.enabled,
                "sort_order": row.sort_order,
                # ── 新增: 抓取模式 & 验证码类型 & options(给前端编辑表单) ──
                "options": opts_sanitized,
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
    # ── 凭证字段 ──
    # 语义(2026-09-07 修订, 修复"UI 清空凭证无效"治理缺口 #28):
    #   - payload 中**不含** username/password key  → 不改(兼容旧调用: 只改其它字段)
    #   - key 存在且值为**非空字符串**            → 覆盖为新凭证(加密入库)
    #   - key 存在且值为 None/空串                 → **显式清空**(置 NULL, 回退继承组凭证)
    # 背景: 此前"空串=不改"导致前端提示「清空账号密码以继承组凭证」实际无效——
    #       用户无法通过 UI 清除误填的单源凭证(device_combo 误填 tanzk 无法回退组)。
    if "username" in payload:
        if payload["username"]:
            row.username = encrypt_secret(payload["username"])
        else:
            row.username = None  # 显式清空 → 运行时继承组凭证
    if "password" in payload:
        if payload["password"]:
            row.password = encrypt_secret(payload["password"])
        else:
            row.password = None  # 显式清空 → 运行时继承组凭证

    # ── options 合并:顶层特殊 key (fetch_mode/captcha_type) + options.patch + 其他未知顶层 key ──
    _VALID_FETCH_MODES = {"openapi", "playwright", "dual", "http"}
    _VALID_CAPTCHA_TYPES = {"none", "text", "math"}

    options_patch: dict = {}

    # ---------- 1) 直接传的 fetch_mode / captcha_type 优先 ----------
    #   修复 2026-08-06: 之前只允许精确小写匹配,不在枚举中就静默忽略,导致用户传
    #   "Playwright" / " playwright " / 拼写有误时,保存看起来成功但实际没写 DB,
    #   下次进来又回到旧值(默认 openapi)。
    #   现在: 对字符串先 strip().lower() 规范化, 仍不合法就显式抛 ValueError,
    #   前端拿到 400/422 给用户看明确错误提示, 不再默默吃掉。
    if "fetch_mode" in payload and payload["fetch_mode"] is not None:
        raw_fm = str(payload["fetch_mode"]).strip().lower() if isinstance(payload["fetch_mode"], str) else None
        if raw_fm and raw_fm not in _VALID_FETCH_MODES:
            raise ValueError(
                f"[update_sync_source_config] data_type={data_type} fetch_mode={raw_fm!r} 非法;"
                f" 允许值: {sorted(_VALID_FETCH_MODES)}"
            )
        if raw_fm:
            options_patch["fetch_mode"] = raw_fm

    if "captcha_type" in payload and payload["captcha_type"] is not None:
        raw_ct = str(payload["captcha_type"]).strip().lower() if isinstance(payload["captcha_type"], str) else None
        if raw_ct and raw_ct not in _VALID_CAPTCHA_TYPES:
            raise ValueError(
                f"[update_sync_source_config] data_type={data_type} captcha_type={raw_ct!r} 非法;"
                f" 允许值: {sorted(_VALID_CAPTCHA_TYPES)}"
            )
        if raw_ct:
            options_patch["captcha_type"] = raw_ct

    # ---------- 2) options 整体 patch (前端表单直接 options 展开提交) ----------
    #   如果 options 里也有 fetch_mode/captcha_type, 同样做规范化 + 合法性校验(与顶层一致)。
    if isinstance(payload.get("options"), dict):
        for k, raw_v in payload["options"].items():
            v = raw_v
            # 先处理加密: web_username / web_password 等敏感 key 明文进 options 前必须加密
            if k in ("web_username", "web_password") and isinstance(raw_v, str) and raw_v:
                v = encrypt_secret(raw_v)
            elif k in ("web_username", "web_password") and (raw_v is None or raw_v == ""):
                v = None  # None 会被 merge 时从 options 中删除(等于"清空该字段")
            if raw_v is None:
                options_patch[k] = None
                continue
            if k == "fetch_mode" and isinstance(raw_v, str):
                fm = raw_v.strip().lower()
                if fm not in _VALID_FETCH_MODES:
                    raise ValueError(
                        f"[update_sync_source_config] data_type={data_type} options.fetch_mode={raw_v!r} 非法;"
                        f" 允许值: {sorted(_VALID_FETCH_MODES)}"
                    )
                v = fm
            if k == "captcha_type" and isinstance(raw_v, str):
                ct = raw_v.strip().lower()
                if ct not in _VALID_CAPTCHA_TYPES:
                    raise ValueError(
                        f"[update_sync_source_config] data_type={data_type} options.captcha_type={raw_v!r} 非法;"
                        f" 允许值: {sorted(_VALID_CAPTCHA_TYPES)}"
                    )
                v = ct
            options_patch[k] = v

    # ---------- 3) 其他任何 options 已知子 key (顶层传了就合并,避免前端必须包 options.) ----------
    known_option_keys = {
        "login_url_path", "captcha_img_selector", "username_placeholder", "password_placeholder",
        "captcha_input_placeholder", "submit_button_text", "web_operator_entry_path",
        "web_session_entry_path",
        # SCRM Playwright 模式:独立一对网页登录账号密码(明文传进来时会在 options.patch 分支加密,
        # 这里保留顶层也支持,但顶层传了也要加密后才合入 options)
        "web_username", "web_password",
    }
    for k in known_option_keys:
        if k in payload and payload[k] is not None:
            # 顶层传的敏感 key 先加密(如果传的是明文)再合并
            if k in ("web_username", "web_password") and isinstance(payload[k], str) and payload[k]:
                options_patch[k] = encrypt_secret(payload[k])
            else:
                options_patch[k] = payload[k]
    if options_patch:
        row.options = _merge_options_update(row.options, options_patch)
        logger.info(
            f"[update_sync_source_config] data_type={data_type} options 已更新:"
            f" options_patch={options_patch}; 合并后 row.options={row.options}"
        )

    db.commit()
    db.refresh(row)
    # ⚠️ 2026-09-05 修复: _row_to_config 需 db(用于 resolve_credentials 组凭证回退),
    #   原漏传 db 导致 update_sync_source_config 一调用即 TypeError
    return _row_to_config(db, row)


# ---------- 兼容别名 (旧代码引用) ----------
def get_all_configs(db: Session) -> list[dict]:
    """兼容旧导入名: get_all_configs → list_sync_source_configs"""
    return list_sync_source_configs(db)
