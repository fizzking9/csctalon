#!/usr/bin/env python3
"""复用同一个 ScrmWebFetcher(同一浏览器+同一登录态) 验证:
  1. 人员信息 (scrm_operator / personalityManagement)
  2. 聊天记录 (chatRecord, 最近 2 天)
  3. 接待工作统计 (ecCountWork, 最近 2 天)
"""
from __future__ import annotations

import json
import sys
import logging
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logging.getLogger("ddddocr").setLevel(logging.ERROR)
logging.getLogger("PIL").setLevel(logging.WARNING)
log = logging.getLogger("verify_fetch_v2")

from app.core.crypto import decrypt_secret
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from app.services.scrm_web_fetcher import ScrmWebFetcher
from sqlalchemy import select


def build_single_fetcher(data_type: str) -> ScrmWebFetcher:
    with SessionLocal() as db:
        row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
        assert row is not None, f"没找到 data_type={data_type!r}"
        base_url = row.base_url.rstrip("/")
        opts = dict(row.options or {})
        wu = opts.pop("web_username", None)
        wp = opts.pop("web_password", None)
        uname = decrypt_secret(wu) if wu else decrypt_secret(row.username)
        pwd = decrypt_secret(wp) if wp else decrypt_secret(row.password)
    log.info(f"[build] user={uname[:3]}***, base_url={base_url}")
    return ScrmWebFetcher(base_url=base_url, username=uname, password=pwd,
                          options=opts, logger=log, captcha_max_retries=15)


def print_rows(title: str, rows: list[dict], top_n: int = 5) -> None:
    print("\n" + "=" * 80)
    print(f"  {title}  →  共解析 {len(rows)} 行")
    print("=" * 80)
    for i, row in enumerate(rows[:top_n]):
        print(f"  [行#{i}]")
        short = {k: (v[:50] + "..." if isinstance(v, str) and len(v) > 50 else v)
                 for k, v in row.items()}
        print(json.dumps(short, ensure_ascii=False, indent=4))


end = date.today()
start = end - timedelta(days=1)

fetcher = build_single_fetcher("scrm_operator")
try:
    # ========= 1. 人员信息 =========
    print_rows("1. 人员信息 (scrm_operator)", fetcher.fetch_operators())

    # ========= 2. 聊天记录 =========
    print_rows(f"2. 聊天记录 (chatRecord, {start} ~ {end})",
               fetcher.fetch_sessions(start, end))

    # ========= 3. 接待工作统计 =========
    log.info("[3/3] 抓接待工作统计 (ecCountWork)")
    page = fetcher._page
    assert page is not None
    opts = fetcher.options or {}
    entry = opts.get("web_stats_entry_path") or "/datastats/customer/ecCountWork"
    page.goto(fetcher._full_url(entry), wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    fetcher._fill_el_datetime_range(start, end)
    fetcher._click_query_if_present()
    print_rows(f"3. 接待工作统计 (ecCountWork, {start} ~ {end})",
               fetcher._read_el_table_rows())

    # ========= 4. 客户列表 (可选) =========
    log.info("[4/4] 抓客户列表 (customer/list, 取默认第一页)")
    entry2 = opts.get("web_customer_entry_path") or "/customer/list"
    page.goto(fetcher._full_url(entry2), wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    fetcher._click_query_if_present()
    print_rows("4. 客户列表 (customer/list, 默认第一页)",
               fetcher._read_el_table_rows())

finally:
    fetcher.close()

print("\n\n✅ 全部抓取完成!")
print("建议: 后续在 scrm_sync_service 里加一个 「中文字段名 → 模型字段名」的映射函数即可 upsert.")
