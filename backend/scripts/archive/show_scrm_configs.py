#!/usr/bin/env python3
"""查询并打印 DB 中 scrm_* 相关配置行(username/password 加密值 + options 明文)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from sqlalchemy import select

with SessionLocal() as db:
    rows = db.scalars(select(SyncSourceConfig).where(
        SyncSourceConfig.data_type.like("scrm%")
    )).all()

print(f"\n共查到 {len(rows)} 行 scrm 相关配置:\n")
for r in rows:
    uname_cipher = r.username or ""
    pwd_cipher = r.password or ""
    try:
        uname = decrypt_secret(uname_cipher) if uname_cipher else ""
    except Exception:
        uname = "<解密失败>"
    try:
        pwd = decrypt_secret(pwd_cipher) if pwd_cipher else ""
    except Exception:
        pwd = "<解密失败>"
    opts = r.options or {}
    wu = opts.get("web_username")
    wp = opts.get("web_password")
    try:
        wu_dec = decrypt_secret(wu) if wu and isinstance(wu, str) and wu.startswith("ENC:") else wu
    except Exception:
        wu_dec = "<解密失败>"
    try:
        wp_dec = decrypt_secret(wp) if wp and isinstance(wp, str) and wp.startswith("ENC:") else wp
    except Exception:
        wp_dec = "<解密失败>"

    print("=" * 80)
    print(f"id={r.id}  data_type={r.data_type!r}  name={r.name!r}  enabled={r.enabled}")
    print(f"  base_url           : {r.base_url}")
    print(f"  fetch_mode         : {opts.get('fetch_mode')}")
    print(f"  -- 【列 username / password(旧,OpenAPI 约定)】--")
    print(f"    username(明文)   : {uname!r}  {'(空)' if not uname else ''}")
    print(f"    password(明文)   : {'*' * len(pwd) if pwd else '(空)'}  ({pwd!r})")
    print(f"  -- 【options 独立字段 web_username / web_password(Playwright 网页登录用)】--")
    print(f"    web_username     : {wu_dec!r}  {'(空)' if not wu_dec else ''}")
    print(f"    web_password     : {'*' * len(wp_dec) if wp_dec else '(空)'}  ({wp_dec!r})")
    print(f"  -- 其他 options --")
    other_opts = {k: v for k, v in opts.items() if k not in ("web_username", "web_password")}
    print(json.dumps(other_opts, ensure_ascii=False, indent=2))
print("=" * 80)
