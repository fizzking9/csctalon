#!/usr/bin/env python3
"""直接用 SQL UPDATE 设置 captcha_type='math'."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from sqlalchemy import select

with SessionLocal() as db:
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "scrm_operator"))
    print("BEFORE options.captcha_type =", (row.options or {}).get("captcha_type"))
    new_opts = dict(row.options or {})
    new_opts["captcha_type"] = "math"
    row.options = new_opts
    db.commit()
    db.expire_all()
    row2 = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "scrm_operator"))
    print("AFTER  options.captcha_type =", (row2.options or {}).get("captcha_type"))
    print("✅ OK")
