#!/usr/bin/env python3
"""修正 DB 中 SCRM 配置: captcha_type 改成 text(直接输入识别文本, 不是数学计算)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from sqlalchemy import select

SCRM_DTS = ["scrm_operator", "scrm_session", "scrm_session_transfer"]

db = SessionLocal()
try:
    for dt in SCRM_DTS:
        row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == dt))
        if not row:
            print(f"[{dt}] 未找到配置, 跳过")
            continue
        opts = row.options or {}
        old_ct = opts.get("captcha_type")
        if old_ct != "text":
            opts["captcha_type"] = "text"
            row.options = opts
            db.commit()
            print(f"[{dt}] captcha_type: {old_ct!r} -> 'text' ✅")
        else:
            print(f"[{dt}] captcha_type 已是 'text', 无需修改")
finally:
    db.close()
print("\n完成!")
