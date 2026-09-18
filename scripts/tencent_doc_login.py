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

#148 防退化:
  老版本只看用户按回车就保存, 实测有用户扫码后 PC 端仍停留在扫码引导页,
  误存了访客 storage_state(body class 含 skeleton-guest-mode, 右上角显示
  "Login 腾讯文档"按钮, cookies 里无 skey/p_skey/uin 真登录凭证)。
  现在保存前必须双重校验:
    ① body 不含 skeleton-guest-mode 类(访客模式标识)
    ② cookies 含 skey / p_skey / uin / p_uin / pskey 之一(腾讯真登录凭证)
  校验失败拒绝保存, 强制用户回到浏览器确认登录已生效。
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
# 腾讯文档登录页(直接打开显示扫码, 不会跳访客视图)
TENCENT_LOGIN_URL = "https://docs.qq.com/login?from=docs"
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

    print("🌐 打开有头浏览器(腾讯登录页)…")
    print("   请在浏览器里扫码登录腾讯文档。登录成功后会跳转到目标共享表,")
    print("   看到表格可正常查看后, 回到本终端按 <回车> 保存登录态并退出。\n")

    with sync_playwright() as p:
        # headless=False 弹出真实浏览器(本机), 启动最大化让二维码清晰
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            viewport={"width": 1600, "height": 1000},
        )
        page = ctx.new_page()
        # #148 修复: 直接打开共享表会进访客只读视图, 不引导扫码登录。
        # 改为先打开腾讯登录页(直接显示扫码二维码), 用户扫码成功后再跳转到目标共享表。
        print(f"  打开登录页: {TENCENT_LOGIN_URL}")
        page.goto(TENCENT_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        # 若登录页没出现, 尝试点击页面上的"登录"按钮兜底
        try:
            login_btn = page.query_selector("text=登录")
            if login_btn and login_btn.is_visible():
                login_btn.click()
                page.wait_for_timeout(1500)
        except Exception:
            pass
        print("  👉 请用微信/QQ 扫码登录腾讯文档(二维码应在浏览器中显示)。")
        print("  ⏎ 扫码并确认登录成功后, 请回到本终端按 <回车> (脚本会自动跳转到目标共享表)…")
        input()

        # 登录后跳转到目标共享表, 确认登录态在目标域名也生效
        print(f"  跳转到目标共享表: {target_url}")
        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        print(f"  已打开: {target_url}")
        print("  👉 确认表格内容正常显示后, 请回到本终端按 <回车> 保存登录态…")
        input()

        # #148 防退化: 保存前双重校验登录态真生效(防误存访客 storage_state)
        # ① body 不含 skeleton-guest-mode 类(腾讯前端访客模式标识)
        # ② cookies 含 skey / p_skey / uin / p_uin / pskey 之一(真登录凭证)
        # 校验失败: 给用户提示 + 二次按回车复校, 仍失败则拒绝保存
        def _verify_tencent_login(page_, ctx_) -> tuple[bool, str, str]:
            """返回(是否登录态有效, 失败原因, 当前 body class 摘要)"""
            try:
                body_cls = page_.evaluate(
                    "() => (document.body && document.body.className) || ''"
                )
            except Exception:
                body_cls = ""
            guest_mode = "skeleton-guest-mode" in body_cls
            cookies = ctx_.cookies()
            cookie_names = {c.get("name", "") for c in cookies}
            # 腾讯真登录凭证至少有一个(检查常用 5 个)
            login_keys = {"skey", "p_skey", "uin", "p_uin", "pskey", "rk", "rkey"}
            has_login_cookie = bool(cookie_names & login_keys)
            ok = (not guest_mode) and has_login_cookie
            reason = []
            if guest_mode:
                reason.append(f"body 含 skeleton-guest-mode(访客模式)")
            if not has_login_cookie:
                missing = sorted(login_keys - cookie_names)
                reason.append(f"缺少真登录凭证 cookie({', '.join(missing)})")
            return ok, "; ".join(reason) or "ok", body_cls[:100]

        ok, reason, body_cls = _verify_tencent_login(page, ctx)
        if not ok:
            print("\n❌ 登录态校验失败: 当前仍是访客身份。")
            print(f"   失败原因: {reason}")
            print(f"   body class: {body_cls!r}")
            print("   请回到浏览器: ① 确认二维码已扫描 ② 完成手机端腾讯授权确认 "
                  "③ 等待表格内容正常显示(顶部右上角应显示你的头像, 不是 Login 按钮)")
            print("   然后回到本终端再按一次 <回车> 重新校验。")
            print("   (拒绝保存访客登录态, 防止后续同步脚本误用)")
            input()
            ok, reason, body_cls = _verify_tencent_login(page, ctx)
            if not ok:
                print(f"\n❌ 二次校验仍失败: {reason}, 放弃保存。")
                browser.close()
                return 2

        # 校验通过, 保存登录态
        ctx.storage_state(path=str(STATE_PATH))
        print(f"\n✅ 登录态已保存: {STATE_PATH}")
        print(f"   校验通过: body 非 guest-mode, 含真登录凭证 cookie")
        print(f"   cookies={len(ctx.cookies())} 条, 后续共享文档脚本将复用此登录态。")
        browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
