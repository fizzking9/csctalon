#!/usr/bin/env python3
"""验证 scrm_web_fetcher fetch_operators / fetch_sessions 解析 人员信息 / 聊天记录 页面表格.

使用 DB 最新配置(base_url + options.web_username/web_password + 正确 entry_path).
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
log = logging.getLogger("verify_fetch")

from app.core.crypto import decrypt_secret
from app.core.database import SessionLocal
from app.models.sync_source_config import SyncSourceConfig
from app.services.scrm_web_fetcher import ScrmWebFetcher
from sqlalchemy import select


def build_fetcher(data_type: str) -> ScrmWebFetcher:
    with SessionLocal() as db:
        row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
        assert row is not None, f"没找到 data_type={data_type!r}"
        base_url = row.base_url.rstrip("/")
        opts = dict(row.options or {})
        # 优先 options.web_* (Playwright); decrypt_secret 自带 fallback:密文解,明文原样返回
        wu = opts.pop("web_username", None)
        wp = opts.pop("web_password", None)
        uname = decrypt_secret(wu) if wu else decrypt_secret(row.username)
        pwd = decrypt_secret(wp) if wp else decrypt_secret(row.password)
    log.info(f"[{data_type}] user={uname[:3]}***, base_url={base_url}")
    return ScrmWebFetcher(base_url=base_url, username=uname, password=pwd,
                          options=opts, logger=log, captcha_max_retries=12)


# ========== 1. 抓 人员信息 (scrm_operator) ==========
print("\n" + "=" * 80)
print("  [1/3] 抓人员信息 (scrm_operator / personalityManagement)")
print("=" * 80)
fet_op = build_fetcher("scrm_operator")
try:
    fet_op.login()
    op_rows = fet_op.fetch_operators()
    print(f"✅ fetch_operators() 共解析出 {len(op_rows)} 行")
    for i, row in enumerate(op_rows[:5]):
        print(f"  [行#{i}] keys={list(row.keys())[:10]}...{list(row.keys())[-3:] if len(row) > 10 else ''}")
        short = {k: (v[:40] + "...") if isinstance(v, str) and len(v) > 40 else v for k, v in row.items()}
        print(json.dumps(short, ensure_ascii=False, indent=4))
finally:
    fet_op.close()

# ========== 2. 抓 聊天记录 (scrm_session_private / chatRecord) ==========
print("\n" + "=" * 80)
print("  [2/3] 抓聊天记录 (scrm_sessions / chatRecord, 最近 2 天)")
print("=" * 80)
end = date.today()
start = end - timedelta(days=1)
fet_ss = build_fetcher("scrm_session_private")
try:
    fet_ss.login()
    ss_rows = fet_ss.fetch_sessions(start, end)
    print(f"✅ fetch_sessions({start}~{end}) 共解析出 {len(ss_rows)} 行")
    for i, row in enumerate(ss_rows[:5]):
        print(f"  [行#{i}] keys={list(row.keys())[:10]}...{list(row.keys())[-3:] if len(row) > 10 else ''}")
        short = {k: (v[:40] + "...") if isinstance(v, str) and len(v) > 40 else v for k, v in row.items()}
        print(json.dumps(short, ensure_ascii=False, indent=4))
finally:
    fet_ss.close()

# ========== 3. 抓 接待工作统计 (scrm_work_stats / ecCountWork) ==========
print("\n" + "=" * 80)
print("  [3/3] 抓接待工作统计 (scrm_work_stats / ecCountWork, 最近 2 天)")
print("=" * 80)
fet_st = build_fetcher("scrm_work_stats")
try:
    fet_st.login()
    # 复用 fetch_sessions 套路 (填日期 + 查询 + 读表格)
    from app.services.scrm_web_fetcher import ScrmWebFetcher as _SF
    _page = fet_st._page
    assert _page is not None
    entry = fet_st._selector("web_stats_entry_path") or (fet_st.options or {}).get("web_stats_entry_path")
    _page.goto(fet_st._full_url(entry), wait_until="domcontentloaded")
    _page.wait_for_timeout(2000)
    fet_st._fill_el_datetime_range(start, end)
    fet_st._click_query_if_present()
    st_rows = fet_st._read_el_table_rows()
    print(f"✅ 接待工作统计 共解析出 {len(st_rows)} 行")
    for i, row in enumerate(st_rows[:5]):
        print(f"  [行#{i}] keys={list(row.keys())[:10]}...{list(row.keys())[-3:] if len(row) > 10 else ''}")
        short = {k: (v[:40] + "...") if isinstance(v, str) and len(v) > 40 else v for k, v in row.items()}
        print(json.dumps(short, ensure_ascii=False, indent=4))
finally:
    fet_st.close()

print("\n\n✅ 全部抓取完成! 如字段名是中文表头,下一步只需在 scrm_sync_service 里加中文字段名 → 模型字段名映射即可 upsert.")
