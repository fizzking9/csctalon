#!/usr/bin/env python3
"""manual_refunds.device_sn 历史回填工具 (#311 方案 B, 2026-10-13) — 默认预览, --apply 才写库

## 背景
手动退款登记(manual_refunds)Excel 中常缺 device_sn 但有 order_no; 在 #311 之前
入库路径无反查机制, 直接以 None 入库。90 天窗口实测:
  - D 路共 7618 笔, 其中 2200 笔(28.9%)device_sn 为空但 order_no 有值
  - 这 2200 笔在 sale_days_split 里 100% 归 "(无激活记录)" 桶 (因 SN 空无法查 active_date)
  - 在 device_dims.by_combo 里 100% 归 "(未填SN)" 桶, 拉低真实套餐覆盖率

## 反查策略(#311 拍板, 与入库路径同源 _resolve_device_sn_by_order)
按 order_no 跨三表反查 device_sn, 取并集兜底:
  1. refunds.platform_order_no / third_order_no  (已退款记录带 SN, 命中率 ~24.6%)
  2. device_combos.recharge_order_no             (设备充值订单, 命中率 ~46.6%)
  3. bms_orders.order_no                         (BMS 订单, 命中率极低, 兜底)
并集命中率 ~54.6% (90 天实测 1201/2200)。

## 处理逻辑
- 命中: UPDATE device_sn = <反查值> (保留 quality_status 不变, 仅回填 SN)
- 未命中: UPDATE quality_status='sn_unresolvable',
           quality_note='[SN无法反查] order_no=<xxx> 三表均无匹配'
  (该状态不进入 STATS_EXCLUDED_QUALITY_STATUSES, 不影响经营统计口径;
   仅作为数据质量页可见标记, 便于后续人工补录或溯源)
- 幂等: 只处理 device_sn IS NULL AND order_no IS NOT NULL AND
        COALESCE(quality_status,'') NOT IN ('sn_unresolvable','dup_residual','ignored')
        已反查成功(device_sn 已填)与已打标 sn_unresolvable 的行自动跳过

## 安全性
- 默认预览, 不写库; 写库需显式 --apply --yes
- 事务包裹 + 前后自检 (空 SN 行数应下降, 未打标行不应增加)
- 仅更新 device_sn / quality_status / quality_note 三列, 不触碰去重键
- 建议先 deploy/backup.sh 备份

用法:
    cd backend && ./.venv/bin/python ../scripts/backfill_manual_refund_sn.py           # 预览
    cd backend && ./.venv/bin/python ../scripts/backfill_manual_refund_sn.py --apply --yes
    # 仅处理某时间段(默认全量)
    ... --since '2026-07-15 00:00' --until '2026-10-13 23:59'
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_manual_refund_sn")

from sqlalchemy import func, select, text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.manual_refund import ManualRefund  # noqa: E402
from app.services.upsert import _resolve_device_sn_by_order  # noqa: E402

_UTC = timezone.utc
_TZ_CN = timezone(timedelta(hours=8))


def main() -> int:
    ap = argparse.ArgumentParser(description="manual_refunds.device_sn 历史回填(默认预览)")
    ap.add_argument("--since", default=None, help="起始时间(北京时间, YYYY-MM-DD HH:MM, 默认全量)")
    ap.add_argument("--until", default=None, help="结束时间(北京时间, YYYY-MM-DD HH:MM, 默认全量)")
    ap.add_argument("--apply", action="store_true", help="实际写库(默认仅预览)")
    ap.add_argument("--yes", action="store_true", help="确认执行(与 --apply 同用)")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 行(调试用)")
    args = ap.parse_args()

    if args.apply and not args.yes:
        logger.error("--apply 需同时给出 --yes 以确认(防误操作)")
        return 2

    # 时间窗口(可选, 北京时间 → UTC, 按 register_time 过滤)
    since = None
    until = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d %H:%M").replace(tzinfo=_TZ_CN).astimezone(_UTC)
    if args.until:
        until = datetime.strptime(args.until, "%Y-%m-%d %H:%M").replace(tzinfo=_TZ_CN).astimezone(_UTC)
    if since or until:
        logger.info("时间窗口(北京时间): %s ~ %s", args.since or "(无)", args.until or "(无)")

    db = SessionLocal()
    try:
        # 查待处理行: device_sn 空且 order_no 有值, 且未打 sn_unresolvable/dup_residual/ignored
        q = select(ManualRefund).where(
            ManualRefund.device_sn.is_(None),
            ManualRefund.order_no.isnot(None),
            # 幂等: 已打 sn_unresolvable 的不重复反查(除非数据更新后人工清状态后重跑)
            func.coalesce(ManualRefund.quality_status, "").notin_(
                ("sn_unresolvable", "dup_residual", "ignored")
            ),
        )
        if since:
            q = q.where(ManualRefund.register_time >= since)
        if until:
            q = q.where(ManualRefund.register_time <= until)
        q = q.order_by(ManualRefund.id)
        if args.limit:
            q = q.limit(args.limit)

        rows = list(db.scalars(q).all())
        logger.info("待处理行数(device_sn 空且 order_no 有值): %d", len(rows))
        if not rows:
            return 0

        # ── 反查 ──
        plans_resolved: list[tuple[int, str, str]] = []   # (id, order_no, resolved_sn)
        plans_unresolvable: list[tuple[int, str]] = []    # (id, order_no)
        for r in rows:
            sn = _resolve_device_sn_by_order(db, r.order_no)
            if sn:
                plans_resolved.append((r.id, r.order_no, sn))
            else:
                plans_unresolvable.append((r.id, r.order_no))

        logger.info("反查结果: 命中 %d / 未命中 %d (命中率 %.1f%%)",
                    len(plans_resolved),
                    len(plans_unresolvable),
                    100.0 * len(plans_resolved) / max(1, len(rows)))

        # 预览
        print("\n=== 命中预览(前 15 行) ===")
        print(f"{'id':>8}  {'order_no':<32} {'resolved_sn'}")
        for mid, ono, sn in plans_resolved[:15]:
            print(f"{mid:>8}  {ono:<32} {sn}")
        print(f"... 共 {len(plans_resolved)} 行")

        print("\n=== 未命中预览(前 15 行, 将打 sn_unresolvable) ===")
        print(f"{'id':>8}  {'order_no'}")
        for mid, ono in plans_unresolvable[:15]:
            print(f"{mid:>8}  {ono}")
        print(f"... 共 {len(plans_unresolvable)} 行")

        if not args.apply:
            print("\n[预览模式] 未写库。确认无误后加 --apply --yes 执行。")
            return 0

        # ── 执行(事务 + 前后自检) ──
        before_null = db.scalar(text(
            "SELECT count(*) FROM manual_refunds "
            "WHERE device_sn IS NULL AND order_no IS NOT NULL"
        )) or 0
        logger.info("更新前 空SN且order_no有值 行数: %d", before_null)

        # 1) 回填命中的 device_sn
        n_filled = 0
        for mid, _ono, sn in plans_resolved:
            db.execute(
                text("UPDATE manual_refunds SET device_sn = :sn WHERE id = :i"),
                {"sn": sn, "i": mid},
            )
            n_filled += 1
            if n_filled % 500 == 0:
                db.flush()
                logger.info("  已回填 SN %d/%d", n_filled, len(plans_resolved))
        db.flush()
        logger.info("  回填 SN 完成: %d 行", n_filled)

        # 2) 未命中打标 sn_unresolvable
        n_marked = 0
        for mid, ono in plans_unresolvable:
            note = f"[SN无法反查] order_no={ono} 三表(refunds/device_combos/bms_orders)均无匹配"
            db.execute(
                text("UPDATE manual_refunds SET quality_status = 'sn_unresolvable', "
                     "quality_note = COALESCE(quality_note || ' | ', '') || :note "
                     "WHERE id = :i"),
                {"note": note, "i": mid},
            )
            n_marked += 1
            if n_marked % 500 == 0:
                db.flush()
                logger.info("  已打标 sn_unresolvable %d/%d", n_marked, len(plans_unresolvable))
        db.flush()
        logger.info("  打标 sn_unresolvable 完成: %d 行", n_marked)

        after_null = db.scalar(text(
            "SELECT count(*) FROM manual_refunds "
            "WHERE device_sn IS NULL AND order_no IS NOT NULL"
        )) or 0
        logger.info("更新后 空SN且order_no有值 行数: %d (其中 sn_unresolvable 已打标的不计入待处理)",
                    after_null)

        # 自检: 命中回填后, 空SN行数应等于未命中数(剩余未打标的应=0)
        # after_null 包含已打标的 sn_unresolvable 行(它们 device_sn 仍空);
        # 真正待处理(未打标的)应=0
        unmarked_null = db.scalar(text(
            "SELECT count(*) FROM manual_refunds "
            "WHERE device_sn IS NULL AND order_no IS NOT NULL "
            "AND COALESCE(quality_status,'') NOT IN ('sn_unresolvable','dup_residual','ignored')"
        )) or 0
        if unmarked_null > 0:
            logger.warning("自检: 仍有 %d 行未处理(可能反查期间新增数据, 需重跑)", unmarked_null)

        db.commit()
        logger.info("✔ 回填完成: SN 回填 %d 行 / 打标 sn_unresolvable %d 行",
                    n_filled, n_marked)
        return 0
    except Exception:
        db.rollback()
        logger.exception("回填失败, 已回滚")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
