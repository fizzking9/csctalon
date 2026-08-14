#!/usr/bin/env python3
"""同步报错 v2 - 用 ORM 查 + 正确字段名

运行: cd backend && ./.venv/bin/python ../scripts/debug_sync_error_0814.py
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.bms_sync_log import BmsSyncLog
from app.models.scrm_sync_log import ScrmSyncLog
import datetime as dt
from zoneinfo import ZoneInfo

db = SessionLocal()
sep = "=" * 90
try:
    print(sep)
    print("【H1】bms_sync_logs 最新 20 条 → BMS 同步哪条出错了/是否触发熔断")
    print(sep)
    r1 = db.execute(select(BmsSyncLog).order_by(BmsSyncLog.id.desc()).limit(20)).scalars().all()
    if not r1:
        print("  ⚠ 没有任何 bms_sync_logs 记录")
    for log in r1:
        emoji = {"success":"✅","failed":"❌","running":"🔄","pending":"⏳"}.get(log.status,"?")
        extra = ""
        if log.status == "failed" and log.error:
            extra = f"  ❌ ERR: {log.error[:200]}"
        scope = f"[{log.start_time} → {log.end_time}]" if log.start_time else ""
        print(f"  {emoji} #{log.id} [{log.status:<7}] {log.data_type:<16} trigger={log.trigger_type:<8} created={log.created_at} {scope}{extra}")
        if log.status == "failed" and log.error and len(log.error) > 200:
            print(f"      更多错误内容:\n      {log.error[200:800]}")

    print("\n" + sep)
    print("【H2】scrm_sync_logs 最新 20 条 → 看 SCRM 同步是否报错")
    print(sep)
    r2 = db.execute(select(ScrmSyncLog).order_by(ScrmSyncLog.id.desc()).limit(20)).scalars().all()
    if not r2:
        print("  ⚠ 没有任何 scrm_sync_logs 记录 (SCRM 同步暂未启用)")
    for log in r2:
        emoji = {"success":"✅","failed":"❌","running":"🔄","pending":"⏳","reconciled_mismatch":"⚠️"}.get(log.status,"?")
        extra = ""
        if log.status == "failed" and log.error:
            extra = f"  ❌ ERR: {log.error[:200]}"
        elif log.status == "reconciled_mismatch":
            extra = f"  ⚠ reconcile_delta={log.reconcile_delta} api_total={log.api_total_reported} inserted={log.inserted_rows}+{log.updated_rows}"
        scope = f"[{log.start_time} → {log.end_time}]" if log.start_time else ""
        print(f"  {emoji} #{log.id} [{log.status:<20}] {log.data_type:<22} trigger={log.trigger_type:<8} created={log.created_at} {scope}{extra}")
        if log.status == "failed" and log.error and len(log.error) > 200:
            print(f"      更多错误内容:\n      {log.error[200:1200]}")

    print("\n" + sep)
    print("【H3-模块导入自检】bms_scheduler / analytics / sync_task_manager")
    print(sep)
    try:
        import app.services.bms_scheduler as bs_mod
        print("  ✅ import bms_scheduler OK")
        # 检查单例 scheduler 的状态
        try:
            st = bs_mod.bms_scheduler.get_status()
            import json
            print("  📊 bms_scheduler.get_status():")
            print("    ", json.dumps(st, ensure_ascii=False, indent=2, default=str))
        except Exception as _e:
            print(f"  ⚠ scheduler 没在跑(正常,需要服务启动时才实例化): {type(_e).__name__}: {_e}")
    except Exception as _e:
        import traceback as _tb
        print(f"  ❌ bms_scheduler 导入失败: {type(_e).__name__}: {_e}")
        print("     " + "\n     ".join(_tb.format_exc().splitlines()[-6:]))
    try:
        import app.services.analytics as A
        print("  ✅ import analytics OK")
    except Exception as _e:
        import traceback as _tb
        print(f"  ❌ analytics 导入失败: {type(_e).__name__}: {_e}")
        print("     " + "\n     ".join(_tb.format_exc().splitlines()[-6:]))
    try:
        from app.services.sync_task_manager import sync_task_manager as stm
        print(f"  ✅ sync_task_manager 导入 OK. snapshot:")
        snap = stm.snapshot()
        import json as j2
        print("    ", j2.dumps(snap, ensure_ascii=False, indent=2, default=str))
    except Exception as _e:
        import traceback as _tb
        print(f"  ❌ sync_task_manager 导入失败: {type(_e).__name__}: {_e}")

    print("\n" + sep)
    print("【H5-analytics 自测】today_reception_breakdown(today)")
    print(sep)
    try:
        import app.services.analytics as A2
        today_d = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
        r = A2.today_reception_breakdown(db, date=today_d.isoformat())
        print(f"  ✅ 正常返回: categories={len(r['categories'])} complaint_total={r['complaint_total']} pending_total={r['complaint_pending_total']} pending_list={len(r['complaint_pending_list'])} skipped={r['complaint_pending_skipped']}")
    except Exception as _e:
        import traceback as _tb
        print(f"  ❌ 运行异常: {type(_e).__name__}: {_e}")
        print("     " + "\n     ".join(_tb.format_exc().splitlines()[-12:]))

finally:
    db.close()
