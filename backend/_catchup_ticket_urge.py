"""Manually trigger BMS ticket re-sync to fill 8 new urge/dispatch columns (urge_count etc)."""
import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/app')

from zoneinfo import ZoneInfo
from app.services.bms_sync import sync_all

SH_TZ = ZoneInfo("Asia/Shanghai")
# Use 30 full days window (6/29 ~ 8/3 inclusive) Beijing time
start_sh = datetime(2026, 6, 29, 0, 0, 0, tzinfo=SH_TZ)
end_sh = datetime(2026, 8, 3, 23, 59, 59, tzinfo=SH_TZ)

print(f"Manual ticket sync window (Beijing): {start_sh.isoformat()} ~ {end_sh.isoformat()}")
print("Running sync_all(data_types=['ticket'], time_ranges=...) ...")
print()

results = asyncio.run(
    sync_all(
        trigger_type="manual",
        triggered_by="system_admin_urge_patch",
        on_progress=None,
        data_types=["ticket"],
        time_ranges={"ticket": (start_sh, end_sh)},
    )
)

print()
print(f"========== SYNC RESULTS ({len(results)} items) ==========")
for r in results:
    success = r.get("success")
    name = r.get("name", r.get("data_type", "?"))
    dt = r.get("data_type", "?")
    err = r.get("error", "")
    rows = r.get("imported_rows", r.get("rows", "?"))
    status = "OK" if success else "FAIL"
    print(f"[{status}] {dt:<15s} name={name:<25s} rows={rows}")
    if not success:
        print(f"       ERROR: {err[:500]}")
print()
print("DONE.")
