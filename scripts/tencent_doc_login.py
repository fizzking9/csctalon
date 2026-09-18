#!/usr/bin/env python3
"""腾讯文档扫码登录 → 生成 storage_state(供共享文档读取/导出复用)

背景:
  共享表格(如 https://docs.qq.com/sheet/...) 未登录只能读腾讯私有压缩格式;
  用账号登录后可"导出为 Excel", 走标准 xlsx 解析(复用 _poc_shared_doc_reader)。
  登录态存到 BROWSER_STATE_DIR/tencent_doc.json, 后续脚本复用(免二次扫码)。

两种模式:
  ① GUI 模式(默认, 本机用): 弹出真实浏览器, 用户扫码后按回车保存。
  ② Headless 模式(--headless, 生产/无 GUI 环境用): 用 headless 浏览器打开登录页,
     截图二维码到 --qr-out, 轮询真登录 cookie 出现后自动保存, 无需人工回车。

用法:
  # 本机 GUI 扫码
  cd backend && ./.venv/bin/python ../scripts/tencent_doc_login.py

  # 生产 Headless 扫码(二维码截图到 /tmp/tencent_qr.png, 运维 scp 到本地扫码)
  docker exec -e PYTHONPATH=/app csctalon-backend \
      python /app/scripts/tencent_doc_login.py --headless \
      --qr-out /tmp/tencent_qr.png --timeout 300

登录态文件安全: 存于 data/browser_states/(独立于 uploads, 不对外暴露)。

#148 防退化:
  保存前必须双重校验:
    ① body 不含 skeleton-guest-mode 类(访客模式标识)
    ② cookies 含 skey / p_skey / uin / p_uin / pskey 之一(腾讯真登录凭证)
  校验失败拒绝保存, 强制用户重新扫码。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
# 兼容两种布局: 本机 backend/ 目录(_ROOT/backend) 或 生产容器 /app(_ROOT)
for _p in (str(_ROOT / "backend"), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.core.config import settings

# 默认打开的目标共享表(可用 --url 覆盖; 登录态为账号级)
TARGET_SHEET_URL = "https://docs.qq.com/sheet/DWnRPSmpKU29uSk9T"
# 腾讯文档首页(登录按钮在右上角)
TENCENT_HOME_URL = "https://docs.qq.com/"
STATE_PATH = Path(settings.BROWSER_STATE_DIR) / "tencent_doc.json"
# 腾讯真登录凭证 cookie 名(任一出现即登录成功)
LOGIN_COOKIE_KEYS = {"skey", "p_skey", "uin", "p_uin", "pskey", "rk", "rkey"}


def _verify_login(page, ctx) -> tuple[bool, str, str]:
    """返回(是否登录态有效, 失败原因, body class 摘要)"""
    try:
        body_cls = page.evaluate("() => (document.body && document.body.className) || ''")
    except Exception:
        body_cls = ""
    guest_mode = "skeleton-guest-mode" in body_cls
    cookies = ctx.cookies()
    cookie_names = {c.get("name", "") for c in cookies}
    has_login_cookie = bool(cookie_names & LOGIN_COOKIE_KEYS)
    ok = (not guest_mode) and has_login_cookie
    reason = []
    if guest_mode:
        reason.append("body 含 skeleton-guest-mode(访客模式)")
    if not has_login_cookie:
        reason.append(f"缺少真登录凭证 cookie({', '.join(sorted(LOGIN_COOKIE_KEYS - cookie_names))})")
    return ok, "; ".join(reason) or "ok", body_cls[:100]


def _open_login_and_show_qr(page) -> None:
    """打开腾讯首页 → 点登录 → 勾协议 → 点 Log in now → 显示二维码"""
    page.goto(TENCENT_HOME_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    # 点右上角"登录"按钮弹出登录弹窗
    for sel in ["text=登录", "button:has-text('登录')", "a:has-text('登录')"]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(2000)
                break
        except Exception:
            continue
    # 勾选服务协议(弹窗底部 checkbox)
    try:
        cb = page.query_selector("input[type='checkbox']")
        if cb and not cb.is_checked():
            cb.click()
            page.wait_for_timeout(500)
    except Exception:
        pass
    # 点 "Log in now" 显示二维码
    for sel in ["text=Log in now", "button:has-text('Log in now')"]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(3000)
                break
        except Exception:
            continue


def main() -> int:
    import argparse
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description="腾讯文档扫码登录, 生成 tencent_doc.json")
    ap.add_argument("--url", default=TARGET_SHEET_URL, help="登录后打开的共享表链接")
    ap.add_argument("--headless", action="store_true",
                    help="无头模式(生产环境用): 截图二维码 + 轮询登录态自动保存")
    ap.add_argument("--qr-out", default="/tmp/tencent_qr.png",
                    help="headless 模式下二维码截图输出路径")
    ap.add_argument("--timeout", type=int, default=300,
                    help="headless 模式下等待扫码的超时秒数")
    args = ap.parse_args()
    target_url = args.url

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
        )
        page = ctx.new_page()

        if headless:
            # ── Headless 模式: 截图二维码 + 轮询登录态 ──
            print(f"🌐 [headless] 打开腾讯首页并触发登录弹窗…")
            _open_login_and_show_qr(page)
            qr_path = Path(args.qr_out)
            page.screenshot(path=str(qr_path), full_page=False)
            print(f"📸 二维码截图: {qr_path} ({qr_path.stat().st_size} 字节)")
            print(f"   👉 请把该文件传到本地, 用微信扫码登录")
            print(f"   👉 扫码后脚本自动检测真登录 cookie (超时 {args.timeout}s)…")

            deadline = time.time() + args.timeout
            while time.time() < deadline:
                try:
                    cookies = {c.get("name", "") for c in ctx.cookies()}
                    if cookies & LOGIN_COOKIE_KEYS:
                        matched = sorted(cookies & LOGIN_COOKIE_KEYS)
                        print(f"✅ 检测到登录凭证: {matched}")
                        break
                except Exception:
                    pass
                time.sleep(3)
            else:
                print(f"❌ {args.timeout}s 内未检测到登录态, 超时。")
                browser.close()
                return 1
        else:
            # ── GUI 模式: 弹浏览器, 用户扫码后按回车 ──
            print("🌐 打开有头浏览器(腾讯首页)…")
            _open_login_and_show_qr(page)
            print("  👉 请用微信/QQ 扫码登录腾讯文档。")
            print("  ⏎ 登录成功后按 <回车> (脚本会跳转到目标共享表)…")
            input()

        # 跳转到目标共享表, 确认登录态在目标域名生效
        print(f"  跳转到目标共享表: {target_url}")
        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        ok, reason, body_cls = _verify_login(page, ctx)
        if not ok:
            print(f"\n❌ 登录态校验失败: {reason}")
            print(f"   body class: {body_cls!r}")
            if not headless:
                print("   请回到浏览器确认登录生效后按 <回车> 重新校验…")
                input()
                ok, reason, _ = _verify_login(page, ctx)
            if not ok:
                print(f"❌ 二次校验仍失败: {reason}, 放弃保存。")
                browser.close()
                return 2

        ctx.storage_state(path=str(STATE_PATH))
        cookies = ctx.cookies()
        print(f"\n✅ 登录态已保存: {STATE_PATH}")
        print(f"   校验通过: cookies={len(cookies)} 条, "
              f"真登录凭证={sorted({c.get('name','') for c in cookies} & LOGIN_COOKIE_KEYS)}")
        browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
