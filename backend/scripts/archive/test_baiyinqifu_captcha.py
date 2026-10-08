#!/usr/bin/env python3
"""OCR 识别 scrm.baiyinqifu.com 的验证码图片,确认验证码类型(数学/纯文本)。"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

DEBUG_DIR = Path(__file__).resolve().parent.parent / "uploads" / "_debug_scrm_login"
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname).3s] %(name)s: %(message)s")
logger = logging.getLogger("ocr_test")

# 找到最新的一张验证码候选图片
cap_files = sorted(DEBUG_DIR.glob("*bai_captcha_candidate*.png"))
if not cap_files:
    logger.error(f"在 {DEBUG_DIR} 里没找到 baiyinqifu 验证码图片")
    sys.exit(1)

img_path = cap_files[-1]
logger.info(f"识别验证码图片: {img_path} ({img_path.stat().st_size} bytes)")
png_bytes = img_path.read_bytes()

# 同时加载 ddddocr 的两个模型(beta=False/True)识别,看识别结果是否一致
try:
    import ddddocr
except Exception as e:
    logger.error(f"ddddocr 加载失败: {e}")
    sys.exit(2)

for beta in (True, False):
    logger.info(f"\n===== ddddocr beta={beta} (纯英数) =====")
    ocr = ddddocr.DdddOcr(show_ad=False, beta=beta)
    raw = ocr.classification(png_bytes)
    logger.info(f"  纯识别结果 raw = {raw!r}")

    # 也试一下带概率的,看看置信度
    try:
        prob_raw = ocr.classification(png_bytes, probability=True)
        logger.info(f"  probability=True 返回类型: {type(prob_raw).__name__}")
        if isinstance(prob_raw, dict):
            logger.info(f"  keys = {list(prob_raw.keys())}")
            if "text" in prob_raw:
                logger.info(f"  text = {prob_raw.get('text')!r}")
            if "probabilities" in prob_raw:
                probs = prob_raw["probabilities"]
                # 打印 shape 或前几个值
                try:
                    import numpy as np
                    if isinstance(probs, np.ndarray):
                        logger.info(f"  probabilities shape = {probs.shape}")
                except Exception:
                    logger.info(f"  probabilities type = {type(probs).__name__}, len={len(probs) if hasattr(probs,'__len__') else '?'}")
        elif isinstance(prob_raw, list):
            logger.info(f"  list 长度 = {len(prob_raw)}")
            for item in prob_raw[:8]:
                logger.info(f"    item = {item}")
        else:
            logger.info(f"  value = {prob_raw!r}")
    except Exception as e:
        logger.warning(f"  带 probability 识别失败(ddddocr 版本可能不支持): {e}")

logger.info("\n===== 推断验证码类型 =====")
# 用 try_ocr_math_captcha 尝试一下,看能不能算出数学答案
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.services.captcha_session import try_ocr_math_captcha, try_ocr_math_captcha_with_confidence

ans1 = try_ocr_math_captcha(png_bytes)
logger.info(f"try_ocr_math_captcha 返回答案 = {ans1!r}")

try:
    info2 = try_ocr_math_captcha_with_confidence(png_bytes)
    if info2 is None:
        logger.info("try_ocr_math_captcha_with_confidence = None (OCR 或计算失败)")
    else:
        logger.info(f"try_ocr_math_captcha_with_confidence:")
        for k, v in info2.items():
            logger.info(f"  {k} = {v!r}")
except Exception as e:
    logger.exception(f"try_ocr_math_captcha_with_confidence 异常: {e}")
