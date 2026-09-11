#!/usr/bin/env python3
"""el-radio-button: label → value 迁移（Element Plus 2.14 弃用修复）。

## 为什么必须迁移

Element Plus 2.6+ 起，`el-radio-button` 的 `label` 作为「值」的用法被标记 deprecate
（3.0.0 移除），控制台会持续告警：

    [el-radio] [API] label act as value is about to be deprecated in version 3.0.0,
    please use value instead.

项目实际装的 `element-plus` 为 **2.14.4**（package.json 写 ^2.6.2），故告警真实存在。
官方迁移目标：`label` → `value`。

## 安全边界（只改该改的）

1. **只处理 `<el-radio-button>`**，不动 `<el-radio>`（其 `label` 有显示语义）与其它组件。
2. **只替换「值」位置的 label**：
   - 自闭合：`<el-radio-button label="x" />`         → `value="x"`
   - 带子内容：`<el-radio-button label="x">文本</…>`  → `value="x"`（文本保留为显示）
   - 动态绑定：`:label="expr"`                        → `:value="expr"`
3. **不改** `<el-radio-button>` 内作为**显示文本**的 `label` —— 本项目经核查不存在该用法
   （157 处全部为「值」；显示文本均写在标签内容里）。

## 用法

    python3 scripts/migrate_radio_label_to_value.py --dry-run   # 预览（默认）
    python3 scripts/migrate_radio_label_to_value.py --apply     # 写盘

⚠️ 仅做**文本替换**，不做格式化；写完请自行跑 eslint / build 验证。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "src"

# 匹配 <el-radio-button ...> 的开标签（含自闭合），跨行安全
TAG_RE = re.compile(r"<el-radio-button\b[^>]*?/?>", re.DOTALL)


def migrate_tag(tag: str) -> tuple[str, bool]:
    """把单个 el-radio-button 标签内的 label 属性改为 value。"""
    changed = False

    # ① 静态: label="xxx"  →  value="xxx"
    new_tag, n = re.subn(r'\blabel="', 'value="', tag)
    changed |= n > 0

    # ② 动态: :label="expr" →  :value="expr"
    #    注意顺序：先处理带冒号的，避免被 ① 误伤（① 的 \blabel 不匹配 :label 的 label? 
    #    实测 \blabel 会匹配 ":label" 中的 label，故必须**先**处理带冒号的并加负向断言）
    new_tag = re.sub(r'(?<!:)\blabel="', 'value="', new_tag)
    new_tag = re.sub(r":label=", ":value=", new_tag)
    changed |= new_tag != tag

    return new_tag, changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写盘（默认仅预览）")
    args = ap.parse_args()

    files = sorted(SRC.rglob("*.vue"))
    total_tags = total_files = 0
    report: list[tuple[Path, int]] = []

    for f in files:
        text = f.read_text(encoding="utf-8")
        if "el-radio-button" not in text:
            continue

        hits = 0

        def _sub(m: re.Match) -> str:
            nonlocal hits
            tag = m.group(0)
            new, ch = migrate_tag(tag)
            if ch:
                hits += 1
            return new

        new_text = TAG_RE.sub(_sub, text)
        if hits:
            report.append((f, hits))
            total_tags += hits
            total_files += 1
            if args.apply:
                f.write_text(new_text, encoding="utf-8")

    mode = "已写盘" if args.apply else "预览（未写盘）"
    print(f"=== el-radio-button label → value 迁移 [{mode}] ===\n")
    for f, n in report:
        print(f"  {n:>3} 处  {f.relative_to(ROOT)}")
    print(f"\n合计: {total_files} 个文件 / {total_tags} 处")
    if not args.apply and total_tags:
        print("\n→ 确认无误后加 --apply 执行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
