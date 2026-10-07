#!/usr/bin/env python3
"""L-3 手动退款「2031 乱年份」存量行打标治理（工单 #207 / #209，用户拍板选项③）

## 背景
manual_refunds 中有 3 行 register_time 被录成 2031 年（合计 ¥203），属录入错年份。
#201 已把 dup_residual 纳入统计排除（STATS_EXCLUDED_QUALITY_STATUSES）。
用户 2026-10-07 拍板选项③：**只打标，不改 register_time 原值**（保留错误留痕可追溯），
不改任何统计口径代码（选项②已否决）。

## 本脚本做什么
  定位 register_time >= 2030-01-01（上海时区）的 manual_refunds 行，
  给其中尚未打标的行写 quality_status='dup_residual' + quality_note 注明来源。
  · 不修正 register_time（原始错误保留）
  · 不物理删除任何行
  · 已打 dup_residual 的行重跑时跳过（幂等）

## 安全门（防条件写错误伤）
  命中行数必须等于预期（默认 3，来自 #207 复验）；不符则 --apply 拒绝执行，
  需人工核对后用 --expected N 显式给出新预期，绝不静默写库。

用法：
  # 预览（默认，只读）
  python scripts/mark_manual_future_year_209.py --mode dev
  # 执行（test 库）
  python scripts/mark_manual_future_year_209.py --mode test --apply
  # 生产（标准发布流程内，显式授权）
  python scripts/mark_manual_future_year_209.py --mode prod --apply --allow-real-db
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 必须先 bootstrap（在 import app 之前完成模式↔库名校验与 sys.path 设置）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select  # noqa: E402

from scripts._bootstrap import bootstrap, real_db_requested  # noqa: E402

_CN_TZ = timezone(timedelta(hours=8))

# 定位下界（上海时区 2030-01-01 00:00）：覆盖 2031 等所有"未来年份"错录。
# 与 #207 复验 SQL 的 register_time >= '2030-01-01' 同口径（绑定参数，不拼 SQL）。
CUTOFF = datetime(2030, 1, 1, 0, 0, tzinfo=_CN_TZ)
MARK_STATUS = "dup_residual"  # String(16) 列内合法值，#201 已纳入统计排除
EXPECTED_DEFAULT = 3          # #207 复验命中 3 行；不符即拒绝，防误伤


def future_year_rows(db) -> list:
    """全量定位 register_time 在 2030 年及以后的 manual_refunds 行（按 id 排序）。"""
    # 延迟导入：直跑脚本时须在 main() 的 bootstrap() 设好模式后才能 import app
    from app.models.manual_refund import ManualRefund

    return list(db.scalars(
        select(ManualRefund)
        .where(ManualRefund.register_time >= CUTOFF)
        .order_by(ManualRefund.id)
    ))


def split_pending(rows: list) -> tuple[list, list]:
    """拆成（待打标, 已打标）。已打 dup_residual 的行幂等跳过。"""
    pending = [r for r in rows if r.quality_status != MARK_STATUS]
    already = [r for r in rows if r.quality_status == MARK_STATUS]
    return pending, already


def enforce_count(total: int, expected: int) -> None:
    """行数安全门：命中数与预期不符则拒绝（防止定位条件漂移误伤大片行）。"""
    if total != expected:
        raise SystemExit(
            f"⛔ 命中行数 {total} ≠ 预期 {expected}，拒绝写库。\n"
            "   请先人工核对定位条件（register_time >= 2030-01-01）与数据，\n"
            "   确认无误后用 --expected <实际行数> 显式授权再执行。"
        )


def mark_rows(rows: list, today: str) -> int:
    """给目标行写 quality_status/note（不提交，由调用方决定 commit）。返回更新行数。"""
    n = 0
    for r in rows:
        reg_s = r.register_time.strftime("%Y-%m-%d") if r.register_time else "?"
        r.quality_status = MARK_STATUS
        # 原始 register_time 保留不改；note 记录错误年份与治理出处，可据此回滚
        r.quality_note = (
            f"L-3 录入错年份 2031（register_time={reg_s}），#207 打标治理 {today}；"
            "原始 register_time 保留不改，如需恢复请清除本标记"
        )[:512]
        n += 1
    return n


def _print_rows(rows: list, title: str) -> None:
    print(f"\n{title}：{len(rows)} 行")
    total_amt = 0.0
    for r in rows:
        reg_s = r.register_time.strftime("%Y-%m-%d") if r.register_time else "-"
        amt = float(r.refund_amount or 0)
        total_amt += amt
        print(
            f"  id={r.id:<8} SN={r.device_sn or '(无)':<16} 单号={r.order_no or '(无)':<24} "
            f"金额=¥{amt:>8.2f} register_time={reg_s} 当前quality_status={r.quality_status or '(空)'}"
        )
    print(f"  合计金额：¥{total_amt:,.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="L-3 manual_refunds 2031 乱年份打标治理（#207/#209，选项③）")
    ap.add_argument("--mode", choices=["dev", "test", "prod"], default="dev")
    ap.add_argument("--apply", action="store_true", help="落库（默认 dry-run 只读预览）")
    ap.add_argument("--expected", type=int, default=EXPECTED_DEFAULT,
                    help=f"预期命中行数（默认 {EXPECTED_DEFAULT}，不符拒绝）")
    args = ap.parse_args()

    db = bootstrap(mode=args.mode, allow_real_db=real_db_requested())
    try:
        # bootstrap 设好模式后方可 import app
        from app.core.db_guard import assert_safe_to_destroy

        rows = future_year_rows(db)
        pending, already = split_pending(rows)
        today = datetime.now(_CN_TZ).strftime("%Y-%m-%d")

        print("=" * 72)
        print("L-3 2031 乱年份打标治理（#207 拍板选项③：只打标，不改 register_time/口径）")
        print(f"模式：{'APPLY 落库' if args.apply else 'DRY-RUN 只读（加 --apply 才写库）'}")
        print(f"定位：manual_refunds.register_time >= {CUTOFF:%Y-%m-%d}（上海时区）")
        print(f"命中总数：{len(rows)}（预期 {args.expected}）；待打标 {len(pending)} / 已标 {len(already)}")
        print("=" * 72)
        _print_rows(rows, "命中明细")
        print(f"\n将写入：quality_status='{MARK_STATUS}' + quality_note 注明 #207/{today}，"
              "register_time 原值不动")

        if not args.apply:
            print("\n（DRY-RUN，未写库）")
            return 0

        # 落库前两道门：① 操作守卫（非 test 库须显式 --allow-real-db）；② 行数安全门
        assert_safe_to_destroy(
            db, reason="#209 L-3 批量打标 manual_refunds(register_time>=2030)",
            allow_real_db=real_db_requested(),
        )
        enforce_count(len(rows), args.expected)
        if not pending:
            print("\n（命中行均已是 dup_residual，无需更新，未写库）")
            return 0
        n = mark_rows(pending, today)
        db.commit()
        print(f"\n✅ 已打标 {n} 行为 dup_residual（register_time 未改、行未删，可回滚）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
