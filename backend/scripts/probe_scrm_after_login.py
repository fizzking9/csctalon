#!/usr/bin/env python3
"""SCRM 登录成功后的首页结构探测,找出真实的操作员入口。"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

DEBUG_DIR = ROOT / "uploads" / "_debug_scrm_login"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname).3s] %(name)s: %(message)s")
logger = logging.getLogger("probe2")

from app.core.database import SessionLocal
from app.services.sync_source_config_service import get_sync_source_config
from app.services.scrm_sync_service import _resolve_pw_credentials
from app.services.scrm_web_fetcher import ScrmWebFetcher

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

fetcher = ScrmWebFetcher(
    base_url=cfg.get("base_url") or "",
    username=pw_user,
    password=pw_pwd,
    options=cfg.get("options") or {},
    logger=logger,
    captcha_max_retries=2,
)

try:
    fetcher._ensure_playwright()
    page = fetcher._page
    assert page is not None

    # Step 1: 登录
    login_url = fetcher._full_url(fetcher._selector("login_url_path"))
    logger.info(f"[1] 打开登录页: {login_url}")
    page.goto(login_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    # 先强制 captcha_type 不生效(这个 SCRM 实际没有验证码)
    captcha_type = fetcher.options.get("captcha_type")
    logger.info(f"    配置 captcha_type={captcha_type!r}, 但我们观察到实际页面没有验证码,所以直接手动填账号密码点登录")

    # 手动填写登录(直接用 name 属性,最快)
    page.locator("input[name='username']").fill(pw_user)
    page.locator("input[name='password']").fill(pw_pwd)
    page.wait_for_timeout(300)

    # 点登录
    page.get_by_role("button", name="登录").first.click()
    page.wait_for_timeout(5000)  # 多等一下,看完整跳转

    step_p = DEBUG_DIR / f"{int(time.time())}_post_login_full.png"
    page.screenshot(path=str(step_p), full_page=True)
    logger.info(f"    [截图] 登录后首页: {step_p.name}")

    logger.info(f"    登录后 URL = {page.url}")
    logger.info(f"    _am_i_logged_in = {fetcher._am_i_logged_in()}")

    # ⚠️ 关键: 现在用 page.url 里的实际协议和 host 构造后续访问(不要硬编码 https)
    from urllib.parse import urlparse
    parsed = urlparse(page.url)
    real_base = f"{parsed.scheme}://{parsed.netloc}"
    logger.info(f"    ⚠️  实际基础地址(跟随登录跳转协议): {real_base}")

    # Step 2: dump body 全部文本(看看首页内容是什么)
    logger.info("\n[2] ===== 登录后首页 body 文本前 100 行非空 =====")
    try:
        txt = page.locator("body").inner_text()
        lines = [ln for ln in txt.splitlines() if ln.strip()][:100]
        for ln in lines:
            logger.info(f"    | {ln}")
        (DEBUG_DIR / "post_login_body_text.txt").write_text(txt, encoding="utf-8")
    except Exception as e:
        logger.warning(f"读 body 失败: {e}")

    # Step 3: 所有 <a> 链接和菜单类 div,找用户管理/操作员/客服相关的
    logger.info("\n[3] ===== 所有链接 <a> (href 或文本非空) =====")
    try:
        anchors = page.query_selector_all("a")
        logger.info(f"    共 {len(anchors)} 个 <a>")
        for i, a in enumerate(anchors):
            info = page.evaluate("""(el) => ({
                href: (el.href||'').slice(0,200),
                text: (el.innerText||'').trim().slice(0,60),
                visible: el.offsetParent !== null
            })""", a)
            if info["text"] or info["href"]:
                # 过滤掉明显是 js void / # 的空链接
                is_empty_link = (not info["text"]) and (info["href"] in ("javascript:void(0)", "#", ""))
                if not is_empty_link:
                    logger.info(f"    [{i}] visible={info['visible']} text={info['text']!r} href={info['href']!r}")
    except Exception as e:
        logger.warning(f"读 <a> 失败: {e}")

    # Step 4: 查找有"用户""操作""客服""人员""账号""管理"字样的可点击元素
    for kw in ("用户管理", "操作员", "客服", "系统管理", "人员管理", "账号管理", "用户列表", "员工"):
        try:
            c = page.get_by_text(kw).count()
            if c > 0:
                logger.info(f"\n[4] 关键词 {kw!r} 命中 {c} 处,尝试点一下第一个可见的...")
                loc = page.get_by_text(kw).first
                try:
                    loc.scroll_into_view_if_needed(timeout=2000)
                    loc.click(timeout=3000)
                    page.wait_for_timeout(3000)
                    p = DEBUG_DIR / f"{int(time.time())}_clicked_{kw}.png"
                    page.screenshot(path=str(p), full_page=True)
                    logger.info(f"    点击 {kw!r} 后截图: {p.name}, URL={page.url}")
                    # dump 点击后 body 前 30 行
                    try:
                        t = page.locator("body").inner_text()
                        lns = [ln for ln in t.splitlines() if ln.strip()][:30]
                        for l in lns:
                            logger.info(f"      | {l}")
                    except Exception:
                        pass
                    # 点完返回(用浏览器 back)
                    page.go_back(wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                except Exception as ee:
                    logger.info(f"    点击 {kw!r} 失败: {ee}")
        except Exception:
            pass

    # Step 5: 保存 HTML
    try:
        html_p = DEBUG_DIR / "post_login_page.html"
        html_p.write_text(page.content(), encoding="utf-8")
        logger.info(f"\n[5] 登录后首页完整 HTML 已保存: {html_p}")
    except Exception:
        pass

finally:
    fetcher.close()
