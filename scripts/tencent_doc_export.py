#!/usr/bin/env python3
"""腾讯共享文档导出下载器 —— 用登录态打开任意共享表 → 导出为 Excel(xlsx)

背景:
  共享表格(腾讯 docs.qq.com/sheet/...) 未登录只能读腾讯私有压缩格式(canvas +
  protobuf), 无法直接取数; 用账号登录后可通过 UI "更多 → 导出为 → 本地Excel
  表格 (.xlsx)" 导出标准 xlsx, 再走 _poc_shared_doc_reader.py 解析。

依赖登录态:
  先跑 tencent_doc_login.py 生成 data/browser_states/tencent_doc.json
  (存于 BROWSER_STATE_DIR, 与 BMS/SCRM 同模式)。

已验证(2026-09-07):
  升级投诉登记表(16 Sheet / 4565 行)导出成功, 文件名 "升级投诉登记表.xlsx"

用法:
  cd backend
  ./.venv/bin/python ../scripts/tencent_doc_export.py \
      --url "https://docs.qq.com/sheet/DWnRPSmpKU29uSk9T" \
      --out /tmp/export.xlsx
  # 可选 --sheet 指定仅导出当前工作表为 csv(腾讯菜单的"本地CSV")
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

from app.core.config import settings

STATE_PATH = Path(settings.BROWSER_STATE_DIR) / "tencent_doc.json"

# 顶部"更多"菜单按钮的坐标(Share 左侧的 ⋯ 图标)。
# ⚠️ 腾讯前端可能调整布局, 若失效需重新探测(见仓库记忆/调试记录)。
MENU_X, MENU_Y = 1361, 15


def export_sheet_xlsx(url: str, out_path: str, timeout_ms: int = 90000) -> str:
    """打开共享表 → 更多菜单 → 导出为 → 本地Excel(.xlsx) → 保存到 out_path

    返回下载的建议文件名。需要已登录(tencent_doc.json)。
    """
    from playwright.sync_api import sync_playwright

    if not STATE_PATH.exists():
        raise FileNotFoundError(
            f"未找到登录态 {STATE_PATH}。请先运行 scripts/tencent_doc_login.py 扫码登录"
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
            page.wait_for_timeout(8000)  # 等 SPA + 表格渲染

            # 点击右上角 ⋯(更多)菜单
            print("  打开「更多」菜单…")
            page.mouse.click(MENU_X, MENU_Y)
            page.wait_for_timeout(1500)

            # hover「导出为」展开子菜单, 再点「本地Excel表格 (.xlsx)」
            print("  展开「导出为」→ 点击「本地Excel表格 (.xlsx)」…")
            page.hover("text=导出为")
            page.wait_for_timeout(800)
            with page.expect_download(timeout=timeout_ms) as dl_info:
                page.click("text=本地Excel表格 (.xlsx)")
            dl = dl_info.value
            dl.save_as(out_path)
            print(f"✅ 导出成功: {out_path}")
            print(f"   原始文件名: {dl.suggested_filename}")
            return dl.suggested_filename
        finally:
            browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="腾讯共享文档导出 xlsx")
    ap.add_argument("--url", required=True, help="共享表链接 https://docs.qq.com/sheet/...")
    ap.add_argument("--out", required=True, help="输出 xlsx 路径")
    args = ap.parse_args()
    export_sheet_xlsx(args.url, args.out)
    print("完成。可用 _poc_shared_doc_reader.py --file <out> 查看内容。")
