#!/usr/bin/env python3
"""在登录时禁用重定向跟随,登录后用 https 协议手动访问 hash 路由,看是否有前端内容。"""
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
logger = logging.getLogger("test_no_redirect")

from app.core.database import SessionLocal
from app.services.sync_source_config_service import get_sync_source_config
from app.services.scrm_sync_service import _resolve_pw_credentials

SCRM_DT = "scrm_operator"
db = SessionLocal()
try:
    cfg = get_sync_source_config(db, SCRM_DT) or {}
finally:
    db.close()

pw_user, pw_pwd, pw_err = _resolve_pw_credentials(cfg)
if pw_err:
    logger.error(f"凭证错误: {pw_err}")
    sys.exit(2)

from playwright.sync_api import sync_playwright

# 常见的 hash 路由入口(从种子配置和 SCRM 常见模板推断)
HASH_ROUTES = [
    "/#/system/user-manage",
    "/#/system/operator",
    "/#/system/user",
    "/#/system/operator/index",
    "/#/system/users",
    "/index#/system/user-manage",
    "/index.html#/system/user-manage",
    "/admin#/system/user",
    "/#/monitor/index",
    "/#/monitor",
    "/#/kefu/operator",
    "/#/staff/list",
    "/#/customer/operator",
    "/#/cs/operator",
    "/#/workbench/index",
    "/#/workspace",
    "/#/dashboard",
    "/#/home",
]

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127.0 Safari/537.36",
        viewport={"width": 1680, "height": 960},
        locale="zh-CN",
    )
    context.set_default_timeout(15_000)

    try:
        page = context.new_page()

        # Step 1: 访问登录页
        login_url = "https://gateway.bilinl.com/login"
        logger.info(f"[1] 访问登录页: {login_url}")
        page.goto(login_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        p = DEBUG_DIR / f"{int(time.time())}_A_login_page.png"
        page.screenshot(path=str(p), full_page=True)
        logger.info(f"    当前 URL: {page.url} 截图: {p.name}")

        # Step 2: 填账号密码点登录
        logger.info("[2] 填账号密码并点登录按钮")
        page.locator("input[name='username']").fill(pw_user)
        page.locator("input[name='password']").fill(pw_pwd)
        page.wait_for_timeout(300)

        # 登录按钮点击,不等待跳转(我们自己监听 request/response)
        responses = []
        page.on("response", lambda resp: responses.append({
            "url": resp.url[:200],
            "status": resp.status,
            "content_type": (resp.headers.get("content-type") or "")[:100],
        }))

        submit_btn = page.get_by_role("button", name="登录").first
        submit_btn.click()
        # 等 8 秒,包括重定向
        page.wait_for_timeout(8000)

        logger.info(f"    点击登录后的最终 URL: {page.url}")
        p = DEBUG_DIR / f"{int(time.time())}_B_after_login.png"
        page.screenshot(path=str(p), full_page=True)
        logger.info(f"    截图: {p.name}")

        # 打印期间的响应
        logger.info("    登录期间的 HTTP 响应:")
        for r in responses[-15:]:
            logger.info(f"      {r['status']} {r['content_type'][:40]}  {r['url']}")

        # 打印现在的 cookie
        cookies = context.cookies()
        logger.info(f"\n[3] 现在浏览器里有 {len(cookies)} 个 cookie:")
        for c in cookies:
            logger.info(f"    {c.get('name')!r}={str(c.get('value'))[:30]!r} domain={c.get('domain')!r} path={c.get('path')!r} secure={c.get('secure')}")

        # Step 3: 关键测试! 用 https 协议手动访问常见 hash 路由(不随登录重定向走 http)
        logger.info("\n[4] 用当前 cookie 手动访问 https://gateway.bilinl.com + 常见 hash 路由(寻找前端 SPA 页面):")
        for route in HASH_ROUTES:
            url = "https://gateway.bilinl.com" + route
            logger.info(f"    尝试: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                content_type = ""
                try:
                    resp = page.request.get(url, timeout=5000)
                    content_type = (resp.headers.get("content-type") or "")[:80]
                except Exception:
                    pass
                body = page.locator("body").inner_text()[:200]
                n_inputs = page.locator("input").count()
                n_tables = page.locator("table,.el-table").count()
                is_json_like = body.strip().startswith("{") or "\"code\":" in body

                p = DEBUG_DIR / f"{int(time.time())}_C_{route.replace('/','_').replace('#','H')[:40]}.png"
                page.screenshot(path=str(p), full_page=True)

                msg = f"      inputs={n_inputs} tables={n_tables} is_json={is_json_like} ct={content_type}"
                msg += f"\n      body前150: {body[:150]!r}"
                msg += f"\n      截图: {p.name}"
                logger.info(msg)

                if not is_json_like and (n_tables > 0 or n_inputs > 2):
                    logger.info(f"      ✅ 这可能是真实的前端页面! 停在这里深挖...")
                    # dump body 前 60 行
                    try:
                        t = page.locator("body").inner_text()
                        lns = [ln for ln in t.splitlines() if ln.strip()][:60]
                        for ln in lns:
                            logger.info(f"        | {ln}")
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"      ❌ 失败: {str(e)[:150]}")
                continue

        # Step 4: 也试一下用当前 cookie 直接调 OpenAPI 的操作员接口
        logger.info("\n[5] 用登录后的 cookie 直接调可能的 OpenAPI 接口(获取操作员):")
        API_CANDIDATES = [
            "/api/system/user/list",
            "/api/v1/system/users",
            "/api/v1/operator/list",
            "/system/user/page",
            "/api/admin/users",
            "/api/auth/user/list",
            "/api/sys_user/page",
        ]
        for api_path in API_CANDIDATES:
            url = "https://gateway.bilinl.com" + api_path
            try:
                resp = page.request.get(url, timeout=8000)
                ct = (resp.headers.get("content-type") or "")[:80]
                try:
                    text = resp.text()[:500]
                except Exception:
                    text = "(read failed)"
                logger.info(f"    {resp.status} {ct} GET {api_path}")
                logger.info(f"      body[:500] = {text[:500]!r}")
                if resp.status == 200 and "json" in ct:
                    try:
                        data = json.loads(text)
                        code = data.get("code")
                        msg = data.get("message")
                        total = None
                        rows = None
                        if isinstance(data.get("data"), dict):
                            total = data["data"].get("total") or data["data"].get("count")
                            rows = data["data"].get("list") or data["data"].get("records") or data["data"].get("rows")
                        if code in (0, 200, "0", "200") and (total is not None or rows is not None):
                            logger.info(f"      ✅ 接口返回数据! code={code} msg={msg!r} total={total} rows_len={len(rows) if rows else None}")
                    except Exception:
                        pass
            except Exception as e:
                logger.info(f"    ERR GET {api_path}: {str(e)[:120]}")

    finally:
        browser.close()
