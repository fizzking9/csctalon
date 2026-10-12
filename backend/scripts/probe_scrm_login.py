#!/usr/bin/env python3
"""探测 SCRM 登录页实际结构,找出正确的 input/img/button 选择器。"""
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
logger = logging.getLogger("probe")

from app.services.scrm_web_fetcher import ScrmWebFetcher
from app.core.database import SessionLocal
from app.services.sync_source_config_service import get_sync_source_config

# 1. 拿配置
SCRM_DT = "scrm_operator"
db = SessionLocal()
try:
    cfg = get_sync_source_config(db, SCRM_DT) or {}
finally:
    db.close()

base_url = cfg.get("base_url") or "https://gateway.bilinl.com"
options = cfg.get("options") or {}

logger.info(f"base_url = {base_url}")
logger.info(f"options selectors = {json.dumps({k:v for k,v in options.items() if any(s in k for s in ('selector','placeholder','text','path'))}, ensure_ascii=False, indent=2)}")

# 2. 起 Playwright 探测结构
fetcher = ScrmWebFetcher(
    base_url=base_url,
    username="probe",
    password="probe",
    options=options,
    logger=logger,
    captcha_max_retries=1,
)

try:
    fetcher._ensure_playwright()
    assert fetcher._page is not None
    page = fetcher._page

    login_url = fetcher._full_url(fetcher._selector("login_url_path"))
    logger.info(f"\n=== 打开登录页: {login_url} ===")
    page.goto(login_url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)  # 多等一下,SPA 可能懒加载

    p = DEBUG_DIR / f"{int(time.time())}_probe_login.png"
    page.screenshot(path=str(p), full_page=True)
    logger.info(f"全屏截图: {p.name}")

    # ── dump 所有 input 元素 ──
    logger.info("\n" + "=" * 60)
    logger.info("【所有 <input> 元素详细信息:】")
    logger.info("=" * 60)
    inputs = page.query_selector_all("input")
    logger.info(f"  共找到 {len(inputs)} 个 <input>:")
    for i, inp in enumerate(inputs):
        info = page.evaluate("""(el) => ({
            type: el.type || '',
            name: el.name || '',
            id: el.id || '',
            placeholder: el.placeholder || '',
            className: el.className || '',
            value: (el.value || '').slice(0, 20),
            outerHTML: (el.outerHTML || '').slice(0, 300),
            visible: el.offsetParent !== null,
            rect: el.getBoundingClientRect ? {x: Math.round(el.getBoundingClientRect().x), y: Math.round(el.getBoundingClientRect().y), w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height)} : null
        })""", inp)
        logger.info(f"  [{i}] type={info['type']!r} name={info['name']!r} id={info['id']!r} placeholder={info['placeholder']!r}")
        logger.info(f"      class={info['className'][:80]!r} visible={info['visible']} rect={info['rect']}")
        if info["outerHTML"]:
            logger.info(f"      html={info['outerHTML']}")

    # ── dump 所有 img 元素 ──
    logger.info("\n" + "=" * 60)
    logger.info("【所有 <img> 元素(找验证码图片):】")
    logger.info("=" * 60)
    imgs = page.query_selector_all("img")
    logger.info(f"  共找到 {len(imgs)} 个 <img>:")
    for i, img in enumerate(imgs):
        info = page.evaluate("""(el) => ({
            src: (el.src || '').slice(0, 200),
            id: el.id || '',
            className: el.className || '',
            alt: el.alt || '',
            title: el.title || '',
            width: el.naturalWidth || 0,
            height: el.naturalHeight || 0,
            visible: el.offsetParent !== null,
            rect: el.getBoundingClientRect ? {x: Math.round(el.getBoundingClientRect().x), y: Math.round(el.getBoundingClientRect().y), w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height)} : null,
            outerHTML: (el.outerHTML || '').slice(0, 300)
        })""", img)
        logger.info(f"  [{i}] size={info['width']}x{info['height']} visible={info['visible']} rect={info['rect']}")
        logger.info(f"      src={info['src'][:120]!r}")
        logger.info(f"      class={info['className'][:80]!r} id={info['id']!r} alt={info['alt']!r}")
        if info["outerHTML"]:
            logger.info(f"      html={info['outerHTML']}")

    # ── dump 所有 button 元素 ──
    logger.info("\n" + "=" * 60)
    logger.info("【所有 <button> 元素(找登录按钮):】")
    logger.info("=" * 60)
    btns = page.query_selector_all("button")
    logger.info(f"  共找到 {len(btns)} 个 <button>:")
    for i, btn in enumerate(btns):
        info = page.evaluate("""(el) => ({
            text: (el.innerText || '').trim().slice(0, 50),
            type: el.type || '',
            id: el.id || '',
            className: el.className || '',
            visible: el.offsetParent !== null,
            disabled: el.disabled,
            rect: el.getBoundingClientRect ? {x: Math.round(el.getBoundingClientRect().x), y: Math.round(el.getBoundingClientRect().y), w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height)} : null
        })""", btn)
        logger.info(f"  [{i}] text={info['text']!r} type={info['type']!r} disabled={info['disabled']} visible={info['visible']}")
        logger.info(f"      class={info['className'][:80]!r} rect={info['rect']}")

    # ── dump body 可见文本前 80 行 ──
    logger.info("\n" + "=" * 60)
    logger.info("【body 可见文本前 80 行非空:】")
    logger.info("=" * 60)
    try:
        txt = page.locator("body").inner_text()
        lines = [ln for ln in txt.splitlines() if ln.strip()][:80]
        for ln in lines:
            logger.info(f"  | {ln}")
    except Exception as e:
        logger.warning(f"读 body 失败: {e}")

    # 保存 HTML
    html_path = DEBUG_DIR / f"{int(time.time())}_probe_page.html"
    html_path.write_text(page.content(), encoding="utf-8")
    logger.info(f"\n完整 HTML 已保存: {html_path}")

finally:
    fetcher.close()
