#!/usr/bin/env python3
"""探测 SCRM 管理后台左侧菜单,找出「操作员/客服账号」和「会话」所在页面路径."""
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
log = logging.getLogger("probe_menu")

from app.core.crypto import decrypt_secret
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from app.services.scrm_web_fetcher import ScrmWebFetcher
from sqlalchemy import select

with SessionLocal() as db:
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == "scrm_operator"))
    base_url = row.base_url.rstrip("/")
    uname = decrypt_secret(row.username)
    pwd = decrypt_secret(row.password)
    opts = row.options or {}
log.info(f"base_url={base_url}, user={uname[:3]}***")

shot_dir = Path(tempfile.gettempdir())
fetcher = ScrmWebFetcher(base_url=base_url, username=uname, password=pwd,
                          options=opts, logger=log, captcha_max_retries=12)
try:
    fetcher.login()
    page = fetcher._page
    assert page is not None
    log.info(f"已登录,当前 URL={page.url}")
    # 保存首页
    p = shot_dir / "scrm_home.png"
    page.screenshot(path=str(p), full_page=True)
    log.info(f"首页截图已保存: {p}")

    # 打印左侧所有菜单文字和 href
    log.info("==== 左侧菜单(全部可见元素) ====")
    try:
        side_texts = page.locator("aside .menu a, aside a, .el-menu a, .el-menu-item, .el-submenu__title").all_inner_texts()
        for t in side_texts:
            s = t.strip()
            if s:
                log.info(f"  MENU: {s!r}")
    except Exception as e:
        log.warning(f"打印菜单失败: {e}")

    # 尝试可能的入口 (按菜单名)
    candidates = [
        ("客服中心", "客服中心"),
        ("企业管理", "企业管理"),
        ("商家管理", "商家管理"),
        ("数据中心", "数据中心"),
        ("客户运营", "客户运营"),
    ]
    for name, text in candidates:
        try:
            log.info(f"--- 点击菜单: {name} ---")
            el = page.get_by_text(text).first
            if el.count() == 0:
                log.warning(f"  没找到 {text} 按钮")
                continue
            el.click()
            page.wait_for_timeout(3500)
            cur = page.url
            log.info(f"  跳转 URL = {cur}")
            p = shot_dir / f"scrm_{name}.png"
            page.screenshot(path=str(p), full_page=True)
            log.info(f"  截图: {p}")
            # 检查是否有表格
            table_count = page.locator("table, .el-table, table.el-table__body").count()
            rows_count = page.locator(".el-table__row, tbody tr").count()
            log.info(f"  表格元素数: {table_count}, 数据行数(含表头): {rows_count}")
            # 把页面 HTML 前 2000 字也存一份
            try:
                body_html = page.inner_html("body")
                hp = shot_dir / f"scrm_{name}.html"
                hp.write_text(body_html[:20000], encoding="utf-8")
                log.info(f"  页面 HTML 片段: {hp}")
            except Exception as e2:
                log.warning(f"  存 HTML 失败: {e2}")
        except Exception as e:
            log.warning(f"  {name} 出错: {e}")

    log.info("✅ 探测完成! 可以查看 /tmp/scrm_*.png / .html 找出真正的入口")
finally:
    fetcher.close()
