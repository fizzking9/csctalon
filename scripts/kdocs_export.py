#!/usr/bin/env python3
"""金山文档(kdocs.cn)共享表导出下载器 —— 用登录态导出为 Excel(xlsx)

背景:
  金山共享表格(如 https://www.kdocs.cn/l/...) 未登录只能看 canvas, 无法取数;
  登录后可通过 左上角菜单 → 导出为 → Excel(.xlsx) 导出标准 xlsx。
  本脚本复用 scripts/kdocs_login.py 生成的登录态(data/browser_states/kdocs.json)。

实测路径(2026-09-08, 商户号手机退款处理登记):
  - 打开共享链接 → 左上角菜单图标(x≈95,y≈15) → 菜单出现 另存/下载/导出为
  - 点"导出为"(精确文本, 排除导出为PDF/图片/多维表格) → 子菜单"导出为 Excel(.xlsx)"
  - page.expect_download 捕获 → 保存 xlsx
  - 实测导出 3 Sheet, 其中"登记明细"3117行/14列(登记日期/设备号/订单号/商户ID/...)

用法:
  cd backend && ./.venv/bin/python ../scripts/kdocs_export.py \
      --url "https://www.kdocs.cn/l/cbZIHtt6obyf" --out /tmp/kdocs.xlsx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

from app.core.config import settings

STATE_PATH = Path(settings.BROWSER_STATE_DIR) / "kdocs.json"


def export_sheet_xlsx(url: str, out_path: str, timeout_ms: int = 90000) -> str:
    """打开金山共享表 → 菜单 → 导出为Excel → 保存 xlsx. 返回建议文件名"""
    from playwright.sync_api import sync_playwright

    if not STATE_PATH.exists():
        raise FileNotFoundError(
            f"未找到登录态 {STATE_PATH}。请先运行 scripts/kdocs_login.py 扫码登录"
        )

    out_path = str(Path(out_path).resolve())
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            storage_state=str(STATE_PATH),
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        try:
            print(f"📖 打开: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(10000)

            # 1. 点左上角菜单图标(kd-icon-menu, x≈95)
            print("  打开左上角文件菜单…")
            page.mouse.click(95, 15)
            page.wait_for_timeout(1500)

            # 2. 精确定位"导出为"(排除"导出为PDF/图片/多维表格"), 点击展开子菜单
            clicked = page.evaluate("""() => {
                const all=[...document.querySelectorAll('*')];
                const targets = all.filter(e => (e.innerText||'').trim() === '导出为' && e.children.length===0);
                if (!targets.length) return false;
                targets[0].click();
                return true;
            }""")
            if not clicked:
                raise RuntimeError("菜单中未找到'导出为'入口(前端可能变动)")
            page.wait_for_timeout(1500)

            # 3. 点"导出为 Excel(.xlsx)" 并捕获下载
            print("  点击「导出为 Excel(.xlsx)」…")
            with page.expect_download(timeout=timeout_ms) as dl_info:
                page.click("text=导出为 Excel(.xlsx)", timeout=8000)
            dl = dl_info.value
            dl.save_as(out_path)
            print(f"✅ 导出成功: {out_path}")
            print(f"   原始文件名: {dl.suggested_filename}")
            return dl.suggested_filename
        finally:
            browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="金山共享文档导出 xlsx")
    ap.add_argument("--url", required=True, help="共享表链接 https://www.kdocs.cn/l/...")
    ap.add_argument("--out", required=True, help="输出 xlsx 路径")
    args = ap.parse_args()
    export_sheet_xlsx(args.url, args.out)
    print("完成。可用 _poc_shared_doc_reader.py --file <out> 查看内容。")
