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

#148 防退化:
  老版本只看用户按回车就保存, 实测有用户扫码后 PC 端仍停留在扫码引导页,
  误存了访客 storage_state(__WPSENV__.user.id="fake_xxx", permission:read)。
  现在保存前必须校验: __WPSENV__.user.id 不以 "fake_" 开头(真用户 id 为数字串)。
  校验失败拒绝保存, 强制用户回到浏览器确认表格已正常显示。
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
        # #148 修复: 原 viewport 1440x900 导致登录弹窗二维码太小看不清;
        # 启动最大化 + viewport 1600x1000, 让二维码足够大便于扫码。
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
        )
        page = ctx.new_page()
        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        print(f"  已打开: {target_url}")
        # #148 若登录弹窗未自动弹出, 尝试点击页面上的登录按钮
        for sel in ["text=登录", "text=立即登录", "button:has-text('登录')"]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    print(f"  检测到登录按钮({sel}), 点击打开登录弹窗…")
                    btn.click()
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                continue
        print("  👉 请用 WPS/微信 扫码登录(二维码应在浏览器中央弹窗显示, 已放大视口)。")
        print("  ⏎ 登录完成且表格内容正常显示后, 请回到本终端按 <回车> 保存登录态…")

        input()  # 等待用户确认登录完成

        # #148 防退化: 保存前校验登录态是否真生效(防误存访客 storage_state)
        # 金山文档前端会把访客身份写入 window.__WPSENV__.user.id, 真用户 id 是纯数字,
        # 访客形如 "fake_520110566"。若仍为访客, 强制拒绝保存。
        try:
            user_info = page.evaluate(
                """() => {
                    const env = window.__WPSENV__ || {};
                    const u = (env.file_info && env.file_info.user) || env.user || {};
                    return {id: String(u.id || ''), name: String(u.name || ''),
                            perm: String(u.permission || '')};
                }"""
            )
        except Exception as e:
            print(f"⚠ 无法读取登录态校验信息({type(e).__name__}: {e})")
            user_info = {"id": "", "name": "", "perm": ""}

        uid = user_info.get("id", "")
        is_guest = not uid or uid.startswith("fake_")
        if is_guest:
            print("\n❌ 登录态校验失败: 当前仍是访客身份 "
                  f"(user.id={uid or '<空>'!r}, name={user_info.get('name','')!r}, "
                  f"perm={user_info.get('perm','')!r})。")
            print("   请回到浏览器: ① 确认二维码已扫描 ② 完成手机端 WPS 授权确认 "
                  "③ 等待表格内容正常显示(顶部头像区应为你的真实头像, 不是访客)")
            print("   然后回到本终端再按一次 <回车> 重新校验。")
            print("   (拒绝保存访客登录态, 防止后续同步脚本误用)")
            input()
            # 二次校验
            try:
                user_info2 = page.evaluate(
                    """() => {
                        const env = window.__WPSENV__ || {};
                        const u = (env.file_info && env.file_info.user) || env.user || {};
                        return {id: String(u.id || ''), name: String(u.name || '')};
                    }"""
                )
                uid2 = user_info2.get("id", "")
                if not uid2 or uid2.startswith("fake_"):
                    print(f"\n❌ 二次校验仍为访客(user.id={uid2!r}), 放弃保存。")
                    browser.close()
                    return 2
                uid = uid2
            except Exception as e:
                print(f"\n❌ 二次校验失败: {type(e).__name__}: {e}, 放弃保存。")
                browser.close()
                return 2

        ctx.storage_state(path=str(STATE_PATH))
        print(f"\n✅ 登录态已保存: {STATE_PATH}")
        print(f"   校验通过: user.id={uid} name={user_info.get('name','')!r}")
        print(f"   cookies={len(ctx.cookies())} 条")
        browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
