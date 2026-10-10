#!/usr/bin/env python3
"""Element Plus 2.x 弃用项批量迁移（el-link / el-pagination）。

## 背景

项目 `package.json` 声明 `element-plus: ^2.6.2`，实际安装 **2.14.4**（npm 解析最新 minor）。
2.6 → 2.14 期间部分组件的 API 被标记弃用（3.0.0 移除），运行时持续打印：

    [el-link] [API] The underline option (boolean) is about to be deprecated in
              version 3.0.0, please use 'always' | 'hover' | 'never' instead.

本脚本做**机械迁移**，把"官方已明确等价"的写法换成新写法。

## 迁移规则（只改确定等价的部分）

### el-link
| 旧 | 新 | 等价性依据 |
|---|---|---|
| `:underline="false"` | `underline="never"` | ✅ 官方弃用提示直接给出目标取值 |
| `:underline="true"`  | `underline="always"` | ✅ 同上 |

> ⚠️ **不迁移动态表达式**（如 `:underline="someVar"`）——
> 无法静态判定等价，留给人工判断。
> ⚠️ 同理 `el-pagination` 的弃用项若涉及动态值，脚本不处理（见下方 RULES 注释）。

## 用法

    python3 scripts/migrate_ep_deprecations.py            # 预览（默认，不写盘）
    python3 scripts/migrate_ep_deprecations.py --apply    # 写盘

⚠️ 仅做文本替换；写盘后请跑 eslint / build 验证。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "src"

# (编译后的正则, 替换文本, 说明)
RULES: list[tuple[re.Pattern, str, str]] = [
    # el-link: 布尔 underline → 枚举 underline
    (re.compile(r':underline="false"'), 'underline="never"',
     'el-link :underline="false" → underline="never"'),
    (re.compile(r':underline="true"'), 'underline="always"',
     'el-link :underline="true" → underline="always"'),
]

# el-pagination: 裸 `small` 属性 → `size="small"`
#   官方弃用提示：`[el-pagination] [API] small is about to be deprecated in
#   version 3.0.0, please use size instead.`
#   ⚠️ 只在 <el-pagination ...> 标签内替换，避免误伤其它组件的 small 用法
PAGINATION_TAG = re.compile(r'<el-pagination\b[^>]*?/?>', re.DOTALL)
PAGINATION_SMALL = re.compile(r'(?<![-\w:])small(?=\s|/|>)')


def _fix_pagination(tag: str) -> tuple[str, int]:
    """把 el-pagination 标签内的裸 small 换成 size="small"（幂等）。"""
    if 'size="small"' in tag:
        return tag, 0
    return PAGINATION_SMALL.subn('size="small"', tag)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写盘（默认仅预览）")
    args = ap.parse_args()

    files = sorted(SRC.rglob("*.vue"))
    report: list[tuple[Path, str, int]] = []
    total = 0

    for f in files:
        text = f.read_text(encoding="utf-8")
        new = text
        per_rule: list[tuple[str, int]] = []
        # ① 纯文本规则（el-link）
        for pat, rep, desc in RULES:
            new, n = pat.subn(rep, new)
            if n:
                per_rule.append((desc, n))
        # ② el-pagination 裸 small → size="small"（限定标签内，避免误伤）
        n_pag = 0

        def _sub(m: re.Match) -> str:
            nonlocal n_pag
            tag, n = _fix_pagination(m.group(0))
            n_pag += n
            return tag

        new = PAGINATION_TAG.sub(_sub, new)
        if n_pag:
            per_rule.append(('el-pagination 裸 small → size="small"', n_pag))

        if new != text:
            for desc, n in per_rule:
                report.append((f, desc, n))
                total += n
            if args.apply:
                f.write_text(new, encoding="utf-8")

    mode = "已写盘" if args.apply else "预览（未写盘）"
    print(f"=== Element Plus 弃用项迁移 [{mode}] ===\n")
    cur = None
    for f, desc, n in report:
        if f != cur:
            print(f"  {f.relative_to(ROOT)}")
            cur = f
        print(f"      {n:>3} 处  {desc}")
    print(f"\n合计 {total} 处")
    if not args.apply and total:
        print("\n→ 确认无误后加 --apply 执行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
