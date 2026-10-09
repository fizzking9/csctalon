#!/usr/bin/env python3
"""bms_order 历史订单回填工具 (#293, 2026-10-09) — 默认预览, --apply 才抓取入库

## 背景
bms_orders 仅覆盖 2026-09-06 起(采集上线后的每日增量), 历史订单从未抓取。
近30天退款 15515 SN 中: 有激活 12892(95% 有 bms 订单) vs 无激活 2623
(99.6% 无 bms 订单, 商户渠道裸档案)。9 月初前激活的设备, 其订单/激活记录
在 BMS 订单系统(pay-manage)中存在但从未采集 → 激活覆盖率封顶 78.7%/83.1%。

用户拍板(2026-10-09): 通过接口调用回填; **BMS 页面时间窗口一次只能跨 1 个月**;
**窗口间必须冷却**(同系统 1 分钟频控)。

## 方案
逐自然月切窗(≤1月/窗), 每窗调用既有同步链路 `sync_all`(BmsFetcher Playwright
导出 → _auto_import 解析 → upsert_bms_orders 幂等入库 → BmsSyncLog manual 记录),
**不新增任何抓取/入库代码**。

## 用法
    cd backend && python ../scripts/backfill_bms_orders.py                      # 预览(默认最近3个完整月)
    cd backend && python ../scripts/backfill_bms_orders.py --start 2025-10 --end 2026-08   # 预览指定月区间
    cd backend && python ../scripts/backfill_bms_orders.py --apply --start 2025-10 --end 2026-08
    cd backend && python ../scripts/backfill_bms_orders.py --apply --months 6 --interval 120

## 安全性
- 默认 dry-run: 只打印逐窗计划与基线统计, 不访问 BMS、不写库
- upsert 幂等: 重复抓取同窗不产生重复行(按订单键 upsert, create_date 为同步锚点)
- 窗口间冷却 --interval 秒(默认 90 > BMS 1 分钟频控); 单窗失败重试 --max-retries 次
- 单窗失败不阻塞后续窗; 结束打印逐窗对账表 + 失败清单
- ⚠️ 避开每日凌晨批次(约 00:00-01:00 北京)运行, 防止与定时同步撞频控
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_bms_orders")

from app.core.database import SessionLocal  # noqa: E402
from app.services.analytics import TZ  # noqa: E402  # 东八区 aware
from app.services.bms_sync import sync_all  # noqa: E402

TRIGGERED_BY = "backfill:#293"


def month_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """按自然月切窗: [月首 00:00:00, 下月首 00:00:00 - 1s], aware 北京时间。

    BMS 页面时间窗口一次只能跨 1 个月(用户实证), 跨月数据自动由相邻两窗覆盖。
    """
    windows: list[tuple[datetime, datetime]] = []
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # end 收敛到其所在月的月首(窗口终点=该月末)
    end_m = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur <= end_m:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)  # 下月1日
        windows.append((cur, nxt - timedelta(seconds=1)))
        cur = nxt
    return windows


def parse_month(s: str) -> datetime:
    """'YYYY-MM' → 该月1日 00:00 (aware 北京)。"""
    return datetime.strptime(s, "%Y-%m").replace(tzinfo=TZ)


def baseline_stats() -> dict:
    """回填前基线: bms_orders 行数/SN数/最小订单时间。"""
    from sqlalchemy import text

    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT count(*), count(DISTINCT device_sn), min(create_date) FROM bms_orders"
        )).one()
        return {"rows": row[0], "sns": row[1], "min_create": row[2]}
    finally:
        db.close()


def run_one_window(start: datetime, end: datetime, max_retries: int) -> dict:
    """单窗同步(含重试): 复用 sync_all(manual), 返回结果 dict。"""
    last: dict | None = None
    for attempt in range(1, max_retries + 1):
        results = asyncio.run(sync_all(
            trigger_type="manual",
            triggered_by=TRIGGERED_BY,
            data_types=["bms_order"],
            time_ranges={"bms_order": (start, end)},
        ))
        r = results[0] if results else {"success": False, "error": "sync_all 无返回"}
        r["attempt"] = attempt
        last = r
        if r.get("success"):
            return r
        logger.warning(f"窗口 {start:%Y-%m} 第{attempt}次失败: {r.get('error')}")
        if attempt < max_retries:
            time.sleep(60)  # 重试前也冷却, 避开频控
    return last or {"success": False, "error": "未知"}


def main() -> None:
    ap = argparse.ArgumentParser(description="bms_order 历史订单回填(#293)")
    ap.add_argument("--start", help="起始月 YYYY-MM(含)")
    ap.add_argument("--end", help="结束月 YYYY-MM(含)")
    ap.add_argument("--months", type=int, default=3, help="未指定 start/end 时: 最近 N 个完整月(不含当前月)")
    ap.add_argument("--apply", action="store_true", help="真跑抓取入库; 缺省 dry-run 预览")
    ap.add_argument("--interval", type=int, default=90, help="窗口间冷却秒数(默认90, BMS 1分钟频控)")
    ap.add_argument("--max-retries", type=int, default=2, help="单窗最大尝试次数(默认2)")
    args = ap.parse_args()

    today = datetime.now(TZ)
    if args.start and args.end:
        start, end = parse_month(args.start), parse_month(args.end)
        if end < start:
            ap.error("--end 不得早于 --start")
    elif args.start or args.end:
        ap.error("--start 与 --end 必须成对给出(或用 --months)")
    else:
        # 最近 N 个完整月: 不含当前月
        first_of_cur = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = first_of_cur - timedelta(seconds=1)
        start = (first_of_cur - timedelta(days=args.months * 31)).replace(day=1)

    windows = month_windows(start, end)
    base = baseline_stats()
    print(f"\n== 基线: bms_orders {base['rows']} 行 / {base['sns']} SN / 最早订单 {base['min_create']}")
    mode = "APPLY(抓取入库)" if args.apply else "DRY-RUN(仅预览)"
    print(f"== 计划[{mode}]: {len(windows)} 个月窗口, 窗间冷却 {args.interval}s\n")
    for w_start, w_end in windows:
        print(f"   {w_start:%Y-%m-%d %H:%M:%S} ~ {w_end:%Y-%m-%d %H:%M:%S}  ({w_start:%Y-%m})")

    if not args.apply:
        print("\n(dry-run 结束, 加 --apply 执行)")
        return

    ok_rows = 0
    failures: list[tuple[str, str]] = []
    print()
    for i, (w_start, w_end) in enumerate(windows, 1):
        print(f"── [{i}/{len(windows)}] {w_start:%Y-%m} 抓取中 ...")
        r = run_one_window(w_start, w_end, args.max_retries)
        label = f"{w_start:%Y-%m}"
        if r.get("success"):
            n = r.get("total_rows") or 0
            ok_rows += n
            empty = " (空窗,正常)" if r.get("empty_result") else ""
            print(f"   ✓ {label}: total={n} ins={r.get('inserted_rows')} "
                  f"upd={r.get('updated_rows')} fail={r.get('failed_rows')}{empty}")
        else:
            failures.append((label, r.get("error") or "未知错误"))
            print(f"   ✗ {label}: {r.get('error')}")
        if i < len(windows):
            time.sleep(args.interval)  # BMS 同系统 1 分钟频控 → 窗间冷却

    after = baseline_stats()
    print("\n== 回填完成对账 ==")
    print(f"   成功窗合计行数: {ok_rows} | 失败窗: {len(failures)}")
    for label, err in failures:
        print(f"   ✗ {label}: {err}")
    print(f"   bms_orders: {base['rows']} → {after['rows']} 行 (+{after['rows'] - base['rows']})")
    print(f"   SN: {base['sns']} → {after['sns']} (+{after['sns'] - base['sns']})")
    print(f"   最早订单: {base['min_create']} → {after['min_create']}")
    if failures:
        print("\n⚠️ 存在失败窗, 可用同参数重跑(幂等, 已入库月份不会重复)")
        sys.exit(1)


if __name__ == "__main__":
    main()
