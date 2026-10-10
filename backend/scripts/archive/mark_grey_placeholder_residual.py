#!/usr/bin/env python3
"""D 路手动退款 灰色占位残留行治理脚本(A-20260906-001 v1.2 §3.2 / #21 P2)

目标(2026-09-06 业务裁定第二批, 范围收紧): 治理"无SN + 乱年份 + auto_filled +
无登记人"的**同单多行超额行**, 且**仅当该 order 在 E 水单只有 1 笔退款**。

背景: 抽样实证多数灰色行(如 order 真实退款 ¥47.50 已由 E 实扣, 台账却多记一行
¥100 备注"余额已清除")与真实行同单重复 → D 路重复计入经营。但**排除 E 水单多笔
的 order**: 这类灰色行金额可能=E 分笔退款合计(如 299=240+52.4+6.6), 疑似真实
"总额登记行", 误删会漏记(2026-09-06 用户裁定, 见 grey_multi_check 实证)。

与第一类(同 report 一对多, 见 cleanup_manual_duplicate_residual.py)的区别:
这类**没有 report 溯源**(早于护栏的回填未记录 report 链接), 但特征高度一致。

判定(严格, 防误伤):
  1. is_refunded = '是'
  2. 该行是同 order_no 多行中的"超额行"(非保留首行; 保留规则=优先有SN+最早时间)
  3. device_sn 为空
  4. register_time 年份不在 2025~2026(乱年份)
  5. quality_status = 'auto_filled'(被回填过)
  6. registrant 为空(无登记人, 区别于真实客服登记行)
  7. 【安全护栏】该 order_no 在 refund_record_reports(E) 中只有 1 笔
     → E 多笔的 order 不自动治理(可能是真实总额登记, 转人工)

治理方式: **不物理删除**, 标 quality_status='dup_residual' + quality_note
(写 reason + 同单保留行 id), 可回滚。是否从统计排除走口径评审。

用法:
  cd backend
  ./.venv/bin/python scripts/mark_grey_placeholder_residual.py          # 预览(不写)
  ./.venv/bin/python scripts/mark_grey_placeholder_residual.py --apply  # 执行标记
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.models.manual_refund import ManualRefund  # noqa: E402
from app.models.refund_record_report import RefundRecordReport  # noqa: E402

_TZ_CN = timezone(timedelta(hours=8))
_CURRENT_YEARS = (2025, 2026)
_MIN_DT = datetime(2000, 1, 1, tzinfo=_TZ_CN)


def _is_grey_placeholder(m: ManualRefund) -> bool:
    """灰色占位行判定: 无SN + 乱年份 + auto_filled + 无登记人"""
    if m.device_sn:
        return False
    if m.quality_status != "auto_filled":
        return False
    if m.registrant:
        return False
    if m.register_time is None:
        return False
    try:
        yr = m.register_time.astimezone(_TZ_CN).year
    except (ValueError, OSError):
        return False
    return yr not in _CURRENT_YEARS


def main() -> None:
    ap = argparse.ArgumentParser(description="D 路灰色占位残留行治理(预览/标记)")
    ap.add_argument("--apply", action="store_true", help="执行标记(默认仅预览不写库)")
    ap.add_argument("--include-e-multi", action="store_true",
                    help="连带治理 E 水单多笔 order 的灰色行(默认排除, 见 v1.2 安全护栏; 2026-09-06 用户裁定 12 行都治)")
    args = ap.parse_args()

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        # 全部已退款且有有效时间的行, 按 order 分组标 rn(保留规则同 D 路统计口径)
        all_rows = list(
            db.scalars(
                select(ManualRefund).where(
                    ManualRefund.is_refunded == "是",
                    ManualRefund.order_no.is_not(None),
                    ManualRefund.order_no != "",
                    (ManualRefund.op_time.is_not(None)) | (ManualRefund.register_time.is_not(None)),
                ).order_by(ManualRefund.id)
            )
        )
        # 分组排序: 保留首行 = 优先有SN + 最早 COALESCE(op,reg)
        from collections import defaultdict

        groups: dict[str, list[ManualRefund]] = defaultdict(list)
        for m in all_rows:
            groups[m.order_no].append(m)
        for members in groups.values():
            members.sort(key=lambda x: (
                0 if x.device_sn else 1,
                x.op_time or x.register_time or _MIN_DT,
                x.id,
            ))

        targets: list[ManualRefund] = []
        keep_map: dict[str, int] = {}
        skipped_e_multi: list[ManualRefund] = []

        # 安全护栏: 找出 E 水单多笔的 order(E 分笔扣款, 灰色行可能是总额登记, 不自动治理)
        e_multi_orders = {
            row[0] for row in db.execute(
                select(RefundRecordReport.platform_order_no)
                .where(RefundRecordReport.platform_order_no.is_not(None))
                .group_by(RefundRecordReport.platform_order_no)
                .having(func.count() > 1)
            ).all()
        }

        for order_no, members in groups.items():
            keep = members[0]
            keep_map[order_no] = keep.id
            for m in members[1:]:
                if m.quality_status == "dup_residual":
                    continue  # 已治理过(第一类)
                if not _is_grey_placeholder(m):
                    continue
                if order_no in e_multi_orders and not args.include_e_multi:
                    # E 分笔扣款的 order → 灰色行可能是真实总额登记, 默认转人工不自动治
                    skipped_e_multi.append(m)
                    continue
                targets.append(m)

        t_amt = sum(float(m.refund_amount or 0) for m in targets)
        skip_amt = sum(float(m.refund_amount or 0) for m in skipped_e_multi)
        print("=" * 70)
        print("D 路灰色占位残留行治理 (A-20260906-001 v1.2 / #21 P2)")
        print(f"模式: {'执行标记 --apply' if args.apply else '仅预览(加 --apply 才写库)'}")
        print(f"灰色占位行(无SN+乱年份+auto_filled+无登记人, 同单超额): 候选 {len(targets) + len(skipped_e_multi)} 行")
        print(f"  → 治理(E单笔 order): {len(targets)} 行 / ¥{t_amt:,.2f}")
        print(f"  → 排除(E多笔 order, 疑似总额登记, 转人工): {len(skipped_e_multi)} 行 / ¥{skip_amt:,.2f}")
        print("=" * 70)

        for i, m in enumerate(targets[:10], 1):
            reg = m.register_time
            reg_s = reg.strftime("%Y-%m-%d") if reg else "-"
            op = m.op_time
            op_s = op.strftime("%Y-%m-%d") if op else "-"
            print(
                f"  #{i:<3} id={m.id:<8} order_no={m.order_no or '':<26} "
                f"金额={float(m.refund_amount or 0):>10.2f} 登记={reg_s} 回填op={op_s} "
                f"备注={(m.remark or '')[:18]}"
            )
        if len(targets) > 10:
            print(f"  ... 其余 {len(targets) - 10} 行省略")

        if args.apply and targets:
            n = 0
            for m in targets:
                m.quality_status = "dup_residual"
                m.quality_note = (
                    f"治理#21(v1.2): 同单占位残留行(无SN+乱年份+auto_filled+无登记人), "
                    f"判定与保留行 id={keep_map.get(m.order_no)} 重复; 如需恢复请清除本标记"
                )
                n += 1
            db.commit()
            print(f"\n✅ 已标记 {n} 行为 dup_residual (可回滚)")
        elif not args.apply:
            print("\n(预览模式, 未写库)")
            db.rollback()
        else:
            # --apply 但无目标行: 提示 0 行而非静默(Reviewer INFO)
            print("\n(--apply 模式, 无待治理目标行, 未写库)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
