#!/usr/bin/env python3
"""测试 SCRM 白音管理后台: 直接文本验证码(非计算式)登录 + 操作员页面数据解析

使用环境: .venv_test (需要 playwright + ddddocr + SQLAlchemy + cryptography 等)
"""
from __future__ import annotations

import json
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 日志 ─────────────────────────────────────────────────────────────
log = logging.getLogger("scrm_text_cap")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
# 屏蔽 ddddocr 嘈杂日志
logging.getLogger("ddddocr").setLevel(logging.ERROR)
logging.getLogger("PIL.PngImagePlugin").setLevel(logging.WARNING)

# ── 1. 读取 DB 配置 (解密) ──────────────────────────────────────────
from app.core.crypto import decrypt_secret
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from app.services.sync_source_config_service import _decrypt_options_secrets
from sqlalchemy import select

data_type = "scrm_operator"
with SessionLocal() as db:
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
    assert row, f"未找到 {data_type} 配置"
    base_url = row.base_url.rstrip("/")
    opts = row.options or {}
    # 解密 options (web_username/web_password 如果有的话)
    opts = _decrypt_options_secrets(opts)
    # 用户名密码优先: options.web_username/web_password → 列 username/password
    username = opts.get("web_username") or (decrypt_secret(row.username) if row.username else "") or ""
    password = opts.get("web_password") or (decrypt_secret(row.password) if row.password else "") or ""
    captcha_type = opts.get("captcha_type", "math")
    log.info(f"base_url = {base_url}")
    log.info(f"captcha_type = {captcha_type}")
    log.info(f"账号来源 = {'options.web_username' if opts.get('web_username') else '列.username'}")
    log.info(f"账号 = {username[:3]}{'*'*max(0, len(username)-3)}")
    assert username and password, "必须配置用户名/密码(列 或 options.web_*)"

# ── 2. 直接用 ScrmWebFetcher.login() ────────────────────────────────
from app.services.scrm_web_fetcher import ScrmWebFetcher

fetcher = ScrmWebFetcher(
    base_url=base_url,
    username=username,
    password=password,
    options=opts,
    logger=log,
    captcha_max_retries=15,  # 多试几次验证码 (ddddocr 识别不是 100% 准)
)
try:
    log.info("开始登录 ...")
    fetcher.login()
    log.info("✅ 登录成功! URL = %s", fetcher._page.url)
    # 保存登录后截图
    shot_dir = Path("/tmp")
    p = shot_dir / "scrm_after_login.png"
    fetcher._page.screenshot(path=str(p), full_page=True)
    log.info("登录后截图已保存: %s", p)

    # ── 3. 抓操作员数据 ────────────────────────────────────────────
    log.info("开始抓操作员 (operators) ...")
    operators = fetcher.fetch_operators()
    log.info("共抓到 %d 个操作员", len(operators))
    if operators:
        sample_path = shot_dir / "scrm_operators_sample.json"
        with open(sample_path, "w", encoding="utf-8") as f:
            json.dump(operators[:5], f, ensure_ascii=False, indent=2, default=str)
        log.info("前 5 条样例已存: %s", sample_path)
        log.info("第 1 条 operator 样例 keys: %s", list(operators[0].keys()))

    # ── 4. 再抓会话 (可选, 快速试一下) ─────────────────────────────
    from datetime import date, timedelta
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=1)
    log.info("开始抓会话 sessions (%s ~ %s) ...", start_dt, end_dt)
    try:
        sessions = fetcher.fetch_sessions(start_dt, end_dt)
        log.info("共抓到 %d 条会话", len(sessions))
        if sessions:
            s_path = shot_dir / "scrm_sessions_sample.json"
            with open(s_path, "w", encoding="utf-8") as f:
                json.dump(sessions[:3], f, ensure_ascii=False, indent=2, default=str)
            log.info("前 3 条会话样例已存: %s", s_path)
    except Exception as e:
        log.warning("抓会话失败 (非致命): %r", e)

    log.info("✅ 全部完成!")
finally:
    fetcher.close()
