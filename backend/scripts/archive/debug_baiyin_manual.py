#!/usr/bin/env python3
"""Debug: 验证 input[name='username'] 等元素能否定位 + 尝试 ddddocr 文本验证码,打印每次 OCR 原文和结果."""
from __future__ import annotations

import json
import sys
import tempfile
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logging.getLogger("ddddocr").setLevel(logging.ERROR)
logging.getLogger("PIL").setLevel(logging.WARNING)

log = logging.getLogger("dbg_loc")
from app.core.crypto import decrypt_secret
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from app.services.captcha_session import try_ocr_captcha_with_confidence
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
from sqlalchemy import select

# ── 拿账号密码 ───────────────────────────────────────────────────
with SessionLocal() as db:
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "scrm_operator"))
    base_url = row.base_url.rstrip("/")
    uname = decrypt_secret(row.username)
    pwd = decrypt_secret(row.password)
    opts = row.options or {}
log.info(f"base_url={base_url}, user={uname[:3]}***")

# ── 运行 Playwright ──────────────────────────────────────────────
shot_dir = Path(tempfile.gettempdir())
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)  # 打开 Chrome 可见,方便看
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        log.info("GOTO /login ...")
        page.goto(base_url + "/login", wait_until="domcontentloaded")
        log.info("wait 3s for Vue render ...")
        page.wait_for_timeout(3000)
        cur = page.url
        log.info(f"当前 URL={cur}")
        # 测试定位
        for sel in [
            "input[name='username']",
            "input[name='password']",
            "input[placeholder*='账号']",
            "input[placeholder*='密码']",
            "input[placeholder*='验证码']",
            "img.show-code-img",
        ]:
            try:
                cnt = page.locator(sel).count()
                log.info(f"selector {sel}: count={cnt}")
            except Exception as e:
                log.warning(f"selector {sel}: err={e}")

        log.info("=== 尝试 10 次文本验证码登录 ===")
        for i in range(1, 11):
            log.info(f"\n--- 尝试 #{i} ---")
            # 先清空输入框 + 填账号密码
            try:
                page.locator("input[name='username']").fill("")
                page.locator("input[name='username']").fill(uname)
                page.locator("input[name='password']").fill("")
                page.locator("input[name='password']").fill(pwd)
            except Exception as e:
                log.warning(f"填账号密码异常: {e}")
                continue
            # 抓验证码图
            try:
                png_bytes = page.locator("img.show-code-img").first.screenshot(type="png")
            except Exception as e:
                log.warning(f"抓验证码图失败: {e}")
                break
            # OCR 文本 (直接输入原文,不是计算答案)
            r = try_ocr_captcha_with_confidence(png_bytes)
            log.info(f"  ddddocr 文本识别结果: {json.dumps(r, ensure_ascii=False) if r else None}")
            # 存验证码图
            img_path = shot_dir / f"baiyin_cap_{i}.png"
            img_path.write_bytes(png_bytes)
            log.info(f"  图片: {img_path}")
            code = r["code"] if r else ""
            if not code:
                log.warning("  识别结果为空, 刷新验证码重来")
                try:
                    page.locator("img.show-code-img").first.click()
                except Exception:
                    pass
                page.wait_for_timeout(1000)
                continue
            log.info(f"  本次填写验证码: '{code}' (len={len(code)})")
            # 填验证码
            try:
                cap_in = page.locator("input[placeholder*='验证码']").first
                cap_in.fill("")
                cap_in.fill(code)
                page.wait_for_timeout(400)
            except Exception as e:
                log.warning(f"填验证码失败: {e}")
            # 点登录按钮
            try:
                btn = page.get_by_role("button", name="登录").first
                log.info(f"  登录按钮 count={btn.count()}")
                btn.click()
            except Exception as e:
                log.warning(f"点登录按钮失败: {e}")
                try:
                    page.locator("button").last.click()
                except Exception as e2:
                    log.warning(f"  兜底点 button.last 也失败: {e2}")
            # 等跳转
            page.wait_for_timeout(3000)
            new_url = page.url
            log.info(f"  点击后 URL={new_url}")
            # 查错误提示
            err_msgs = []
            for role_name in ["alert", "错误", "验证码错误", "账号或密码"]:
                try:
                    if role_name == "alert":
                        els = page.get_by_role(role_name).all_text_contents()
                    else:
                        els = [el for el in page.get_by_text(role_name).all_text_contents() if el.strip()]
                    if els:
                        err_msgs.extend(els)
                except Exception:
                    pass
            # 截图
            try:
                p = shot_dir / f"baiyin_after_submit_{i}.png"
                page.screenshot(path=str(p))
                log.info(f"  提交后截图: {p}")
            except Exception:
                pass
            if err_msgs:
                log.warning(f"  错误提示: {err_msgs}")
            # 判断是否登录成功
            if "/login" not in new_url.split("?")[0].split("#")[0]:
                log.info(f"  ✅ 登录成功! 跳转后 URL={new_url}")
                try:
                    page.screenshot(path=str(shot_dir / "baiyin_logged_in.png"), full_page=True)
                    log.info(f"  登录成功截图已保存!")
                except Exception:
                    pass
                break
            else:
                log.info("  仍然在登录页, 点击验证码图片刷新继续下一轮")
                try:
                    page.locator("img.show-code-img").first.click()
                    page.wait_for_timeout(1000)
                except Exception:
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
        else:
            log.warning("  10 次用完仍未登录")
    finally:
        log.info("Done, 浏览器 5 秒后关闭(headless=False 可以看一下)")
        page.wait_for_timeout(5000)
        ctx.close()
        browser.close()
