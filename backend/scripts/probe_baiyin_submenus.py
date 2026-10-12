#!/usr/bin/env python3
"""精准探测 SCRM 管理后台:客服管理(操作员) / 聊天记录(会话) / 敏感词监控 的真实 URL 与表格结构."""
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
log = logging.getLogger("probe_sub")

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

shot_dir = Path(tempfile.gettempdir())
fetcher = ScrmWebFetcher(base_url=base_url, username=uname, password=pwd,
                          options=opts, logger=log, captcha_max_retries=12)
results = {}
try:
    fetcher.login()
    page = fetcher._page
    assert page is not None
    log.info(f"登录成功,URL={page.url}")

    probes = [
        ("客服管理", "客服管理", "operators"),
        ("聊天记录", "聊天记录", "sessions"),
        ("敏感词监控", "敏感词监控", "sensitive"),
        ("工作量报表", "工作量报表", "workload"),
        ("新工作统计", "新工作统计", "stats"),
        ("企业管理", "企业管理", "enterprise"),
        ("商家管理", "商家管理", "merchant"),
    ]

    for label, text, key in probes:
        try:
            log.info(f"==== {label} ====")
            el = page.get_by_text(text).first
            if el.count() == 0:
                log.warning(f"  没有找到文本为 {text!r} 的元素")
                continue
            # scroll into view
            try:
                el.scroll_into_view_if_needed()
            except Exception:
                pass
            el.click()
            page.wait_for_timeout(3500)
            cur_url = page.url
            log.info(f"  URL = {cur_url}")
            # 截图
            shot = shot_dir / f"baiyin_{key}.png"
            page.screenshot(path=str(shot), full_page=True)
            log.info(f"  截图: {shot}")
            # 统计表格 / 行数
            rows = page.locator(".el-table__row, tbody tr").count()
            tables = page.locator("table, .el-table").count()
            log.info(f"  .el-table 个数={tables}, 数据行数={rows}")
            # 如果有表格,打印第一行的文本
            first_row = None
            try:
                if rows > 0:
                    first_row = page.locator(".el-table__row, tbody tr").first.inner_text()
                    if first_row and len(first_row) > 500:
                        first_row = first_row[:500] + "..."
            except Exception as e:
                log.warning(f"  取第一行失败: {e}")
            # 保存 body HTML (前 30k)
            try:
                html_path = shot_dir / f"baiyin_{key}.html"
                html_path.write_text(page.inner_html("body")[:30000], encoding="utf-8")
                log.info(f"  HTML: {html_path}")
            except Exception:
                pass
            results[key] = {
                "label": label,
                "url": cur_url,
                "tables": tables,
                "rows": rows,
                "first_row": first_row,
            }
        except Exception as e:
            log.warning(f"  {label} 异常: {e}")
            results[key] = {"label": label, "error": str(e)}

    print("\n\n===== 探测结果 =====")
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
finally:
    fetcher.close()
