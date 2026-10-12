#!/usr/bin/env python3
"""探测 SCRM 管理后台 4 个用户提供的真实页面:
  1. 接待工作(统计) /datastats/customer/ecCountWork
  2. 聊天记录(偶尔查询) /tuoke/customer/chatRecord
  3. 人员信息(偶尔同步) /wxWork/personalityManagement
  4. 客户信息(偶尔同步) /customer/list
"""
from __future__ import annotations

import json
import sys
import logging
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logging.getLogger("ddddocr").setLevel(logging.ERROR)
logging.getLogger("PIL").setLevel(logging.WARNING)
log = logging.getLogger("probe_urls")

from app.core.crypto import decrypt_secret
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from app.services.scrm_web_fetcher import ScrmWebFetcher
from sqlalchemy import select

# ---------- 读 DB 配置 ----------
with SessionLocal() as db:
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "scrm_operator"))
    base_url = row.base_url.rstrip("/")
    # 双账号密码体系: 优先 options.web_username/web_password(Playwright 网页登录), 否则 fallback 列里的
    opts = row.options or {}
    uname = opts.get("web_username") or decrypt_secret(row.username)
    pwd = opts.get("web_password") or decrypt_secret(row.password)
    opts = {k: v for k, v in opts.items() if k not in ("web_username", "web_password")}
log.info(f"base_url={base_url}, user={uname[:3]}***")

# ---------- 4 个目标 URL ----------
TARGETS = [
    ("ecCountWork 接待工作统计", "/datastats/customer/ecCountWork", "stats"),
    ("chatRecord 聊天记录查询", "/tuoke/customer/chatRecord", "chat"),
    ("personalityManagement 人员信息", "/wxWork/personalityManagement", "staff"),
    ("customer/list 客户列表", "/customer/list", "cust"),
]

shot_dir = Path(tempfile.gettempdir())
fetcher = ScrmWebFetcher(base_url=base_url, username=uname, password=pwd,
                          options=opts, logger=log, captcha_max_retries=12)
results = {}
try:
    fetcher.login()
    page = fetcher._page
    assert page is not None
    log.info(f"✅ 登录成功,当前 URL={page.url}")

    for label, path, key in TARGETS:
        log.info(f"\n{'=' * 60}\n  探测: {label}\n  URL path: {path}\n{'=' * 60}")
        url = f"{base_url}{path}"
        try:
            # 用 goto 打开,等待 SPA 加载
            page.goto(url, wait_until="domcontentloaded")
            # 额外等待 SPA 组件挂载
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(4000)

            cur_url = page.url
            log.info(f"  跳转后 URL = {cur_url}")
            # 是否重定向回登录页?
            redirected = "/login" in cur_url.lower() or "未登录" in (page.title() or "")
            if redirected:
                log.error("  ❌ 重定向回了登录页(或未登录),需要额外登录检查 cookie")
            # 截图 + 保存 HTML
            shot = shot_dir / f"baiyin_{key}.png"
            page.screenshot(path=str(shot), full_page=True)
            log.info(f"  截图: {shot}")
            html_path = shot_dir / f"baiyin_{key}.html"
            try:
                body_html = page.inner_html("body")
                html_path.write_text(body_html[:80000], encoding="utf-8")
                log.info(f"  HTML(前8万字): {html_path}")
            except Exception as e:
                log.warning(f"  存 HTML 失败: {e}")

            # ---- 检查表格/可解析元素 ----
            info = {"label": label, "path": path, "final_url": cur_url, "redirected": redirected}
            # 1. 常见 Element UI / Ant Design 表格选择器
            for sel_name, sel in [
                ("el-table", ".el-table table.el-table__body"),
                ("el-table__row", ".el-table__row"),
                ("ant-table", ".ant-table tbody"),
                ("tbody_tr", "tbody tr"),
                ("table", "table"),
            ]:
                try:
                    count = page.locator(sel).count()
                    info[f"count_{sel_name}"] = count
                except Exception:
                    info[f"count_{sel_name}"] = -1
            # 2. 如果有 el-table__row,打印前 3 行完整文本
            rows_text = []
            try:
                n_rows = info.get("count_el-table__row") or info.get("count_tbody_tr") or 0
                if n_rows and n_rows > 0:
                    n = min(n_rows, 3)
                    for i in range(n):
                        row_el = page.locator(".el-table__row, tbody tr").nth(i)
                        txt = row_el.inner_text()
                        rows_text.append(txt[:1000])
            except Exception as e:
                rows_text.append(f"<取行失败: {e}>")
            info["sample_rows"] = rows_text

            # 3. 打印页面所有按钮/日期选择器/下拉,帮助推断筛选条件
            info["filter_hints"] = {}
            for hint_sel, hint_name in [
                ("input[placeholder*='日期']", "日期输入框"),
                (".el-date-editor", "日期选择器"),
                (".el-select", "下拉选择"),
                ("button.el-button, button", "按钮(前10个)"),
                ("input", "所有 input(前10个)"),
            ]:
                try:
                    loc = page.locator(hint_sel)
                    count = loc.count()
                    items = []
                    for i in range(min(count, 10)):
                        try:
                            el = loc.nth(i)
                            ph = el.get_attribute("placeholder") or ""
                            txt = (el.inner_text() or "").strip()
                            val = el.get_attribute("value") or ""
                            name = el.get_attribute("name") or ""
                            items.append({
                                "i": i,
                                "name": name or None,
                                "placeholder": ph or None,
                                "value": val or None,
                                "text": txt[:50] if txt else None,
                            })
                        except Exception:
                            items.append({"i": i, "err": True})
                    info["filter_hints"][hint_name] = {"count": count, "samples": items}
                except Exception:
                    info["filter_hints"][hint_name] = {"err": True}

            results[key] = info
            log.info(f"  ✓ {label} 探测完毕,统计:")
            log.info(f"    - 表格相关计数: el-table={info.get('count_el-table')}, el-table__row={info.get('count_el-table__row')}, tbody_tr={info.get('count_tbody_tr')}")
            log.info(f"    - 采样行数: {len(info.get('sample_rows', []))} 行")
        except Exception as e:
            log.error(f"  ❌ {label} 探测异常: {e}")
            results[key] = {"label": label, "path": path, "error": str(e)}

    print("\n\n================ 最终探测结果(简版) ================\n")
    for k, v in results.items():
        label = v.get("label")
        if "error" in v:
            print(f"  ❌ {label}: 异常 {v['error']}")
            continue
        redirected = v.get("redirected")
        print(f"  {'✅' if not redirected else '❌'} {label} ({k})")
        print(f"    路径: {v.get('path')}  最终URL: {v.get('final_url')}")
        print(f"    表格: el-table={v.get('count_el-table')}  el-table__row={v.get('count_el-table__row')}  tbody_tr={v.get('count_tbody_tr')}")
        samples = v.get("sample_rows") or []
        if samples:
            print(f"    采样行({len(samples)}):")
            for i, s in enumerate(samples):
                print(f"      [{i}]: {s[:200]}{'...' if len(s) > 200 else ''}")
        print()

    # 完整 JSON 存文件
    result_json_path = shot_dir / "baiyin_probe_result.json"
    result_json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"完整结果(JSON)已保存: {result_json_path}")
finally:
    fetcher.close()
