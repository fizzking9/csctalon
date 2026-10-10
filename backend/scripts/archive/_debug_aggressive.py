#!/usr/bin/env python3
"""Debug: print all candidate results from _aggressive_try_math_compute for each OCR string."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 暴力调试: 把 _aggressive_try_math_compute 改成打印所有候选项
from app.services.captcha_session import (
    _MATH_SYMBOL_FALLBACK_VARIANTS,
    _apply_symbol_map,
    _MATH_HEAD_CLEAN_RE,
    _MATH_TAIL_STRIP_RE,
    _MATH_EXPR_VALID_RE,
    _safe_eval_math_expr,
)

def debug_aggressive(raw_text, expected_ans, caption):
    print(f"\n===== [{caption}] raw={raw_text!r} 期望答案={expected_ans} =====")
    candidates = []
    for vi, overrides in enumerate(_MATH_SYMBOL_FALLBACK_VARIANTS):
        mapped = _apply_symbol_map(raw_text, overrides)
        for tail_strip in range(0, min(6, max(0, len(mapped) - 2))):
            cur = mapped[:-tail_strip] if tail_strip > 0 else mapped
            if len(cur) < 3:
                continue
            cleaned = _MATH_HEAD_CLEAN_RE.sub("", cur)
            cleaned = _MATH_TAIL_STRIP_RE.sub("", cleaned)
            while cleaned and cleaned[-1] in ("=", "?"):
                cleaned = cleaned[:-1]
            while cleaned and cleaned[-1] not in "0123456789)":
                cleaned = cleaned[:-1]
            if len(cleaned) < 3:
                continue
            if not _MATH_EXPR_VALID_RE.match(cleaned):
                continue
            if not any(op in cleaned for op in ("+", "-", "*", "/")):
                continue
            # 括号配对
            depth = 0
            bad = False
            for ch in cleaned:
                if ch == "(": depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth < 0:
                        bad = True
                        break
            if bad or depth != 0:
                continue
            val = _safe_eval_math_expr(cleaned)
            if val is None:
                continue
            if not (-99999 <= val <= 99999):
                continue
            score = 1000 - tail_strip * 15 - vi * 5
            if isinstance(val, int):
                score += 5000
            elif float(val).is_integer():
                score += 2500
            else:
                score -= 500
            hit = "⭐" if (val == expected_ans) else "  "
            candidates.append((score, hit, val, cleaned, tail_strip, vi, overrides))

    candidates.sort(key=lambda x: x[0], reverse=True)
    for i, (s, h, v, expr, ts, vi, ovr) in enumerate(candidates[:15]):
        print(f"  #{i} {h} score={s:5d} value={v:4d} expr={expr!r:15s} tail_strip={ts} v#{vi} override={ovr!r}")

debug_aggressive("4v57", 9, "baiyin_cap_1.png")
debug_aggressive("7x7e3", 49, "baiyin_cap_2.png")
debug_aggressive("9t39", 27, "baiyin_cap_3.png")
