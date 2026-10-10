# -*- coding: utf-8 -*-
"""标记 315 采集「SN 后补」造成的重复行 —— 存量治理 (#59)

## 背景

  源文档「先建行、后补 SN」+ `dedup_hash` 含 SN →
  同一投诉产生两条：**缺SN残影** + **有SN完整行**（后者总在后入库）。

  实测(生产库): 85 条 = 37 组，冗余 **48 条**。

## 策略（用户明令「不能乱删除 所以还是慎重」）

  ★ **只标记，不物理删除**：
    · 保留**有SN的完整行**（信息全）
    · 给**缺SN残影**打标 `quality_status='dup_sn_backfill'` +
      `quality_note` 记录「被哪条完整行取代」
    · 记录 id 便于回溯；统计/对账侧可据此把残影归入同一事件

## 判定（保守，多重条件同时满足才算）

  1. 同 `(投诉日期, 投诉手机号, 归一化问题签名)`
  2. 组内**恰好**一条有SN、其余缺SN
  3. 缺SN 的那条 `created_at` **早于**有SN 的那条（符合"先建行后补SN"时序）

  ⚠️ 条件3不满足的**不标记**（可能是数据录入顺序不同，需人工判断）。

## 用法

    # 预览(默认, 只读)
    python scripts/mark_sn_backfill_duplicates.py

    # 执行标记
    python scripts/mark_sn_backfill_duplicates.py --apply

    # 导出清单
    python scripts/mark_sn_backfill_duplicates.py --export /tmp/dup_list.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.card_complaint import CardComplaint  # noqa: E402
from app.models.external_complaint import ExternalComplaint  # noqa: E402
from app.services.complaint_sync import _weak_identity_hash  # noqa: E402

MARK_STATUS = "dup_sn_backfill"


def _find_groups(db, model):
    """找出「缺SN残影 + 有SN完整行」成组的记录

    返回 [(ghost_row, full_row), ...]
    """
    rows = db.execute(
        select(model.id, model.complaint_date, model.complainant_phone,
               model.problem, model.device_sn, model.created_at,
               model.quality_status)
        .where(model.complaint_date.isnot(None))
    ).all()

    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        if r[6] == MARK_STATUS:      # 已标记的跳过
            continue
        w = _weak_identity_hash({
            "complaint_date": r[1], "complainant_phone": r[2], "problem": r[3],
        })
        buckets[w].append(r)

    out = []
    for w, items in buckets.items():
        if len(items) < 2:
            continue
        no_sn = [x for x in items if not x[4]]
        has_sn = [x for x in items if x[4]]
        # 保守条件: 恰好 1 条有SN, 且其余缺SN
        if len(has_sn) != 1 or not no_sn:
            continue
        full = has_sn[0]
        for g in no_sn:
            # 条件3: 残影更早入库(先建行后补SN)
            if g[5] is None or full[5] is None or g[5] <= full[5]:
                out.append((g, full))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="标记 SN 后补造成的重复残影 (#59)")
    ap.add_argument("--apply", action="store_true",
                    help="执行标记(默认仅预览, 不改库)")
    ap.add_argument("--export", metavar="PATH",
                    help="导出被标记清单到 CSV(含SN/手机号, 注意PII)")
    ap.add_argument("--table", choices=["external", "card", "all"], default="all",
                    help="处理哪张表(默认 all)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        targets = []
        if args.table in ("external", "all"):
            targets.append(("external_complaints", ExternalComplaint))
        if args.table in ("card", "all"):
            targets.append(("card_complaints", CardComplaint))

        grand = 0
        exported: list[list] = []
        for tname, model in targets:
            pairs = _find_groups(db, model)
            print(f"\n=== {tname} ===")
            print(f"  发现成组: {len(pairs)} 组 (每组 = 1 残影 + 1 完整行)")
            grand += len(pairs)
            for g, full in pairs[:5]:
                print(f"    残影 id={g[0]} (SN空, 入库 {g[5]}) "
                      f"← 完整行 id={full[0]} (SN={str(full[4])[:8]}…, 入库 {full[5]})")
            if len(pairs) > 5:
                print(f"    …… 其余 {len(pairs) - 5} 组略")
            exported.extend(
                [[tname, g[0], full[0], str(g[1]), g[2] or "", str(full[4] or "")]
                 for g, full in pairs]
            )
            if args.apply and pairs:
                for g, full in pairs:
                    note = f"[{MARK_STATUS}] 被 id={full[0]} 取代(SN后补, 残影保留不删)"
                    db.execute(
                        model.__table__.update()
                        .where(model.__table__.c.id == g[0])
                        .values(quality_status=MARK_STATUS, quality_note=note[:500])
                    )
                db.commit()
                print(f"  ✔ 已标记 {len(pairs)} 条残影 (未删除任何行)")

        print(f"\n合计: {grand} 条残影待标记")
        if not args.apply:
            print("预览模式 —— 未改库。加 --apply 执行标记。")
        if args.export and exported:
            with open(args.export, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["表", "残影id", "完整行id", "投诉日期", "电话", "SN"])
                w.writerows(exported)
            print(f"已导出 {len(exported)} 行 → {args.export}  (含PII, 勿入库)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
