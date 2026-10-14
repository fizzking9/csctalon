# -*- coding: utf-8 -*-
"""#293 设备档案 SN 补全 —— 手动 CLI 入口（与调度器 4:00 档共用同一服务）。

## 用途
devices 表缺档案字段（设备类型/当前套餐/商户/激活时间）的设备，逐 SN 调 BMS
`findPageList` 精确查询并**只填空值**回填。幂等：已完整设备自动出队，重复运行无害。

## 与日批的关系
device_profile 日批导出（10 万行截断）负责新设备建档 + ICCID/批次上下文；
本 CLI/调度档是**存量漏采兜底**，两者互补。

## 用法（生产容器内）
    # 常规：每次补 2 万个（约 25 分钟）
    docker compose exec backend python3 scripts/backfill_device_profile_by_sn.py

    # 首次存量清零：不限量（约 56 万 SN, 15-19 小时, 建议 nohup/夜间跑）
    docker compose exec backend python3 scripts/backfill_device_profile_by_sn.py --limit 0

    # 指定并发（默认 10）
    docker compose exec backend python3 scripts/backfill_device_profile_by_sn.py --concurrency 10

`--limit 0` 表示不设上限（一次跑完所有缺字段设备）。
结果写 bms_sync_logs(data_type='device_profile_enrich', trigger_type='manual')。
"""
import argparse
import asyncio

from scripts._bootstrap import bootstrap, real_db_requested


def main() -> None:
    parser = argparse.ArgumentParser(description="设备档案 SN 补全(#293, 幂等只填空值)")
    parser.add_argument("--limit", type=int, default=20000,
                        help="本次最多补多少个 SN; 0=不限(存量清零用)")
    parser.add_argument("--concurrency", type=int, default=10, help="BMS 查询并发(默认 10)")
    parser.add_argument("--timeout", type=int, default=86400,
                        help="整体超时秒数(默认 86400=24h, 供存量清零长跑)")
    args, _ = parser.parse_known_args()

    # 档案补全是 UPDATE devices 的生产运维操作 → 显式授权真实库
    db = bootstrap(mode="prod", allow_real_db=real_db_requested())

    from app.services.device_profile_enrich import run_enrich

    res = asyncio.run(run_enrich(
        db, limit=(10**9 if args.limit == 0 else args.limit),
        concurrency=args.concurrency, trigger_type="manual", timeout_sec=args.timeout,
    ))
    print(f"结果: {res}")


if __name__ == "__main__":
    main()
