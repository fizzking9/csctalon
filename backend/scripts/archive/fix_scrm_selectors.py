#!/usr/bin/env python3
"""修正 SCRM 同步源的选择器 placeholder 配置,并根据实际页面调整验证码设置。"""
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
            print(f"[SKIP] {dt}: 未找到配置")
            continue
        opts = row.options or {}
        changed = False

        # 修正 placeholder 为实际探测值
        old_up = opts.get("username_placeholder")
        if old_up != "登录名/手机号":
            opts["username_placeholder"] = "登录名/手机号"
            print(f"[{dt}] username_placeholder: {old_up!r} -> '登录名/手机号'")
            changed = True

        old_pp = opts.get("password_placeholder")
        if old_pp != "密码":
            opts["password_placeholder"] = "密码"
            print(f"[{dt}] password_placeholder: {old_pp!r} -> '密码'")
            changed = True

        old_cap = opts.get("captcha_input_placeholder")
        # 探测结果: 没有验证码,也没有验证码输入框,把 placeholder 清空,并把 captcha_type 改为 none
        if old_cap not in (None, "", "none"):
            opts["captcha_input_placeholder"] = ""
            print(f"[{dt}] captcha_input_placeholder: {old_cap!r} -> '' (没有验证码)")
            changed = True

        old_ct = opts.get("captcha_type")
        if old_ct != "none":
            opts["captcha_type"] = "none"
            print(f"[{dt}] captcha_type: {old_ct!r} -> 'none' (探测到无验证码)")
            changed = True

        # 验证码 img 选择器也清空(没有验证码图片)
        old_cis = opts.get("captcha_img_selector")
        if old_cis not in (None, "", "none"):
            opts["captcha_img_selector"] = ""
            print(f"[{dt}] captcha_img_selector: {old_cis!r} -> '' (无验证码图片)")
            changed = True

        if changed:
            row.options = opts
            db.commit()
            print(f"[{dt}] ✅ 已保存修正后的 options")
        else:
            print(f"[{dt}] 无需修改,配置已正确")

finally:
    db.close()
print("\n全部修正完成!")
