#!/usr/bin/env python3
"""D 路手动退款 确凿重复治理脚本(A-20260906-001 v1.1 §3.2 / #21 P2)

目标(2026-09-06 业务裁定, v1.1 收紧口径): 仅治理**确凿重复** ——
同一 E 水单(refund_record_reports.id)被 backfill_d_op_time 回填到 **多行**
manual_refunds(同 backfill_refund_record_report_id 的多行, 即一对多回填产物)。
这类是同一笔真实退款在台账里被记了多行 → D 路重复计入经营。

判定(严格, 防误伤; 生产预览曾发现"无SN+年份异常"一刀切会误伤真实多批次登记, 已废弃):
  1. is_refunded = '是'
  2. backfill_refund_record_report_id IS NOT NULL
  3. 该 backfill_refund_record_report_id 被 >= 2 行 manual 引用(一对多)
  4. 每组保留 1 行(优先: 有SN 且 id 最小 = 最早登记的真实行), 其余标 dup_residual

治理方式: **不物理删除**, 标 quality_status='dup_residual'(≤16字符, 满足
String(16) 列约束) + quality_note(写 reason + 保留行 id), 可回滚(改回即可)。
是否从统计排除走口径评审。

用法:
  cd backend
  ./.venv/bin/python scripts/cleanup_manual_duplicate_residual.py          # 预览(不写)
  ./.venv/bin/python scripts/cleanup_manual_duplicate_residual.py --apply  # 执行标记
  ./.venv/bin/python scripts/cleanup_manual_duplicate_residual.py --limit 500
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.models.manual_refund import ManualRefund  # noqa: E402

_TZ_CN = timezone(timedelta(hours=8))


def main() -> None:
    ap = argparse.ArgumentParser(description="D 路确凿重复(同report一对多)治理(预览/标记)")
    ap.add_argument("--apply", action="store_true", help="执行标记(默认仅预览不写库)")
    ap.add_argument("--limit", type=int, default=0, help="最多处理行数(0=全部)")
    args = ap.parse_args()

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        # 1) 找被 >=2 行 manual 引用的 report(一对多回填的 report 集合)
        dup_report_rows = db.execute(
            select(
                ManualRefund.backfill_refund_record_report_id,
                func.count().label("n"),
            )
            .where(
                ManualRefund.backfill_refund_record_report_id.is_not(None),
                ManualRefund.is_refunded == "是",
            )
            .group_by(ManualRefund.backfill_refund_record_report_id)
            .having(func.count() > 1)
        ).all()
        dup_report_ids = [r[0] for r in dup_report_rows]

        # 2) 拉出这些 report 关联的全部 manual 行
        if dup_report_ids:
            rows = list(
                db.scalars(
                    select(ManualRefund).where(
                        ManualRefund.backfill_refund_record_report_id.in_(dup_report_ids),
                        ManualRefund.is_refunded == "是",
                    ).order_by(ManualRefund.id)
                )
            )
        else:
            rows = []

        # 3) 分组: 每组保留 1 行(优先有SN, 再按 id), 其余为超额(治理目标)
        from collections import defaultdict

        groups: dict[str, list[ManualRefund]] = defaultdict(list)
        for m in rows:
            groups[m.backfill_refund_record_report_id].append(m)

        targets: list[ManualRefund] = []
        keep_map: dict[str, int] = {}  # report_id -> keep manual id
        for rid, members in groups.items():
            members.sort(key=lambda x: (0 if x.device_sn else 1, x.id))
            keep = members[0]
            keep_map[rid] = keep.id
            targets.extend(members[1:])  # 其余全部是确凿重复

        if args.limit:
            targets = targets[: args.limit]

        t_amt = sum(float(m.refund_amount or 0) for m in targets)
        print("=" * 70)
        print("D 路确凿重复治理 (A-20260906-001 v1.1 / #21 P2)")
        print(f"模式: {'执行标记 --apply' if args.apply else '仅预览(加 --apply 才写库)'}")
        print(f"一对多 report 组数: {len(groups)}")
        print(f"  每组保留 1 行后, 超额(确凿重复)行: {len(targets)} 行 / ¥{t_amt:,.2f}")
        print("=" * 70)

        for i, m in enumerate(targets[:10], 1):
            reg = m.register_time
            reg_s = reg.strftime("%Y-%m-%d") if reg else "-"
            print(
                f"  #{i:<3} id={m.id:<8} order_no={m.order_no or '':<26} "
                f"金额={float(m.refund_amount or 0):>10.2f} 登记={reg_s} "
                f"SN={'有' if m.device_sn else '无':<3} report={m.backfill_refund_record_report_id}"
            )
        if len(targets) > 10:
            print(f"  ... 其余 {len(targets) - 10} 行省略")

        if args.apply and targets:
            n = 0
            for m in targets:
                if m.quality_status == "dup_residual":
                    continue
                m.quality_status = "dup_residual"
                m.quality_note = (
                    f"治理#21(v1.1): 同 E 水单 report={m.backfill_refund_record_report_id} "
                    f"被回填到多行 manual, 本行判定确凿重复(保留行 id={keep_map.get(m.backfill_refund_record_report_id)}); "
                    "如需恢复请清除本标记"
                )
                n += 1
            db.commit()
            print(f"\n✅ 已标记 {n} 行为 dup_residual (可回滚)")
        elif not args.apply:
            print("\n(预览模式, 未写库)")
            db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
