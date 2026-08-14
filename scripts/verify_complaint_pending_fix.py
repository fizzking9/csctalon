#!/usr/bin/env python3
"""Post-fix 验证脚本: 直接调用 A.today_reception_breakdown(d=今天, granularity='day')
对比修复前期望 (13 条) vs 修复后期望 (≈208 条, 且包含大量微信投诉)

运行: cd backend && ./.venv/bin/python ../scripts/verify_complaint_pending_fix.py
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import datetime as dt
from zoneinfo import ZoneInfo
from app.core.database import SessionLocal
from app.services import analytics as A

TZ = ZoneInfo("Asia/Shanghai")
d = dt.datetime.now(TZ).date()

db = SessionLocal()
try:
    print(f"调用 A.today_reception_breakdown(db=Session, date={d.isoformat()!r})")
    r = A.today_reception_breakdown(db, date=d.isoformat())

    total = r["complaint_pending_total"]
    skipped = r["complaint_pending_skipped"]
    reason = r["complaint_pending_skip_reason"]
    lst = r["complaint_pending_list"]

    print(f"\n【修复后关键指标】")
    print(f"  complaint_pending_total (红Tag数字): {total}")
    print(f"  complaint_pending_skipped:            {skipped}")
    print(f"  complaint_pending_skip_reason:        {reason!r}")
    print(f"  complaint_pending_list 实际条数:       {len(lst)}")
    print(f"  (期望 total ≈ 208, 修复前截图是 13 → 差 15 倍)")

    if lst:
        # 前 5 行 & 后 5 行 (按 occur_time ASC → 前5=最早积压, 后5=最新未处理)
        print(f"\n  列表中 前 5 行 (最早积压, 应该都是 8/6~8/8 的老投诉待处理):")
        for rw in lst[:5]:
            print(f"    {rw['occur_time']}  {rw['status_bucket']:<6} {rw['source_type']:<6} {rw['handler']:<8} {rw['title'][:50]}")
        print(f"\n  列表中 后 5 行 (时间排序的尾部):")
        for rw in lst[-5:]:
            print(f"    {rw['occur_time']}  {rw['status_bucket']:<6} {rw['source_type']:<6} {rw['handler']:<8} {rw['title'][:50]}")

        # 来源分布: 内部投诉 / 外部投诉 / 卡商群投诉 各多少条?
        from collections import Counter
        src_dist = Counter(rw["source_type"] for rw in lst)
        sb_dist = Counter(rw["status_bucket"] for rw in lst)
        # 内部投诉中 service_item 中包含微信投诉的有多少个?
        internal_wechat = sum(1 for rw in lst if ("微信" in rw["title"]) or ("微信投诉" == rw["title"].split(" · ")[0]))
        print(f"\n  来源分布:           {dict(src_dist)}")
        print(f"  status 分布:        {dict(sb_dist)}")
        print(f"  含'微信投诉'摘要的行: {internal_wechat}  (期望 ≥109 条,即为用户说的商户号那一批)")

        # 检查前 200 条是否有任何 已完成/已终止 (不应该有!)
        closed_leak = [rw for rw in lst if rw["status_bucket"] in ("已完成","已终止")]
        print(f"  ⚠ 列表中出现已完成/已终止?(应该0条): {len(closed_leak)}")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    db.close()
