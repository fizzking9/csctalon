#!/usr/bin/env python3
"""金山文档(kdocs.cn / WPS)扫码登录 CLI → 生成 storage_state

背景:
  金山共享表格(如 https://www.kdocs.cn/l/...) 未登录只能看 canvas, 无法导出;
  登录后可"导出为 Excel", 走标准 xlsx 解析。
  登录态存到 BROWSER_STATE_DIR/kdocs.json, 后续共享文档同步复用。

两种模式:
  ① GUI 模式(默认, 本机用): 弹出真实浏览器, 用户扫码后按回车保存。
  ② Headless 模式(--headless, 生产/无 GUI 环境用): 用 headless 浏览器打开文档,
     截图二维码到 --qr-out, 轮询 __WPSENV__.user.id 非 fake_ 后自动保存。

用法:
  # 本机 GUI 扫码
  cd backend && ./.venv/bin/python -m app.services.shared_doc_kdocs_cli

  # 生产 Headless 扫码(已预装于镜像, 可直接 docker exec)
  docker exec csctalon-backend \
      python -m app.services.shared_doc_kdocs_cli --headless \
      --qr-out /tmp/kdocs_qr.png --timeout 300

#148 防退化:
  保存前校验 __WPSENV__.user.id 不以 "fake_" 开头(真用户 id 为数字串),
  访客身份拒绝保存。
"""
from __future__ import annotations

import time
from pathlib import Path

from app.core.config import settings

# 登录按钮选择器与二维码判定复用页内扫码的真身定义, 避免两份漂移。
# (2026-10-21 生产实测: CLI 旧选择器 text=登录 会误点到"协作邀请"浮层,
#  截图里根本没有二维码 → 用户没有可扫的东西。.wlcp-btn:not(.is-highlight) 才有效。)
from app.services.shared_doc_login_service import (
    _KDOCS_SIGNIN_BUTTONS,
    _QRCODE_PRESENT_JS,
)

TARGET_URL = "https://www.kdocs.cn/l/cbZIHtt6obyf"
STATE_PATH = Path(settings.BROWSER_STATE_DIR) / "kdocs.json"


def _get_kdocs_user_id(page) -> str:
    """读取金山文档当前用户 id(访客形如 fake_xxx, 真用户为数字串)"""
    try:
        return page.evaluate(
            """() => {
                const env = window.__WPSENV__ || {};
                const u = (env.file_info && env.file_info.user) || env.user || {};
                return String(u.id || '');
            }"""
        )
    except Exception:
        return ""


def _open_doc_and_show_login(page) -> None:
    """打开共享文档 → 点登录按钮展开二维码 → 等二维码图片真正加载完成

    选择器顺序与页内扫码保持一致(见 shared_doc_login_service._KDOCS_SIGNIN_BUTTONS)。
    必须先等二维码图片 naturalWidth>0, 否则截图是一块白板。
    """
    page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    # 清掉可能遮挡登录按钮的浮层(实测: "XX 邀请你协作编辑文档" 模态框会盖住登录入口)
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
    except Exception:
        pass
    for sel in _KDOCS_SIGNIN_BUTTONS:
        try:
            page.click(sel, timeout=2500)
            try:
                page.wait_for_function(_QRCODE_PRESENT_JS, timeout=8000)
                print(f"   ✔ 二维码已展开(选择器={sel})")
            except Exception:
                page.wait_for_timeout(2000)
                print(f"   ⚠ 已点击({sel}), 二维码判定未命中但继续")
            return
        except Exception:
            continue
    print("   ⚠ 未找到登录按钮, 按当前页面截图")


def _maybe_confirm_middle_page(page) -> bool:
    """扫码成功后金山会跳到 account.wps.cn 的"选择账号/确认登录"中间页。

    该页只有一个 `a#loginConfirm.form_button`("Sign In"), 不点它页面永远停在访客态
    (生产实测 2026-10-21: 未点 → uid=fake_xxx)。返回 True 表示点到了。
    """
    try:
        cur = (page.url or "").lower()
    except Exception:
        return False
    if "account.wps.cn" not in cur:
        return False
    for sel in ("#loginConfirm", "a.form_button", ".form_button",
                "a:has-text('Sign In')", "button:has-text('Sign In')"):
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(3000)
                print(f"   ✔ 已点击中间页确认按钮: {sel}")
                return True
        except Exception:
            continue
    return False


def main() -> int:
    import argparse

    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description="金山文档扫码登录, 生成 kdocs.json")
    ap.add_argument("--url", default=TARGET_URL, help="登录后打开的共享文档链接")
    ap.add_argument("--headless", action="store_true",
                    help="无头模式(生产环境用): 截图二维码 + 轮询登录态自动保存")
    ap.add_argument("--qr-out", default="/tmp/kdocs_qr.png",
                    help="headless 模式下二维码截图输出路径")
    ap.add_argument("--timeout", type=int, default=300,
                    help="headless 模式下等待扫码的超时秒数")
    args = ap.parse_args()

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        bak = STATE_PATH.with_suffix(".json.bak")
        bak.write_bytes(STATE_PATH.read_bytes())
        print(f"⚠ 旧登录态已备份到 {bak.name}")

    headless = args.headless
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[] if headless else ["--start-maximized"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
        )
        page = ctx.new_page()
        _open_doc_and_show_login(page)

        if headless:
            qr_path = Path(args.qr_out)
            page.screenshot(path=str(qr_path), full_page=False)
            print(f"📸 二维码截图: {qr_path} ({qr_path.stat().st_size} 字节)")
            print("   👉 请把该文件传到本地, 用 WPS/微信扫码登录")
            print(f"   👉 扫码后脚本自动检测 __WPSENV__.user.id (超时 {args.timeout}s)…")

            deadline = time.time() + args.timeout
            uid = ""
            confirmed = False
            while time.time() < deadline:
                # 扫码成功后可能停在 account.wps.cn 中间页, 必须先点确认按钮
                if _maybe_confirm_middle_page(page):
                    confirmed = True
                uid = _get_kdocs_user_id(page)
                if uid and not uid.startswith("fake_"):
                    print(f"✅ 检测到真用户: user.id={uid}")
                    break
                if confirmed and not uid:
                    # 点完确认但页面没自动跳回文档页 → 主动跳一次再检测
                    try:
                        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass
                    confirmed = False
                time.sleep(3)
            else:
                print(f"❌ {args.timeout}s 内未检测到登录态(最后 uid={uid or '<空>'}), 超时。")
                browser.close()
                return 1
        else:
            print("🌐 打开有头浏览器(金山共享文档)…")
            print("  👉 请用 WPS/微信扫码登录。")
            print("  ⏎ 登录成功且表格显示正常后, 按 <回车> 保存登录态…")
            input()

        # 校验: 非访客身份
        uid = _get_kdocs_user_id(page)
        if not uid or uid.startswith("fake_"):
            print(f"\n❌ 登录态校验失败: 当前仍是访客身份(user.id={uid or '<空>'!r})。")
            if not headless:
                print("   请回到浏览器确认登录生效后按 <回车> 重新校验…")
                input()
                uid = _get_kdocs_user_id(page)
            if not uid or uid.startswith("fake_"):
                print(f"❌ 二次校验仍为访客(user.id={uid!r}), 放弃保存。")
                browser.close()
                return 2

        ctx.storage_state(path=str(STATE_PATH))
        cookies = ctx.cookies()
        print(f"\n✅ 登录态已保存: {STATE_PATH}")
        print(f"   校验通过: user.id={uid}, cookies={len(cookies)} 条")
        browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
