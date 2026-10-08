#!/usr/bin/env python3
"""离线测试:用保存的 3 张验证码图片测试 try_ocr_math_captcha 识别率."""
from __future__ import annotations

import json
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logging.getLogger("ddddocr").setLevel(logging.ERROR)
logging.getLogger("PIL").setLevel(logging.WARNING)

from app.services.captcha_session import try_ocr_math_captcha_with_confidence

import tempfile
TD = Path(tempfile.gettempdir())
files = [
    (TD / "baiyin_cap_1.png", "4+5=?", 9),
    (TD / "baiyin_cap_2.png", "7×7=?", 49),
    (TD / "baiyin_cap_3.png", "9×3=?", 27),
]

for f, human, expected in files:
    if not f.exists():
        print(f"[SKIP] {f.name} 不存在")
        continue
    raw = f.read_bytes()
    r = try_ocr_math_captcha_with_confidence(raw)
    if r is None:
        print(f"[FAIL] {f.name} 真实={human!r} 期望答案={expected} → OCR 返回 None")
        continue
    ok = (r.get("computed_value") == expected) or (r.get("answer") == str(expected))
    print(f"[{'OK  ' if ok else 'FAIL'}] {f.name} 真实={human!r} 期望答案={expected} → "
          f"ocr_code={r['ocr_code']!r} expr={r['normalized_expr']!r} "
          f"answer={r['answer']!r} computed={r['computed_value']} "
          f"beta={r['used_beta']} min_p={r['min_prob']} avg_p={r['avg_prob']} "
          f"confident={r['confident']} chars={r['chars']}")
