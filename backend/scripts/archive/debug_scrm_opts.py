#!/usr/bin/env python3
"""debug SCRM 配置解密: 打印 DB 原始 opts + 解密 opts, 找出为啥读不到 username."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from app.services.sync_source_config_service import _decrypt_options_secrets
from sqlalchemy import select

with SessionLocal() as db:
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "scrm_operator"))
    print("base_url =", row.base_url)
    raw_opts = row.options or {}
    print("RAW options keys:", list(raw_opts.keys()))
    for k, v in raw_opts.items():
        if isinstance(v, str) and len(v) > 80:
            print(f"  {k}: <长字符串, len={len(v)}>")
        else:
            print(f"  {k}: {v!r}")
    dec = _decrypt_options_secrets(raw_opts)
    print("\nDECRYPTED options keys:", list(dec.keys()))
    for k, v in dec.items():
        if k.lower() in ("password", "web_password") and v:
            print(f"  {k}: '{'*'*len(v)}'")
        else:
            print(f"  {k}: {v!r}")
