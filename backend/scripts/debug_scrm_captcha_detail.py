#!/usr/bin/env python3
"""详细调试 SCRM 登录的验证码识别过程: 保存每一次验证码+识别结果,对比。"""
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

DEBUG_DIR = ROOT / "uploads" / "_debug_scrm_login" / "captcha_debug"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

log_fmt = "%(asctime)s [%(levelname).3s] %(name)s: %(message)s"
logging.basicConfig(level=logging.DEBUG, format=log_fmt, datefmt="%H:%M:%S")
logger = logging.getLogger("capdbg")

from app.core.database import SessionLocal
from app.services.sync_source_config_service import get_sync_source_config
from app.services.scrm_sync_service import _resolve_pw_credentials
from app.services.captcha_session import try_ocr_math_captcha_with_confidence

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

logger.info(f"账号: {pw_user[:2]}********")

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127.0 Safari/537.36",
        viewport={"width": 1680, "height": 960},
        locale="zh-CN",
    )
    context.set_default_timeout(20_000)
    page = context.new_page()

    try:
        # 打开登录页
        login_url = "https://scrm.baiyinqifu.com/login"
        logger.info(f"打开登录页: {login_url}")
        page.goto(login_url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # 填账号密码
        page.locator("input[name='username']").fill(pw_user)
        page.locator("input[name='password']").fill(pw_pwd)
        page.wait_for_timeout(300)
        logger.info("账号密码已填好")

        results = []

        for attempt in range(1, 8):  # 试 7 次,收集足够多的样本
            logger.info(f"\n===== 尝试 #{attempt} =====")

            # 1. 截验证码图
            cap_input = page.locator("input[placeholder*='请输入验证码']").first
            cap_img = page.locator("img.show-code-img").first
            cap_input.wait_for(state="visible")
            cap_img.wait_for(state="visible")
            page.wait_for_timeout(700)

            png_bytes = cap_img.screenshot(type="png")

            ts = int(time.time())
            img_path = DEBUG_DIR / f"{ts}_attempt{attempt}_raw.png"
            img_path.write_bytes(png_bytes)
            logger.info(f"  验证码图片: {img_path.name} ({len(png_bytes)} bytes)")

            # 2. ddddocr 直接识别(beta=True 和 False 都试一下,把结果都记下来)
            import ddddocr
            raw_results = {}
            for beta in (True, False):
                ocr = ddddocr.DdddOcr(show_ad=False, beta=beta)
                r_plain = ocr.classification(png_bytes)
                try:
                    r_prob = ocr.classification(png_bytes, probability=True)
                    if isinstance(r_prob, dict):
                        prob_text = r_prob.get("text")
                        conf = r_prob.get("confidence")
                        chars = None
                        probs_list = None
                        charset = None
                        try:
                            import numpy as np
                            charset = r_prob.get("charset")
                            if isinstance(charset, np.ndarray):
                                charset = charset.tolist()
                            prob_arr = r_prob.get("probabilities")
                            if isinstance(prob_arr, np.ndarray):
                                prob_arr = prob_arr.tolist()
                                # CTC 输出: 提取每步最大概率字符
                                chars = []
                                probs_list = []
                                for step_vec in prob_arr:
                                    if isinstance(step_vec, list) and len(step_vec) > 0:
                                        vec = step_vec[0] if isinstance(step_vec[0], list) else step_vec
                                        ci = max(range(len(vec)), key=lambda i: vec[i])
                                        c = (charset or [''])[ci] if charset else ''
                                        if c:
                                            chars.append(c)
                                            probs_list.append(round(vec[ci], 4))
                        except Exception:
                            pass
                        raw_results[f"beta={beta}"] = {
                            "plain": r_plain,
                            "text": prob_text,
                            "confidence": conf,
                            "chars": chars,
                            "per_char_prob": probs_list,
                        }
                    else:
                        raw_results[f"beta={beta}"] = {"plain": r_plain, "prob_type": type(r_prob).__name__}
                except Exception as e:
                    raw_results[f"beta={beta}"] = {"plain": r_plain, "prob_err": str(e)}

            logger.info(f"  ddddocr 原始识别结果: {json.dumps(raw_results, ensure_ascii=False, indent=2)}")

            # 3. 用我们封装的数学验证码识别
            info = try_ocr_math_captcha_with_confidence(png_bytes)
            math_result = None
            if info is None:
                logger.info("  封装函数 try_ocr_math_captcha_with_confidence = None")
            else:
                math_result = {k: v for k, v in info.items()}
                logger.info(f"  封装函数结果: ocr_code={info.get('ocr_code')!r} normalized_expr={info.get('normalized_expr')!r} answer={info.get('answer')!r}")
                logger.info(f"                 min_prob={info.get('min_prob')} avg_prob={info.get('avg_prob')} chars={info.get('chars')} confident={info.get('confident')} used_beta={info.get('used_beta')}")

            # 记录本次结果
            results.append({
                "attempt": attempt,
                "img": img_path.name,
                "ddddocr": raw_results,
                "math_captcha": math_result,
            })

            # 4. 填封装函数的答案去登录,看返回什么
            answer = math_result["answer"] if math_result else ""
            if not answer:
                # 如果封装函数返回 None, 用 beta=True 的纯识别结果
                answer = raw_results.get("beta=True", {}).get("plain", "")

            cap_input.fill("")
            cap_input.fill(str(answer))
            logger.info(f"  本次输入的验证码答案: {answer!r}")
            page.wait_for_timeout(400)

            # 截图 (填完验证码)
            p_fill = DEBUG_DIR / f"{ts}_attempt{attempt}_after_fill.png"
            page.screenshot(path=str(p_fill), full_page=True)

            # 点登录
            page.get_by_role("button", name="登录").nth(0).click()
            page.wait_for_timeout(4000)

            after_url = page.url
            logger.info(f"  点击登录后 URL = {after_url}")

            p_after = DEBUG_DIR / f"{ts}_attempt{attempt}_after_submit.png"
            page.screenshot(path=str(p_after), full_page=True)

            # 读错误提示
            err_msgs = []
            try:
                msgs = page.evaluate(
                    "() => Array.from(document.querySelectorAll('.el-message, .el-message-box__message, .el-form-item__error, [class*=message], [class*=error], .el-alert__content, .toast'))"
                    "  .map(n => n.innerText || '').filter(Boolean).slice(0,5)"
                )
                err_msgs = [m.strip() for m in msgs if m.strip()]
                if err_msgs:
                    logger.info(f"  页面错误提示: {err_msgs}")
            except Exception as e:
                logger.info(f"  读错误提示失败: {e}")

            results[-1]["url_after"] = after_url
            results[-1]["error_messages"] = err_msgs

            is_success = "/login" not in after_url.split("?")[0]
            if is_success:
                logger.info(f"  ✅ 登录成功！")
                break
            else:
                logger.info(f"  ❌ 仍然在登录页,刷新验证码下一轮")
                try:
                    cap_img.click()  # 点验证码刷新
                except Exception:
                    page.reload(wait_until="domcontentloaded")
                    page.locator("input[name='username']").wait_for(state="visible")
                    page.locator("input[name='username']").fill(pw_user)
                    page.locator("input[name='password']").fill(pw_pwd)
                page.wait_for_timeout(900)

        # 保存所有结果
        (DEBUG_DIR / "captcha_all_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        logger.info(f"\n全部样本已保存到 {DEBUG_DIR / 'captcha_all_results.json'}, 共 {len(results)} 次尝试")

    finally:
        browser.close()
