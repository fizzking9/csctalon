#!/usr/bin/env python3
"""独立的 SCRM Playwright 登录 + 解析测试脚本 v2。

修复点:
  - 支持 captcha_type=none (无验证码, SCRM gateway.bilinl.com 实测无验证码)
  - input 优先按 name='username' / name='password' 定位,比 placeholder 更稳健
  - 完整打印每一步: 登录页结构 → 登录跳转 → 操作员页解析

直接从 sync_source_configs 读用户刚保存的 SCRM 配置(解密凭证) → 复用现有的
ScrmWebFetcher (Playwright + 可选 ddddocr 数学验证码识别), 跑一遍:
  1. 打开登录页 → 截图
  2. 输入账号/密码(若有验证码则 OCR 识别 + 填验证码) → 点登录
  3. 判断登录成功(url 不再在 /login)
  4. 进入「客服操作员」页面 → 抓表格 → 打印前 N 行(直接看到解析结果)

同时:
  - 把每一步的截图存到 uploads/_debug_scrm_login/ 目录(给用户可视化核对)
  - 所有步骤都打详细日志

用法:
  cd backend
  source .venv_test/bin/activate   # 如果需要的话
  python3 scripts/debug_scrm_login.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

DEBUG_DIR = ROOT / "uploads" / "_debug_scrm_login"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

# 配置控制台详细日志
log_fmt = "%(asctime)s [%(levelname).3s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=log_fmt, datefmt="%H:%M:%S")
logger = logging.getLogger("scrm_test")
logger.setLevel(logging.DEBUG)

# 记录到文件,方便回查
fh = logging.FileHandler(DEBUG_DIR / "run.log", encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter(log_fmt))
logging.getLogger().addHandler(fh)


def step_save_screenshot(page, name: str) -> Path | None:
    try:
        p = DEBUG_DIR / f"{int(time.time())}_{name}.png"
        page.screenshot(path=str(p), full_page=True)
        logger.info(f"[截图] -> {p.name}")
        return p
    except Exception as e:  # noqa: BLE001
        logger.warning(f"截图失败 {name}: {e}")
        return None


# ── 1. 读取数据库中的 SCRM 配置(解密凭证) ─────────────────────────────────
logger.info("=" * 72)
logger.info("Step 1: 从 sync_source_configs 读 scrm_operator 的配置(解密凭证)")
logger.info("=" * 72)

from app.core.database import SessionLocal  # noqa: E402
from app.services.sync_source_config_service import get_sync_source_config  # noqa: E402
from app.services.scrm_sync_service import _resolve_pw_credentials  # noqa: E402

SCRM_DT = "scrm_operator"

db = SessionLocal()
try:
    cfg = get_sync_source_config(db, SCRM_DT) or {}
finally:
    db.close()

logger.info(f"  data_type      = {SCRM_DT}")
logger.info(f"  base_url       = {cfg.get('base_url')!r}")
logger.info(f"  fetch_mode     = {cfg.get('fetch_mode')!r}")
logger.info(f"  enabled        = {cfg.get('enabled')!r}")
logger.info(f"  username 非空  = {bool(cfg.get('username'))}")
logger.info(f"  password 非空  = {bool(cfg.get('password'))}")

opts = cfg.get("options") or {}
logger.info(f"  options.captcha_type  = {opts.get('captcha_type')!r} (none=不需要验证码)")
logger.info(f"  options.username_ph   = {opts.get('username_placeholder')!r}")
logger.info(f"  options.password_ph   = {opts.get('password_placeholder')!r}")
logger.info(f"  options.captcha_img   = {opts.get('captcha_img_selector')!r}")
logger.info(f"  options.operator_path = {opts.get('web_operator_entry_path')!r}")

if opts.get("web_username_configured") is not None:
    logger.info(f"  web_username_configured (标志位) = {opts.get('web_username_configured')!r}")
    logger.info(f"  web_password_configured (标志位) = {opts.get('web_password_configured')!r}")

# 走与同步服务完全一致的凭证解析
pw_user, pw_pwd, pw_err = _resolve_pw_credentials(cfg)
logger.info("")
logger.info("[_resolve_pw_credentials 结果]")
logger.info(f"  web_username (解析后) 非空 = {bool(pw_user)}")
if pw_user:
    masked_user = pw_user[:2] + "*" * max(0, len(pw_user) - 2) if len(pw_user) > 2 else pw_user
    logger.info(f"  web_username (脱敏)       = {masked_user!r}")
logger.info(f"  web_password (解析后) 非空 = {bool(pw_pwd)}")
logger.info(f"  error (None=成功)         = {pw_err!r}")

if pw_err:
    logger.error("❌ 凭证解析失败, 说明你在同步设置里保存的账号密码还没真正写到对应字段, 先回到同步设置/SyncDashboard 重新保存一次。")
    logger.error(f"   具体错误: {pw_err}")
    sys.exit(2)

if not cfg.get("base_url"):
    logger.error("❌ base_url 为空, 请先在同步设置里填好 SCRM 基础地址")
    sys.exit(2)

# ── 2. 用 ScrmWebFetcher 跑登录 + 抓操作员 ───────────────────────────────
logger.info("")
logger.info("=" * 72)
logger.info("Step 2: 启动 Playwright (已修复: 支持无验证码 + name 优先定位), 跑登录流程")
logger.info("=" * 72)

from app.services.scrm_web_fetcher import ScrmWebFetcher  # noqa: E402

fetcher = ScrmWebFetcher(
    base_url=cfg.get("base_url") or "",
    username=pw_user,
    password=pw_pwd,
    options=opts,
    logger=logger,
    captcha_max_retries=3,  # 无验证码情况下 3 次也够了
)

# 手动控制每一步,便于截图(不直接调用 fetcher.login())
logged_in_ok = False
try:
    fetcher._ensure_playwright()
    assert fetcher._page is not None, "Playwright 页面未创建"
    page = fetcher._page

    needs_cap = fetcher._needs_captcha()
    logger.info(f"  fetcher._needs_captcha() = {needs_cap}")

    # 2.1 打开登录页
    login_url = fetcher._full_url(fetcher._selector("login_url_path"))
    logger.info(f"[Step 2.1] 打开登录页: {login_url}")
    page.goto(login_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)  # 等 SPA + JS 渲染
    step_save_screenshot(page, "01_login_page_initial")

    # 2.2 dump 当前页面 input 结构(确认定位正确)
    logger.info(f"  当前 URL: {page.url}")
    logger.info("  当前登录页 <input> 列表:")
    for i, inp in enumerate(page.query_selector_all("input")):
        info = page.evaluate("""(el) => ({
            type: el.type, name: el.name, placeholder: el.placeholder,
            visible: el.offsetParent !== null,
            html: (el.outerHTML||'').slice(0,150)
        })""", inp)
        logger.info(f"    [{i}] name={info['name']!r} type={info['type']!r} placeholder={info['placeholder']!r} visible={info['visible']}")
        logger.info(f"        html={info['html']}")

    logger.info("  <button> 列表:")
    for i, btn in enumerate(page.query_selector_all("button")):
        info = page.evaluate("""(el) => ({
            text: (el.innerText||'').trim(), type: el.type, disabled: el.disabled,
            visible: el.offsetParent !== null
        })""", btn)
        logger.info(f"    [{i}] text={info['text']!r} type={info['type']!r} disabled={info['disabled']} visible={info['visible']}")

    # 2.3 调用 fetcher.login() 直接走我们刚修的逻辑
    logger.info("")
    logger.info("[Step 2.2] 调用 fetcher.login() (走修复后的 _locate_input + 可选验证码)")
    try:
        fetcher.login()
        logged_in_ok = fetcher._am_i_logged_in()
        logger.info(f"  fetcher.login() 返回, 登录状态 _am_i_logged_in() = {logged_in_ok}")
        if logged_in_ok:
            logger.info(f"  ✅ 登录成功! 现在 URL = {page.url}")
            step_save_screenshot(page, "06_after_login_ok")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"  ❌ fetcher.login() 抛出异常: {e}")
        step_save_screenshot(page, "06_login_exception")

    if not logged_in_ok:
        # 再尝试抓取登录页的错误提示
        logger.warning("  登录未成功,尝试抓页面错误提示...")
        step_save_screenshot(page, "07_login_not_jumped")
        try:
            hints = page.evaluate(
                "() => Array.from(document.querySelectorAll('.el-message, .el-form-item__error, .el-alert__content, .el-message-box, [class*=error], [class*=Error], .alert, .toast, .message, .tip, .notice'))"
                "  .map(n => n.innerText || '').filter(Boolean).slice(0, 10)"
            )
            if hints:
                logger.warning(f"  页面错误提示: {hints}")
            else:
                # dump body 前 40 行非空
                txt = page.locator("body").inner_text()
                lines = [ln for ln in txt.splitlines() if ln.strip()][:40]
                logger.warning(f"  无明显错误提示, body 前 40 行非空文本:\n" + "\n".join(f"    | {l}" for l in lines))
        except Exception as ee:  # noqa: BLE001
            logger.warning(f"  (读错误提示失败: {ee})")

        logger.error("❌ 登录失败。常见原因:1) 账号/密码本身不正确;2) 登录按钮文本与配置不匹配")
        sys.exit(3)

    # ── 3. 进入客服操作员页面,抓表格解析 ────────────────────────────────────
    logger.info("")
    logger.info("=" * 72)
    logger.info("Step 3: 登录后进入 操作员列表页 → 抓表格 → 解析列名+前 5 行")
    logger.info("=" * 72)
    entry = opts.get("web_operator_entry_path") or fetcher._selector("web_operator_entry_path")
    operator_url = fetcher._full_url(entry)
    logger.info(f"  入口 URL: {operator_url}")

    # 注意: 有些 SCRM 入口是带 #/ 的 hash 路由,需要处理
    page.goto(operator_url, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)  # SPA hash 路由加载时间长一些
    step_save_screenshot(page, "08_operator_page_initial")

    logger.info(f"  操作员页加载后 URL = {page.url}")

    # dump 页面文本前 60 行(确认是否真的进了操作员页)
    try:
        txt = page.locator("body").inner_text()
        lines = [ln for ln in txt.splitlines() if ln.strip()][:60]
        logger.info("  页面 body 前 60 行非空:")
        for ln in lines:
            logger.info(f"    | {ln}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"  (读 body 失败: {e})")

    # 尝试点查询按钮
    clicked_query = False
    for txt in ("查询", "搜索", "筛选", "查 询", "搜 索"):
        try:
            btn = page.get_by_role("button", name=txt).first
            if btn.count() > 0:
                btn.wait_for(state="visible", timeout=2000)
                logger.info(f"  点击「{txt}」按钮")
                btn.click()
                page.wait_for_timeout(3500)
                clicked_query = True
                break
        except Exception:
            continue
    if not clicked_query:
        logger.warning("  未找到「查询/搜索」按钮,直接尝试读当前页面表格")
    step_save_screenshot(page, "09_operator_page_after_query_or_current")

    # 读 el-table (或者普通 table)
    rows = fetcher._read_el_table_rows()
    logger.info(f"  fetcher._read_el_table_rows() 解析行数 = {len(rows)}")
    if not rows:
        # 再等一下 5s
        logger.warning("  表格为空, 再等 5 秒 + 读普通 <table> 兜底")
        try:
            page.locator("table").first.wait_for(state="visible", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        rows = fetcher._read_el_table_rows()
        if not rows:
            # 尝试读原生 <table>
            try:
                tbls = page.query_selector_all("table")
                logger.info(f"  原生 <table> 数量: {len(tbls)}")
                for ti, tbl in enumerate(tbls[:2]):
                    native_rows = page.evaluate("""(t) => {
                        const rows = Array.from(t.querySelectorAll('tr'));
                        return rows.map(tr => {
                            const cells = Array.from(tr.querySelectorAll('th,td'));
                            return cells.map(c => (c.innerText||'').trim());
                        });
                    }""", tbl)
                    logger.info(f"  原生 table#{ti}: {len(native_rows)} 行, 前 3 行 = {native_rows[:3]}")
                    if len(native_rows) > 1:
                        headers = native_rows[0]
                        rows = []
                        for r in native_rows[1:]:
                            row = {}
                            for j, h in enumerate(headers):
                                row[h] = r[j] if j < len(r) else ""
                            rows.append(row)
                        if rows:
                            break
            except Exception as ee:  # noqa: BLE001
                logger.warning(f"  读原生 table 也失败: {ee}")
        logger.info(f"  二次读取后行数 = {len(rows)}")

    if rows:
        headers = list(rows[0].keys())
        logger.info(f"  ✅ 解析到数据! 列名 = {headers}")
        show_n = min(8, len(rows))
        logger.info(f"  表格前 {show_n} 行(脱敏显示):")
        for i, row in enumerate(rows[:show_n], 1):
            masked: dict[str, Any] = {}
            for k, v in row.items():
                if not isinstance(v, str):
                    masked[k] = v
                    continue
                low = k.lower()
                vv = v.strip()
                if any(s in low for s in ("手机", "电话", "mobile", "phone", "身份证", "pass", "密码", "mail")) and len(vv) > 4:
                    masked[k] = vv[:2] + "*" * (len(vv) - 4) + vv[-2:]
                else:
                    masked[k] = vv
            logger.info(f"    [{i}] {json.dumps(masked, ensure_ascii=False)}")

        result_path = DEBUG_DIR / "scrm_operator_parsed_rows_sample.json"
        result_path.write_text(
            json.dumps(
                [
                    {k: (
                        (v[:2] + "*" * max(0, len(v) - 4) + v[-2:])
                        if isinstance(v, str) and len(v) > 4 and any(
                            s in k.lower() for s in ("手机", "电话", "mobile", "phone", "身份证", "pass", "密码", "mail")
                        )
                        else v
                    ) for k, v in r.items()}
                    for r in rows
                ],
                ensure_ascii=False, indent=2, default=str,
            ),
            encoding="utf-8",
        )
        logger.info(f"  ✅ 完整解析结果 JSON(脱敏) 已保存: {result_path} (共 {len(rows)} 行)")
    else:
        logger.warning("  ⚠️  最终还是没读到表格数据。")
        logger.warning("     可能原因:1) 账号权限不足看不到任何操作员;2) 入口路径不对;3) 表格是 div 模拟的非 el-table/非 <table>")
        # dump 更完整的 body 文本
        try:
            txt = page.locator("body").inner_text()
            (DEBUG_DIR / "09_operator_page_full_body_text.txt").write_text(txt, encoding="utf-8")
            snippet = "\n".join([ln for ln in txt.splitlines() if ln.strip()][:80])
            logger.info(f"     页面 body 前 80 行非空片段已保存,前 80 行:\n{snippet}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"     读 body 失败: {e}")
        # 保存 HTML
        try:
            (DEBUG_DIR / "09_operator_page.html").write_text(page.content(), encoding="utf-8")
            logger.info(f"     页面 HTML 已保存到: {DEBUG_DIR / '09_operator_page.html'}")
        except Exception:
            pass

    logger.info("")
    logger.info("=" * 72)
    logger.info("🎉 流程全部执行完毕。所有截图/日志/解析结果:")
    logger.info(f"   {DEBUG_DIR}")
    logger.info(f"   run.log = 本脚本完整日志(含 fetcher 内部日志)")
    logger.info(f"   01~09_*.png = 关键步骤全屏截图")
    logger.info(f"   scrm_operator_parsed_rows_sample.json = 表格解析结果(脱敏, 如有)")
    logger.info("=" * 72)

finally:
    fetcher.close()
