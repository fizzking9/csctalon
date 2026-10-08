#!/usr/bin/env python3
"""debug SCRM: 打印 username/password 列 + options, 并手动修 captcha_type=text + 测试手动填账号密码."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.crypto import decrypt_secret
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from sqlalchemy import select

with SessionLocal() as db:
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "scrm_operator"))
    print(f"base_url = {row.base_url}")
    print(f"page_path = {row.page_path}")
    raw_uname = row.username
    raw_pwd = row.password
    try:
        uname = decrypt_secret(raw_uname) if raw_uname else ""
    except Exception as e:
        uname = f"<解密失败: {e}>"
    try:
        pwd = decrypt_secret(raw_pwd) if raw_pwd else ""
    except Exception as e:
        pwd = f"<解密失败: {e}>"
    print(f"username (列) = {uname[:3] if uname and len(uname)>=3 else ''}***  len={len(uname or '')}")
    print(f"password (列) = {'*'*len(pwd) if pwd else ''}  len={len(pwd or '')}")
    opts = row.options or {}
    print("options.captcha_type =", opts.get("captcha_type"))
    print("options keys:", list(opts.keys()))
