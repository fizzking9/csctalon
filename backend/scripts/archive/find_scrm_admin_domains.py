#!/usr/bin/env python3
"""尝试常见的 SCRM 管理后台域名(因为 gateway.bilinl.com 是纯 API 网关,没有网页)。"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

DEBUG_DIR = ROOT / "uploads" / "_debug_scrm_login"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname).3s] %(name)s: %(message)s")
logger = logging.getLogger("find_domains")

# 已知 base_url (OpenAPI 网关) 是 https://gateway.bilinl.com
# 尝试常见的管理后台域名:
CANDIDATE_URLS = [
    # bilinl 相关常见管理后台
    ("admin.bilinl.com", "/login"),
    ("scrm.bilinl.com", "/login"),
    ("console.bilinl.com", "/login"),
    ("manage.bilinl.com", "/login"),
    ("crm.bilinl.com", "/login"),
    ("boss.bilinl.com", "/login"),
    ("mgr.bilinl.com", "/login"),
    ("admin.bllln.com", "/login"),   # 注意之前种子里好像有过 bllln.com(拼写不同)
    ("scrm.bllln.com", "/login"),
    ("gateway.bllln.com", "/login"),
    ("console.bllln.com", "/login"),
    ("manage.bllln.com", "/login"),
    # 带端口的常见情况
    ("gateway.bilinl.com", "/admin/login"),
    ("gateway.bilinl.com", "/scrm/login"),
    ("gateway.bilinl.com", "/manage/login"),
    ("gateway.bilinl.com", "/index.html#/login"),
    ("gateway.bilinl.com", "/login.html"),
]

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127.0 Safari/537.36",
        viewport={"width": 1680, "height": 960},
        locale="zh-CN",
    )
    context.set_default_timeout(10_000)
    page = context.new_page()

    valid_pages = []
    for i, (host, path) in enumerate(CANDIDATE_URLS):
        # 如果看起来是完整域名就加 https,否则组合 gateway
        if "://" in host:
            url = host.rstrip("/") + path
        else:
            url = f"https://{host.rstrip('/')}{path}"
        try:
            logger.info(f"[{i+1}/{len(CANDIDATE_URLS)}] 尝试: {url}")
            resp = page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            # 记录状态
            status = resp.status if resp else 0
            body_text = ""
            try:
                body_text = page.locator("body").inner_text()[:200]
            except Exception:
                pass

            n_inputs = page.locator("input").count()
            n_buttons = page.locator("button").count()
            is_html = "<html" in (page.content()[:500].lower() or "")
            has_login_form = ("登录" in body_text or "login" in page.url.lower()) and n_inputs >= 2

            p = DEBUG_DIR / f"{int(time.time())}_try_{i+1}.png"
            page.screenshot(path=str(p), full_page=True)

            summary = f"    status={status} inputs={n_inputs} buttons={n_buttons} is_html={is_html} has_login_form={has_login_form}"
            summary += f"\n    body前100: {body_text[:100]!r}"
            summary += f"\n    截图: {p.name}"
            logger.info(summary)

            if status == 200 and is_html and has_login_form:
                logger.info(f"    ✅ 找到疑似登录页: {url}")
                valid_pages.append((url, status, n_inputs, body_text[:200]))
        except Exception as e:
            logger.warning(f"    ❌ 失败: {str(e)[:150]}")
            continue

    browser.close()

logger.info("\n" + "=" * 60)
logger.info("【总结: 找到的疑似登录页】")
if valid_pages:
    for u, s, n, t in valid_pages:
        logger.info(f"  URL: {u}")
        logger.info(f"    status={s} inputs={n} body={t!r}")
else:
    logger.info("  没有找到标准登录页,可能需要提供真实的 SCRM 管理后台域名。")
    logger.info("  注意: gateway.bilinl.com 是 OpenAPI 网关(纯 JSON),不是管理后台网页。")
    logger.info("  请手动在浏览器里打开你实际使用的 SCRM 后台登录页, 然后把域名给我们。")
logger.info("=" * 60)
