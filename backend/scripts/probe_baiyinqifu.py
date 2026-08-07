#!/usr/bin/env python3
"""探测真实的 SCRM 管理后台 https://scrm.baiyinqifu.com/ 登录页结构。"""
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
logger = logging.getLogger("probe_baiyinqifu")

BASE_URL = "https://scrm.baiyinqifu.com"

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127.0 Safari/537.36",
        viewport={"width": 1680, "height": 960},
        locale="zh-CN",
    )
    context.set_default_timeout(20_000)

    try:
        page = context.new_page()

        # 访问根路径,看是否直接有登录页或者自动跳 /login
        for try_url in (BASE_URL + "/", BASE_URL + "/login"):
            logger.info(f"[1] 访问: {try_url}")
            try:
                page.goto(try_url, wait_until="domcontentloaded")
                break
            except Exception as e:
                logger.warning(f"  访问 {try_url} 失败: {e}, 试下一个")
        page.wait_for_timeout(5000)  # 给 SPA 足够时间
        p = DEBUG_DIR / f"{int(time.time())}_bai_login.png"
        page.screenshot(path=str(p), full_page=True)
        logger.info(f"  当前 URL: {page.url}")
        logger.info(f"  截图: {p.name}")

        # 保存 HTML
        html_p = DEBUG_DIR / "baiyinqifu_login.html"
        html_p.write_text(page.content(), encoding="utf-8")
        logger.info(f"  HTML 保存: {html_p}")

        # dump 所有 <input>
        logger.info("\n[2] ===== 所有 <input> 元素 =====")
        for i, inp in enumerate(page.query_selector_all("input")):
            info = page.evaluate("""(el) => ({
                type: el.type, name: el.name, id: el.id, placeholder: el.placeholder,
                className: el.className, visible: el.offsetParent !== null,
                html: (el.outerHTML||'').slice(0,200),
                rect: el.getBoundingClientRect ? {x: Math.round(el.getBoundingClientRect().x), y: Math.round(el.getBoundingClientRect().y), w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height)} : null
            })""", inp)
            logger.info(f"  [{i}] name={info['name']!r} type={info['type']!r} placeholder={info['placeholder']!r} visible={info['visible']}")
            logger.info(f"      class={info['className'][:60]!r} rect={info['rect']}")
            logger.info(f"      html={info['html']}")

        # dump 所有 <img> (找验证码图片)
        logger.info("\n[3] ===== 所有 <img> 元素 =====")
        imgs = page.query_selector_all("img")
        logger.info(f"  共 {len(imgs)} 个 <img>")
        for i, img in enumerate(imgs):
            info = page.evaluate("""(el) => ({
                src: (el.src||'').slice(0,300), id: el.id, className: el.className,
                alt: el.alt, title: el.title,
                w: el.naturalWidth||0, h: el.naturalHeight||0,
                visible: el.offsetParent !== null,
                html: (el.outerHTML||'').slice(0,200),
                rect: el.getBoundingClientRect ? {x: Math.round(el.getBoundingClientRect().x), y: Math.round(el.getBoundingClientRect().y), w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height)} : null
            })""", img)
            logger.info(f"  [{i}] {info['w']}x{info['h']} visible={info['visible']} rect={info['rect']}")
            logger.info(f"      src={info['src'][:150]!r}")
            logger.info(f"      class={info['className'][:60]!r} id={info['id']!r} alt={info['alt']!r}")
            logger.info(f"      html={info['html']}")
            # 如果看起来像验证码(尺寸 ~100x40 或有 code/captcha/vcode 关键字), 保存图片
            if (info["w"] > 50 and info["h"] > 20 and info["h"] < 80) or any(k in (info["className"]+info["src"]+info["id"]+info["className"]).lower() for k in ("captcha","code","vcode","yanzheng","verify")):
                try:
                    png = img.screenshot(type="png")
                    cap_p = DEBUG_DIR / f"{int(time.time())}_bai_captcha_candidate_{i}.png"
                    cap_p.write_bytes(png)
                    logger.info(f"      ✅ 疑似验证码图片,已保存: {cap_p.name} ({len(png)} bytes)")
                except Exception as ee:
                    logger.info(f"      保存失败: {ee}")

        # dump 所有 <button>
        logger.info("\n[4] ===== 所有 <button> 元素 =====")
        for i, btn in enumerate(page.query_selector_all("button")):
            info = page.evaluate("""(el) => ({
                text: (el.innerText||'').trim().slice(0,50), type: el.type, id: el.id,
                className: el.className, disabled: el.disabled, visible: el.offsetParent !== null
            })""", btn)
            logger.info(f"  [{i}] text={info['text']!r} type={info['type']!r} disabled={info['disabled']} visible={info['visible']}")
            logger.info(f"      class={info['className'][:60]!r} id={info['id']!r}")

        # dump 所有 <div> clickable + 可能是图片验证码的 canvas
        logger.info("\n[5] ===== 所有 <canvas> 元素(有些验证码用 canvas 绘制) =====")
        canvases = page.query_selector_all("canvas")
        logger.info(f"  共 {len(canvases)} 个 <canvas>")
        for i, c in enumerate(canvases):
            info = page.evaluate("""(el) => ({
                id: el.id, className: el.className,
                w: el.width||0, h: el.height||0,
                visible: el.offsetParent !== null,
                html: (el.outerHTML||'').slice(0,200)
            })""", c)
            logger.info(f"  [{i}] {info['w']}x{info['h']} visible={info['visible']} class={info['className'][:60]!r} id={info['id']!r}")
            logger.info(f"      html={info['html']}")
            # 如果 ~100x40, 截图保存
            if info["w"] > 50 and info["h"] > 20 and info["h"] < 100:
                try:
                    png = c.screenshot(type="png")
                    cap_p = DEBUG_DIR / f"{int(time.time())}_bai_canvas_captcha_{i}.png"
                    cap_p.write_bytes(png)
                    logger.info(f"      ✅ 疑似 canvas 验证码,已保存: {cap_p.name}")
                except Exception as ee:
                    logger.info(f"      保存失败: {ee}")

        # dump body 文本前 80 行
        logger.info("\n[6] ===== body 文本前 80 行非空 =====")
        try:
            txt = page.locator("body").inner_text()
            lns = [ln for ln in txt.splitlines() if ln.strip()][:80]
            for ln in lns:
                logger.info(f"  | {ln}")
            (DEBUG_DIR / "baiyinqifu_body.txt").write_text(txt, encoding="utf-8")
        except Exception as e:
            logger.warning(f"  读 body 失败: {e}")

    finally:
        browser.close()
    logger.info("\n探测完成!")
