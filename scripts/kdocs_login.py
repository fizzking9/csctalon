#!/usr/bin/env python3
"""金山文档(kdocs.cn / WPS)本机扫码登录 → 生成 storage_state

背景:
  金山共享表格(如 https://www.kdocs.cn/l/...) 未登录只能看 canvas, 无法导出;
  登录后可"导出为 Excel", 走标准 xlsx 解析(与腾讯 tencent_doc_export 同思路)。
  本脚本在 macOS 本机弹出有头浏览器 → 打开共享链接 → 你扫码登录 WPS →
  成功后把 storage_state 存到 BROWSER_STATE_DIR/kdocs.json, 后续脚本复用。

用法:
  cd backend && ./.venv/bin/python ../scripts/kdocs_login.py
  # 弹出浏览器 → 打开金山共享文档 → 若未登录会引导扫码 → 登录成功后
  # 回到终端按 <回车> 保存登录态并退出

登录态文件安全: 存于 backend/data/browser_states/(已 gitignore)。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

from app.core.config import settings

# 默认打开的金山共享文档(可用 --url 覆盖; 仅作登录引导目标, 登录态为账号级)
TARGET_URL = "https://www.kdocs.cn/l/cbZIHtt6obyf"
STATE_PATH = Path(settings.BROWSER_STATE_DIR) / "kdocs.json"


def main() -> int:
    import argparse
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description="金山文档扫码登录, 生成 kdocs.json")
    ap.add_argument("--url", default=TARGET_URL, help="登录后打开的共享文档链接")
    args = ap.parse_args()
    target_url = args.url

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        bak = STATE_PATH.with_suffix(".json.bak")
        bak.write_bytes(STATE_PATH.read_bytes())
        print(f"⚠ 检测到旧登录态, 已备份到 {bak.name}")

    print("🌐 打开有头浏览器(金山共享文档)…")
    print("   请在浏览器里完成登录(WPS 扫码/手机号)。看到表格可正常查看后,")
    print("   回到本终端按 <回车> 保存登录态并退出。\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            accept_downloads=True,
        )
        page = ctx.new_page()
        page.goto(target_url, wait_until="domcontentloaded")
        print(f"  已打开: {target_url}")
        print("  👉 若未登录会自动引导登录, 请扫码; 登录成功后表格可正常显示。")
        print("  ⏎ 登录完成后, 请回到本终端按 <回车> 保存登录态…")

        input()  # 等待用户确认登录完成

        ctx.storage_state(path=str(STATE_PATH))
        print(f"\n✅ 登录态已保存: {STATE_PATH}")
        print(f"   cookies={len(ctx.cookies())} 条")
        browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
