"""数据源分组服务 - 同域多源共享登录凭证

设计(2026-08-25):
  - 按 base_url 域名自动归组(如 BMS 主平台 / 闪猫 / SCRM / ai-acc)
  - 组级可配置账号密码(Fernet 加密), 组内单源可覆盖(单源凭证优先)
  - 运行时凭证解析: 单源凭证 > 组凭证 > 种子/.env
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret, is_fernet_ciphertext
from app.models.source_group import SourceGroup
from app.models.sync_source_config import SyncSourceConfig

logger = logging.getLogger(__name__)


# ── 归组规则: base_url → group_key ──────────────────────────────────
def _group_key_for_base_url(base_url: str | None) -> str | None:
    """按 base_url 推导组标识(同域共享登录态 → 归同组)"""
    if not base_url:
        return None
    url = base_url.strip().lower()
    if "wifi.cloud-cat.cn" in url or "support.cloud-cat.cn" in url:
        return "bms-wifi"
    if "fast.cloud-cat.cn" in url:
        return "shanmao"
    if "ai-acc" in url or "ai_acc" in url:
        return "aiacc"
    if "bilinl.com" in url or "baiyinqifu" in url:
        return "scrm"
    # 其它域名: 用域名主体作组 key
    m = re.search(r"//([^/:]+)", url)
    if m:
        host = m.group(1)
        host = re.sub(r"^www\.", "", host).replace(".", "_")
        return f"group_{host}"
    return None


def _group_name_for_key(group_key: str) -> str:
    return {
        "bms-wifi": "BMS 主平台",
        "shanmao": "闪猫平台",
        "aiacc": "ai-acc 平台",
        "scrm": "SCRM 平台",
    }.get(group_key, group_key)


# ── Seed 自动归组 ───────────────────────────────────────────────────
def ensure_source_groups_registered(db: Session) -> dict:
    """幂等归组: 为现有 sync_source_configs 自动建组并设置 group_key(缺失才建/才改)

    仅对 group_key 为空的源自动归组; 用户手动改过的 group_key 不覆盖。
    """
    created = 0
    assigned = 0
    rows = db.scalars(select(SyncSourceConfig).order_by(SyncSourceConfig.id)).all()
    group_order: dict[str, int] = {}
    for row in rows:
        gkey = _group_key_for_base_url(row.base_url)
        if not gkey:
            continue
        if row.group_key is None:
            row.group_key = gkey
            assigned += 1
        group_order.setdefault(gkey, len(group_order))
    for gkey, order in group_order.items():
        existing = db.scalar(select(SourceGroup).where(SourceGroup.group_key == gkey))
        if existing:
            continue
        db.add(SourceGroup(
            group_key=gkey,
            group_name=_group_name_for_key(gkey),
            base_url=None,
            sort_order=order,
        ))
        created += 1
    if created or assigned:
        db.commit()
    logger.info(f"数据源分组注册完成: 新建组 {created} 个, 归组源 {assigned} 个")
    return {"created": created, "assigned": assigned}


# ── 凭证解析 ────────────────────────────────────────────────────────
def _safe_decrypt(ciphertext: str | None) -> str | None:
    """解密凭证字段; 解密结果若仍是 Fernet 密文(密钥不匹配/脏数据) → 视为不可用返回 None。

    背景(2026-09-06): decrypt_secret 解密失败回退返回原值(兼容历史明文), 但旧密钥
    加密的密文在密钥轮换后解密失败会原样返回 `gAAAA...` → 若被当作有效明文账号使用
    会登录报"用户名不存在或密码错误"(生产 device_combo 事故)。这里兜底识别该场景,
    让调用方走组级回退/提示重配, 而不是把密文当账号。
    """
    if not ciphertext:
        return None
    decrypted = decrypt_secret(ciphertext)
    if decrypted is None:
        return None
    if is_fernet_ciphertext(decrypted):
        # 解密失败且原文本身是密文(密钥不匹配) → 不可用
        return None
    return decrypted


def get_group_credentials(db: Session, group_key: str | None) -> tuple[str | None, str | None]:
    """取组级凭证(解密); 无组或无凭证返回 (None, None)"""
    if not group_key:
        return None, None
    g = db.scalar(select(SourceGroup).where(SourceGroup.group_key == group_key))
    if not g:
        return None, None
    user = _safe_decrypt(g.username) if g.username else None
    pwd = _safe_decrypt(g.password) if g.password else None
    return user, pwd


def resolve_credentials(db: Session, row: SyncSourceConfig) -> tuple[str | None, str | None]:
    """运行时凭证解析: 单源凭证 > 组凭证 > (None)
    供 get_sync_source_config 使用, 单源已配置且**可解密**时优先, 否则继承组凭证。

    ⚠️ 2026-09-06 加固: 单源解密结果若是 Fernet 密文特征(旧密钥脏数据) → 视为未配置,
      回退继承组级凭证(否则会把 `gAAAA...` 密文当账号登录报错, 并连累同组其它源)。

    ⚠️ 2026-09-07 加固: 返回增加"凭证来源"标记(own/group/none), 供 fetch_all 熔断细化使用——
      单源(own)凭证失败只熔断本源, 不再连累同组用组凭证的源(见 bms_fetcher.fetch_all)。
    """
    user, pwd, _src = resolve_credentials_with_source(db, row)
    return user, pwd


def resolve_credentials_with_source(
    db: Session, row: SyncSourceConfig
) -> tuple[str | None, str | None, str]:
    """运行时凭证解析(带来源标记): 单源凭证 > 组凭证 > (None)

    返回 (username, password, source):
      - source = "own"   : 命中该源独立配置的单源凭证(解密有效)
      - source = "group" : 继承组级凭证(同域共享登录态, 组凭证坏则整组受影响)
      - source = "none"  : 无任何可用凭证

    设计背景(2026-09-07): 单源凭证是"该源私有覆盖", 登录失败不应连累同组其它用
    组凭证的源; 组凭证是"同组共享", 登录失败说明整组共享账号出问题, 应熔断整组。
    调用方(fetch_all 熔断逻辑 / 前端提示)需靠 source 区分这两种场景。
    """
    own_user = _safe_decrypt(row.username) if row.username else None
    own_pwd = _safe_decrypt(row.password) if row.password else None
    if own_user and own_pwd:
        return own_user, own_pwd, "own"
    group_user, group_pwd = get_group_credentials(db, row.group_key)
    if (own_user or group_user) and (own_pwd or group_pwd):
        return own_user or group_user, own_pwd or group_pwd, "group"
    return None, None, "none"


# ── API ─────────────────────────────────────────────────────────────
def list_source_groups(db: Session) -> list[dict]:
    """分组视图: 每个组 + 组内源列表 + 组凭证状态"""
    groups = db.scalars(select(SourceGroup).order_by(SourceGroup.sort_order, SourceGroup.id)).all()
    group_rows = {g.group_key: g for g in groups}
    sources = db.scalars(select(SyncSourceConfig).order_by(SyncSourceConfig.sort_order, SyncSourceConfig.id)).all()

    result: list[dict] = []
    seen = set()
    for src in sources:
        gkey = src.group_key
        if gkey not in seen:
            seen.add(gkey)
            g = group_rows.get(gkey)
            result.append({
                "group_key": gkey,
                "group_name": g.group_name if g else (gkey or "未分组"),
                "base_url": g.base_url if g else None,
                "has_credentials": bool(g and g.username and g.password),
                "sources": [],
            })
        bucket = next((x for x in result if x["group_key"] == gkey), None)
        if bucket is not None:
            bucket["sources"].append({
                "data_type": src.data_type,
                "name": src.name,
                "page_path": src.page_path,
                "base_url": src.base_url,
                "has_credentials": bool(src.username and src.password),
                "default_sync": src.default_sync,
                "enabled": src.enabled,
            })
    # 未归组源(理论上不会, seed 会归组)
    return result


def update_source_group(db: Session, group_key: str, payload: dict) -> dict | None:
    """更新组配置: 组名/域名/组级账号密码(留空=不改)"""
    g = db.scalar(select(SourceGroup).where(SourceGroup.group_key == group_key))
    if not g:
        return None
    if payload.get("group_name"):
        g.group_name = payload["group_name"]
    if payload.get("base_url") is not None:
        g.base_url = payload["base_url"] or None
    if payload.get("username"):
        g.username = encrypt_secret(payload["username"])
    if payload.get("password"):
        g.password = encrypt_secret(payload["password"])
    db.commit()
    db.refresh(g)
    return {
        "group_key": g.group_key,
        "group_name": g.group_name,
        "base_url": g.base_url,
        "has_credentials": bool(g.username and g.password),
    }


def assign_source_group(db: Session, data_type: str, group_key: str | None) -> bool:
    """源归组(手动调整)"""
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
    if not row:
        return False
    row.group_key = group_key
    db.commit()
    return True
