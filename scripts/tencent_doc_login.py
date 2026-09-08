#!/usr/bin/env python3
"""腾讯文档本机扫码登录 → 生成 storage_state(供共享文档读取/导出复用)

背景:
  共享表格(如 https://docs.qq.com/sheet/...) 未登录只能读腾讯私有压缩格式;
  用账号登录后可"导出为 Excel", 走标准 xlsx 解析(复用 _poc_shared_doc_reader)。
  本脚本在 macOS 本机弹出有头浏览器 → 你扫码登录腾讯文档 → 成功后把
  storage_state 存到 BROWSER_STATE_DIR/tencent_doc.json, 后续脚本复用(免二次扫码)。

与 BMS/SCRM 登录态模式一致(bms_fetcher 里 context.storage_state(path=...))。

用法:
  cd backend && ./.venv/bin/python ../scripts/tencent_doc_login.py
  # 弹出浏览器 → 扫码/登录腾讯文档 → 看到"✅ 登录成功"即自动保存关闭

登录态文件安全: 存于 data/browser_states/(独立于 uploads, 不对外暴露)。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

from app.core.config import settings

# 默认打开的目标共享表(可用 --url 覆盖; 登录态为账号级, 打开任意共享表均可引导扫码)
TARGET_SHEET_URL = "https://docs.qq.com/sheet/DWnRPSmpKU29uSk9T"
STATE_PATH = Path(settings.BROWSER_STATE_DIR) / "tencent_doc.json"


def main() -> int:
    import argparse
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description="腾讯文档扫码登录, 生成 tencent_doc.json")
    ap.add_argument("--url", default=TARGET_SHEET_URL, help="登录后打开的共享表链接")
    args = ap.parse_args()
    target_url = args.url

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 若已有旧 state, 先备份(登录成功会覆盖)
    if STATE_PATH.exists():
        bak = STATE_PATH.with_suffix(".json.bak")
        bak.write_bytes(STATE_PATH.read_bytes())
        print(f"⚠ 检测到旧登录态, 已备份到 {bak.name}")

    print("🌐 打开有头浏览器(直接打开目标共享表)…")
    print("   请在浏览器里完成登录(会引导扫码)。看到表格可正常查看后,")
    print("   回到本终端按 <回车> 保存登录态并退出。\n")

    with sync_playwright() as p:
        # headless=False 弹出真实浏览器(本机)
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        # 直接打开目标共享表: 未登录会引导扫码, 登录后停留在表格页
        page.goto(target_url, wait_until="domcontentloaded")
        print(f"  已打开: {target_url}")
        print("  👉 若未登录会自动跳登录页, 请扫码登录; 登录成功后表格会正常显示。")
        print("  ⏎ 登录完成后, 请回到本终端按 <回车> 保存登录态…")

        # 等待用户在终端按回车(同步 input)
        input()

        # 无论检测结果如何都保存当前登录态(用户已确认登录)
        ctx.storage_state(path=str(STATE_PATH))
        print(f"\n✅ 登录态已保存: {STATE_PATH}")
        print(f"   cookies={len(ctx.cookies())} 条, 后续共享文档脚本将复用此登录态。")
        browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
