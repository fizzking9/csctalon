#!/usr/bin/env python3
"""Debug baiyin_cap_3.png: 检查 _aggressive_try_math_compute 选出的 result 到底是什么"""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.captcha_session import (
    _apply_symbol_map, _MATH_SYMBOL_FALLBACK_VARIANTS,
    _aggressive_try_math_compute, _score_math_candidate,
    _MATH_HEAD_CLEAN_RE, _MATH_TAIL_STRIP_RE, _MATH_EXPR_VALID_RE, _safe_eval_math_expr
)

ocr_code = "9t39"
expected = 27

# 枚举所有变体
print(f"DEBUG ocr_code={ocr_code!r}，期望答案={expected}")
print()

all_candidates = []
for vi, ovr in enumerate(_MATH_SYMBOL_FALLBACK_VARIANTS):
    mapped = _apply_symbol_map(ocr_code, ovr)
    print(f"v{vi} override={ovr!r} → mapped={mapped!r}")
print()

fb = _aggressive_try_math_compute(ocr_code)
print("fb BEST result:", fb)
print()

# 枚举所有
for vi, ovr in enumerate(_MATH_SYMBOL_FALLBACK_VARIANTS):
    mapped = _apply_symbol_map(ocr_code, ovr)
    max_strip = min(6, max(0, len(mapped) - 2))
    for ts in range(max_strip + 1):
        cur = mapped[:-ts] if ts>0 else mapped
        if len(cur) < 3: continue
        cleaned = _MATH_HEAD_CLEAN_RE.sub("", cur)
        cleaned = _MATH_TAIL_STRIP_RE.sub("", cleaned)
        while cleaned and cleaned[-1] in ("=", "?"): cleaned = cleaned[:-1]
        while cleaned and cleaned[-1] not in "0123456789)": cleaned = cleaned[:-1]
        if len(cleaned) < 3: continue
        if not _MATH_EXPR_VALID_RE.match(cleaned): continue
        if not any(op in cleaned for op in ("+", "-", "*", "/")): continue
        depth = 0
        bad = False
        for ch in cleaned:
            if ch == "(": depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0: bad = True; break
        if bad or depth != 0: continue
        val = _safe_eval_math_expr(cleaned)
        if val is None: continue
        if not (-99999 <= val <= 99999): continue
        sc = _score_math_candidate(cleaned, val, tail_strip=ts, variant_idx=vi)
        hit = "⭐⭐⭐" if val == expected else ""
        all_candidates.append((sc, hit, val, cleaned, ts, vi))
all_candidates.sort(key=lambda x: x[0], reverse=True)
print("Top 10 candidates:")
for i,(sc,hh,v,ex,ts,vi) in enumerate(all_candidates[:10]):
    print(f" #{i:2d} score={sc:5d} value={v:4d} expr={ex!r:12s} tail={ts} v{vi} {hh}")
