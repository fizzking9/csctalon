#!/usr/bin/env python3
"""独立脚本:直接调 BmsFetcher.fetch_one 跑闪猫退款 2026-08-05(绕开任务管理器互斥/排队,立即验证)。

用法(容器内):
  cd /app && python scripts/run_shanmao_refund_debug.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# 让脚本能 import app.*（脚本在 /app/scripts 下,/app 是 sys.path 根但不一定被加进去）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ENV_FOR_DYNACONF", "production")

from app.core.config import settings  # noqa: E402
from app.services.bms_fetcher import (  # noqa: E402
    BMS_DATA_SOURCES,
    BmsFetcher,
    FetchResult,
    _storage_state_path,
)


async def main():
    from app.core.database import SessionLocal
    from app.services.bms_sync import get_sync_source_config  # type: ignore

    data_type = "shanmao_refund"
    start = datetime(2026, 8, 5, 0, 0, 0)
    end = datetime(2026, 8, 5, 23, 59, 59)

    # 1. 拿配置（BMS_FETCH_CONFIG 默认种子 + DB override 合并）
    #    为了简单这里直接用默认种子 BMS_DATA_SOURCES[shanmao_refund]
    #    (实际 bms_sync.py 里会合并 DB 配置,但时间格式/按钮这些都在 BMS_DATA_SOURCES 种子里)
    base_cfg = dict(BMS_DATA_SOURCES[data_type])

    # 从 DB 拿 加解密后的 用户名密码
    try:
        db = SessionLocal()
        try:
            db_cfg = await get_sync_source_config(db) if asyncio.iscoroutinefunction(get_sync_source_config) else get_sync_source_config(db)
        finally:
            db.close()
    except Exception as e:
        print(f"[WARN] 读 DB sync_source_config 失败,用 seed 默认(无账号密码): {e}")
        db_cfg = {}
    # db_cfg 结构: {data_type: {username, password, ...}}
    this_cfg_from_db = (db_cfg or {}).get(data_type, {})
    base_cfg["username"] = this_cfg_from_db.get("username")
    base_cfg["password"] = this_cfg_from_db.get("password")
    # 凭证只从 DB sync_source_configs / 环境变量读取,严禁硬编码默认值
    # (2026-08-20 审计 C-1: 原 os.environ.get("SHANMAO_PASS", "<真实密码>") 真实密码硬编码已删除)
    if not base_cfg["username"]:
        base_cfg["username"] = os.environ.get("SHANMAO_USER", "")
    if not base_cfg["password"]:
        base_cfg["password"] = os.environ.get("SHANMAO_PASS", "")
    if not base_cfg["username"] or not base_cfg["password"]:
        print(
            "[FATAL] 缺少杉猫平台凭证: 请先在 sync_source_configs 配置或设置环境变量 SHANMAO_USER/SHANMAO_PASS",
            file=sys.stderr,
        )
        raise SystemExit(2)

    print(f"[配置] data_type={data_type}")
    print(f"[配置] time_format={base_cfg.get('time_format','<default>')}")
    print(f"[配置] base_url={base_cfg.get('base_url')} page_path={base_cfg.get('page_path')}")
    print(f"[配置] storage_state={_storage_state_path(data_type)} exists={_storage_state_path(data_type).exists()}")
    print(f"[时间] {start} ~ {end}")
    print()

    # 2. 调 fetch_one
    fetcher = BmsFetcher()
    try:
        result = await fetcher.fetch_one(
            data_type=data_type,
            start_date=start,
            end_date=end,
            config=base_cfg,
            timeout=600,  # 给 10 分钟
        )
    finally:
        try:
            if fetcher._playwright is not None:
                await fetcher._playwright.stop()
        except Exception:
            pass

    # 3. 打印结果
    print()
    print("=========== FetchResult ===========")
    print(f"success  = {result.success}")
    print(f"error    = {result.error}")
    print(f"filename = {result.filename}")
    print(f"file_path= {result.file_path}")
    print(f"total_rows={result.total_rows}")
    if result.file_path:
        fp = Path(settings.UPLOAD_DIR) / result.file_path
        if fp.exists():
            size_kb = fp.stat().st_size / 1024
            print(f"文件真实存在 ✅  size={size_kb:.1f}KB  path={fp}")
            # 读一下 列名+行数
            try:
                import pandas as pd
                if fp.suffix.lower() in (".xlsx", ".xls"):
                    df = pd.read_excel(fp, dtype=str)
                else:
                    df = pd.read_csv(fp, dtype=str)
                print(f"  行数={len(df)}  列数={len(df.columns)}")
                print(f"  列名={df.columns.tolist()[:20]}")
                if len(df) > 0 and "退费单号" in df.columns:
                    df["_prefix"] = df["退费单号"].astype(str).str[:10]
                    print(f"  退费单号前缀 TOP10:")
                    print(df["_prefix"].value_counts().head(10))
                    today_cnt = len(df[df["_prefix"] == "TF20260805"])
                    print(f"  TF20260805 开头的行 = {today_cnt}")
                print("  前 3 行:")
                for i in range(min(3, len(df))):
                    print(f"   [{i}] {df.iloc[i].head(10).to_dict()}")
            except Exception as e:
                print(f"  读文件失败: {e}")


if __name__ == "__main__":
    asyncio.run(main())
