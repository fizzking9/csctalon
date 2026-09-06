#!/usr/bin/env python3
"""D 路手动退款 历史残留脏数据 治理脚本(A-20260906-001 §3.2 / #21 P2)

目标: manual_refunds 中"与同 order_no 有SN真实行成对"的无SN乱年份残留行
(金额100占位 / register_time 乱年份 / remark=余额已清除·押金 等), 它们与真实行
成对出现并被回填后拉进近期统计窗口 → D 路重复计入经营。

治理方式: **不物理删除**, 标 quality_status='duplicate_residual' + quality_note
(写 reason + 成对真实行 id), 可回滚(改回即可)。是否从统计排除走口径评审。

判定(严格, 防误伤):
  1. is_refunded = '是'
  2. device_sn IS NULL 或 ''
  3. register_time 年份不在 2025~2026(乱年份残留)
  4. 同一 order_no 存在另一行: 有SN 且 is_refunded='是'  (成对 → 判定为重复残留)
     → 不与任何有SN真实行成对的历史行(如 2001-2025 真实历史台账) 不标记

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

from sqlalchemy import select  # noqa: E402

from app.models.manual_refund import ManualRefund  # noqa: E402

_TZ_CN = timezone(timedelta(hours=8))

# 与业务无关的历史台账年份(真实历史登记 2001-2025 是逐年递减自然分布, 但若与同单有SN行成对则判定残留)
_CURRENT_YEARS = (2025, 2026)


def _is_target_residual(m: ManualRefund) -> bool:
    """残留判定: 无SN + 登记年份异常"""
    if m.device_sn:
        return False
    if m.register_time is None:
        return False  # 无登记时间无法判年份, 保守不标记
    try:
        yr = m.register_time.astimezone(_TZ_CN).year
    except (ValueError, OSError):
        return False
    return yr not in _CURRENT_YEARS


def main() -> None:
    ap = argparse.ArgumentParser(description="D 路残留脏数据治理(预览/标记)")
    ap.add_argument("--apply", action="store_true", help="执行标记(默认仅预览不写库)")
    ap.add_argument("--limit", type=int, default=0, help="最多处理行数(0=全部)")
    args = ap.parse_args()

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        # 候选: 已退款 + 无SN + 年份异常
        cand = list(
            db.scalars(
                select(ManualRefund).where(
                    ManualRefund.is_refunded == "是",
                    (ManualRefund.device_sn.is_(None)) | (ManualRefund.device_sn == ""),
                )
            )
        )
        targets = [m for m in cand if _is_target_residual(m)]

        # 只保留"与同 order_no 有SN真实行成对"的(排除真实历史台账 2001-2025 无对行)
        paired: list[ManualRefund] = []
        unpaired_count = 0
        paired_amt = 0.0
        for m in targets:
            if not m.order_no:
                unpaired_count += 1
                continue
            mate = db.execute(
                select(ManualRefund.id)
                .where(
                    ManualRefund.order_no == m.order_no,
                    ManualRefund.device_sn.is_not(None),
                    ManualRefund.device_sn != "",
                    ManualRefund.is_refunded == "是",
                )
                .limit(1)
            ).scalar_one_or_none()
            if mate:
                paired.append(m)
                paired_amt += float(m.refund_amount or 0)
            else:
                unpaired_count += 1

        if args.limit:
            paired = paired[: args.limit]

        print("=" * 70)
        print("D 路残留脏数据治理 (A-20260906-001 / #21 P2)")
        print(f"模式: {'执行标记 --apply' if args.apply else '仅预览(加 --apply 才写库)'}")
        print(f"无SN+年份异常候选: {len(targets)} 行")
        print(f"  其中 与同单有SN真实行成对(残留): {len(paired)} 行 / ¥{paired_amt:,.2f}")
        print(f"  无成对(可能是真实历史台账, 不标记): {unpaired_count} 行")
        print("=" * 70)

        # 展示前 10 行供人工确认
        for i, m in enumerate(paired[:10], 1):
            reg = m.register_time
            reg_s = reg.strftime("%Y-%m-%d") if reg else "-"
            print(
                f"  #{i:<3} id={m.id:<8} order_no={m.order_no or '':<26} "
                f"金额={float(m.refund_amount or 0):>10.2f} 登记={reg_s} 备注={(m.remark or '')[:20]}"
            )
        if len(paired) > 10:
            print(f"  ... 其余 {len(paired) - 10} 行省略")

        if args.apply and paired:
            # 标记 duplicate_residual(可回滚: 改回 quality_status/清 note 即可)
            n = 0
            for m in paired:
                if m.quality_status == "duplicate_residual":
                    continue
                # 找成对的真实行(带SN)
                mate_id = db.execute(
                    select(ManualRefund.id)
                    .where(
                        ManualRefund.order_no == m.order_no,
                        ManualRefund.device_sn.is_not(None),
                        ManualRefund.device_sn != "",
                        ManualRefund.is_refunded == "是",
                    )
                    .order_by(ManualRefund.id)
                    .limit(1)
                ).scalar_one_or_none()
                m.quality_status = "duplicate_residual"
                m.quality_note = (
                    f"治理#21: 与同 order_no 有SN真实行成对的残留行, 判定重复登记; "
                    f"成对真实行 id={mate_id}; 如需恢复请清除本标记"
                )
                n += 1
            db.commit()
            print(f"\n✅ 已标记 {n} 行为 duplicate_residual (可回滚)")
        elif not args.apply:
            print("\n(预览模式, 未写库)")

        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
