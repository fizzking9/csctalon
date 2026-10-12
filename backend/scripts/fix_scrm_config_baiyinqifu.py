#!/usr/bin/env python3
"""修正 DB 中 SCRM 配置: base_url 改为真实的 https://scrm.baiyinqifu.com/, 以及对应的选择器。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from sqlalchemy import select

# 根据我们的探测:
#   - 真实管理后台: https://scrm.baiyinqifu.com/
#   - 登录 URL: /login (会自动加 redirect 参数)
#   - Element UI (input class = el-input__inner, 按钮也有 el-button 类)
#   - 账号 input: name='username', placeholder='请输入登录账号/手机号'
#   - 密码 input: name='password', placeholder='请输入密码'
#   - 验证码 input: name=''(空), placeholder='请输入验证码'
#   - 验证码 img: class='show-code show-code-img'  → 选择器 img.show-code-img (原来的完全正确!)
#   - 登录按钮: text='登录' (不是 '登 录', 中间无空格!)
#   - 验证码类型: 数学计算式(如 6X33 = 6*33 = 198)
#   - 入口路径: 原来的种子里 '/index#/system/user-manage' 比较合理(Element UI 管理后台常见 hash 路由)

NEW_BASE_URL = "https://scrm.baiyinqifu.com"

NEW_OPTIONS = {
    # 登录相关
    "captcha_type": "math",
    "login_url_path": "/login",
    "submit_button_text": "登录",  # ⚠️ 之前可能是 '登 录'(中间空格), 这里修正!
    "username_placeholder": "请输入登录账号/手机号",
    "password_placeholder": "请输入密码",
    "captcha_input_placeholder": "请输入验证码",
    "captcha_img_selector": "img.show-code-img",
    # 数据入口路径
    "web_operator_entry_path": "/index#/system/user-manage",
    "web_session_entry_path": "/index#/monitor/index",
}

# 需要修的 SCRM data_types
SCRM_DTS = ["scrm_operator", "scrm_session", "scrm_session_transfer"]

db = SessionLocal()
try:
    for dt in SCRM_DTS:
        row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == dt))
        if not row:
            print(f"[{dt}] 未找到配置, 跳过")
            continue

        changed = False
        # 更新 base_url
        old_base = row.base_url
        if old_base != NEW_BASE_URL:
            row.base_url = NEW_BASE_URL
            print(f"[{dt}] base_url: {old_base!r} -> {NEW_BASE_URL!r}")
            changed = True

        # 合并 options (保留用户原来设置的 web_username/web_password, 其他覆盖)
        old_opts = row.options or {}
        # 备份敏感的 web_username / web_password (如果有的话)
        keep = {}
        for k in ("web_username", "web_password"):
            if k in old_opts:
                keep[k] = old_opts[k]

        merged_opts = {**old_opts, **NEW_OPTIONS, **keep}
        if json.dumps(old_opts, sort_keys=True) != json.dumps(merged_opts, sort_keys=True):
            print(f"[{dt}] options 已更新 (保留 web_username/web_password 如存在)")
            for k, v in NEW_OPTIONS.items():
                old_v = old_opts.get(k)
                if old_v != v:
                    print(f"       {k}: {old_v!r} -> {v!r}")
            row.options = merged_opts
            changed = True

        if changed:
            db.commit()
            print(f"[{dt}] ✅ 已保存")
        else:
            print(f"[{dt}] 无需修改,配置已正确")

finally:
    db.close()
print("\n全部配置修正完成!")
