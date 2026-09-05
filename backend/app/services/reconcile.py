"""对账回填服务 - 退款记录报表(E 路线上水单) 与经营四路 A/B/C/D 勾稽 + D 路时间回填

依据: A-20260905-002 LOCKED (#17 P2)。核心不变式: **叠加不推翻 v4 LOCKED 口径**,
不修改现有核算函数(_cash_routes_total 等)。

功能:
1. backfill_d_op_time(db): D 路(manual_refunds.is_refunded='是')缺 op_time/倒挂行,
   用 refund_record_reports 的精确退款时间按可信匹配回填(带 auto_filled 质量标记)。
2. water_statement(db, start, end, status): E 路水单(refund_record_reports)逐笔勾稽
   匹配 A(线上直退)/B(钱包)/C(闪猫)/D(手动) → matched/未登记, 输出对账差异下钻。

D 路回填匹配优先级(LOCKED A-20260905-002 §3.3):
  L1: manual.order_no == report.platform_order_no
  L2: manual.device_sn == report.device_sn AND |refund_amount| ≤ 0.01
  L3: manual.device_sn == report.device_sn AND report.refund_time ∈ [register_time-7d, register_time+7d]
  唯一候选才回填; 多候选 → quality_status=pending 人工复核。
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.models.manual_refund import ManualRefund
from app.models.refund import Refund
from app.models.refund_record_report import RefundRecordReport
from app.models.shanmao_refund import ShanmaoRefund
from app.models.wallet import WalletWithdrawal

logger = logging.getLogger(__name__)

_TZ_CN = timezone(timedelta(hours=8))

# 退款记录报表里"退款成功"才算真扣款水单(勾稽/回填只认成功)
_SUCCESS_STATUSES = ("退款成功", "成功", "SUCCESS")


def _is_success_status(s: str | None) -> bool:
    return bool(s and s.strip() in _SUCCESS_STATUSES)


def _amount_eq(a, b, tol=0.01) -> bool:
    """金额近似相等(元, 容忍分位误差)"""
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _dt_or_none(v):
    if v is None:
        return None
    return v


# ─────────────────────────────────────────────────────────────
# 1. D 路 op_time 回填
# ─────────────────────────────────────────────────────────────
def backfill_d_op_time(db, limit: int = 5000) -> dict:
    """D 路(manual_refunds)缺失/倒挂 op_time → 用退款记录报表退款时间回填。

    仅回填 is_refunded='是' 且 op_time IS NULL 或 op_time 明显早于登记(倒挂>1天)的行。
    幂等: 已 auto_filled 的来源 report 已标记 backfill_manual_refund_id 的行跳过。
    """
    stats = {"scanned": 0, "matched": 0, "backfilled": 0, "pending": 0, "skipped_no_candidate": 0}
    # 候选 manual 行: 已退款 且 op_time 缺失/倒挂
    candidates = list(
        db.scalars(
            select(ManualRefund)
            .where(
                ManualRefund.is_refunded == "是",
                or_(
                    ManualRefund.op_time.is_(None),
                    # 倒挂: op_time 比 register_time 早超过 1 天(视为异常,可重填)
                    ManualRefund.op_time < ManualRefund.register_time - timedelta(days=1),
                ),
            )
            .order_by(ManualRefund.id)
            .limit(limit)
        )
    )
    stats["scanned"] = len(candidates)

    for m in candidates:
        # 已回填过(auto_filled)且来源仍在 → 跳过
        if m.quality_status == "auto_filled" and m.op_time is not None:
            stats["matched"] += 1
            continue

        # L1: 订单号精确匹配(manual.order_no ↔ report.platform_order_no)
        matched_reports: list[RefundRecordReport] = []
        if m.order_no:
            matched_reports = list(
                db.scalars(
                    select(RefundRecordReport).where(
                        RefundRecordReport.platform_order_no == m.order_no.strip(),
                        RefundRecordReport.refund_status.in_(_SUCCESS_STATUSES),
                    )
                )
            )
        # L2: 设备号 + 金额
        if not matched_reports and m.device_sn:
            cands = list(
                db.scalars(
                    select(RefundRecordReport).where(
                        RefundRecordReport.device_sn == m.device_sn,
                        RefundRecordReport.refund_status.in_(_SUCCESS_STATUSES),
                    )
                )
            )
            matched_reports = [r for r in cands if _amount_eq(r.refund_amount, m.refund_amount)]

        # L3: 设备号 + 时间窗(登记前后7天, 唯一候选)
        if not matched_reports and m.device_sn and m.register_time:
            reg = m.register_time
            window_start = reg - timedelta(days=7)
            window_end = reg + timedelta(days=7)
            cands = list(
                db.scalars(
                    select(RefundRecordReport).where(
                        RefundRecordReport.device_sn == m.device_sn,
                        RefundRecordReport.refund_time >= window_start,
                        RefundRecordReport.refund_time <= window_end,
                        RefundRecordReport.refund_status.in_(_SUCCESS_STATUSES),
                    )
                )
            )
            # 时间窗候选里只取唯一(避免多月重取产生重复)
            matched_reports = cands if len(cands) == 1 else []

        if not matched_reports:
            stats["skipped_no_candidate"] += 1
            continue
        if len(matched_reports) > 1:
            # 多候选 → pending 人工复核
            m.quality_status = "pending"
            m.quality_note = (
                f"对账回填多候选(device_sn={m.device_sn}, 金额={m.refund_amount}),"
                "需人工确认退款时间"
            )
            stats["pending"] += 1
            continue

        report = matched_reports[0]
        # 唯一命中 → 回填
        old_op = m.op_time
        m.op_time = report.refund_time
        m.quality_status = "auto_filled"
        m.quality_note = (
            f"由退款记录报表 refund_time 回填, 来源 refund_record_reports.id={report.id}"
            + (f", 原 op_time={old_op}" if old_op else "")
        )
        # 反向溯源(幂等: 同 report 只标一次)
        if not report.backfill_manual_refund_id:
            report.backfill_manual_refund_id = str(m.id)
        stats["backfilled"] += 1
        stats["matched"] += 1

    db.commit()
    return stats


# ─────────────────────────────────────────────────────────────
# 2. E 路水单逐笔勾稽
# ─────────────────────────────────────────────────────────────
def _build_route_maps(db, start, end):
    """预取各经营路匹配键, 加速勾稽。

    A 路(线上直退 refunds): platform_order_no / refund_no → id
    B 路(钱包 wallet 用户提现): device_sn+金额 → 备注含退费单号(弱) — 用 device_sn+op_amount 集合
    C 路(闪猫 shanmao_refunds): refund_no/device_sn
    D 路(手动 manual_refunds): order_no/device_sn+金额
    """
    # A: 平台订单号 → refund_no(退费单)
    a_by_order: dict[str, set[str]] = {}
    rows = db.execute(
        select(Refund.platform_order_no, Refund.refund_no)
        .where(Refund.platform_order_no.is_not(None))
    ).all()
    for order_no, refund_no in rows:
        if order_no:
            a_by_order.setdefault(str(order_no).strip(), set()).add(str(refund_no))
    # A2: refund_no 本身(可能 report.platform_order_no 直接是 RF 退费单号)
    a_by_no: set[str] = set()
    for (rno,) in db.execute(select(Refund.refund_no)).all():
        if rno:
            a_by_no.add(str(rno).strip())

    # C: shanmao 设备号集合(已退款)
    c_sns: set[str] = set()
    for (sn,) in db.execute(
        select(ShanmaoRefund.device_sn).where(ShanmaoRefund.device_sn.is_not(None))
    ).all():
        if sn:
            c_sns.add(str(sn).strip())

    # B: wallet 用户提现 设备+金额集合
    b_keys: set[tuple[str, float]] = set()
    for sn, amt in db.execute(
        select(WalletWithdrawal.device_sn, WalletWithdrawal.op_amount)
        .where(
            WalletWithdrawal.op_type == "用户提现",
            WalletWithdrawal.device_sn.is_not(None),
            WalletWithdrawal.op_amount.is_not(None),
        )
    ).all():
        if sn and amt is not None:
            b_keys.add((str(sn).strip(), float(amt)))

    # D: manual 已退款 设备+金额 + order_no 集合
    d_keys: set[tuple[str, float]] = set()
    d_order_nos: set[str] = set()
    for sn, amt, ono in db.execute(
        select(ManualRefund.device_sn, ManualRefund.refund_amount, ManualRefund.order_no)
        .where(
            ManualRefund.is_refunded == "是",
            ManualRefund.refund_amount.is_not(None),
        )
    ).all():
        if sn and amt is not None:
            d_keys.add((str(sn).strip(), float(amt)))
        if ono:
            d_order_nos.add(str(ono).strip())

    return {
        "a_by_order": a_by_order,
        "a_by_no": a_by_no,
        "c_sns": c_sns,
        "b_keys": b_keys,
        "d_keys": d_keys,
        "d_order_nos": d_order_nos,
    }


def _classify_report(report: RefundRecordReport, maps: dict) -> str:
    """单笔 report → 命中路由(A/B/C/D)/未登记。"""
    order_no = (report.platform_order_no or "").strip()
    sn = (report.device_sn or "").strip()
    amt = float(report.refund_amount) if report.refund_amount is not None else None

    # A 路: 平台订单号命中退费单 或 直接是退费单号
    if order_no:
        if order_no in maps["a_by_no"] or order_no in maps["a_by_order"]:
            return "A"
        # report 平台订单号是否出现在退费单的 platform_order_no
        if maps["a_by_order"].get(order_no):
            return "A"
    # D 路(order_no 命中手动台账)
    if order_no and order_no in maps["d_order_nos"]:
        return "D"
    # A2: report 订单号前缀 WS/WX/WN + 退费单关联(已在 a_by_order 覆盖)
    # B/D 按 设备+金额
    if sn and amt is not None:
        if (sn, amt) in maps["d_keys"]:
            return "D"
        if (sn, amt) in maps["b_keys"]:
            return "B"
        if sn in maps["c_sns"]:
            return "C"
    return "unmatched"


def water_statement(
    db,
    start: datetime,
    end: datetime,
    status: str = "all",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """E 路水单逐笔勾稽视图(仅 refund_status='退款成功')。

    输出: 总笔数/金额, matched(按 A/B/C/D 分布), 未登记(unmatched), 明细分页。
    金额单位: 元(与退款报表一致)。不改变任何现有核算。
    """
    base = select(RefundRecordReport).where(
        RefundRecordReport.refund_status.in_(_SUCCESS_STATUSES),
        RefundRecordReport.refund_time >= start,
        RefundRecordReport.refund_time <= end,
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_amt = (
        db.scalar(select(func.coalesce(func.sum(RefundRecordReport.refund_amount), 0)).where(
            RefundRecordReport.refund_status.in_(_SUCCESS_STATUSES),
            RefundRecordReport.refund_time >= start,
            RefundRecordReport.refund_time <= end,
        ))
        or 0.0
    )

    maps = _build_route_maps(db, start, end)

    # 逐行分类(水单量通常可控; 如需更大可改 SQL 关联, 一期 Python 逐行)
    rows = list(db.scalars(base.order_by(RefundRecordReport.refund_time.desc())))
    by_route = {"A": 0, "B": 0, "C": 0, "D": 0, "unmatched": 0}
    classified = []
    for r in rows:
        route = _classify_report(r, maps)
        by_route[route] = by_route.get(route, 0) + 1
        classified.append({"report_id": r.id, "route": route, "report": r})

    matched_count = total - by_route["unmatched"]
    matched_amt = sum(
        float(r.refund_amount or 0) for rc in classified if rc["route"] != "unmatched"
        for r in [rc["report"]]
    )
    unmatched_amt = float(total_amt) - matched_amt

    # 分页输出(按 route 过滤)
    if status != "all":
        classified = [c for c in classified if c["route"] == status]
    paged = classified[(page - 1) * page_size: page * page_size]

    def _ser(c):
        r = c["report"]
        return {
            "id": r.id,
            "platform_order_no": r.platform_order_no,
            "merchant_id": r.merchant_id,
            "merchant_name": r.merchant_name,
            "device_sn": r.device_sn,
            "product_name": r.product_name,
            "refund_time": r.refund_time.isoformat() if r.refund_time else None,
            "refund_amount": float(r.refund_amount) if r.refund_amount is not None else None,
            "route": c["route"],
        }

    return {
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "total_report_count": total,
        "total_report_amount": float(total_amt),
        "matched_count": matched_count,
        "matched_amount": round(matched_amt, 2),
        "unmatched_count": by_route["unmatched"],
        "unmatched_amount": round(unmatched_amt, 2),
        "by_route": by_route,
        "page": page,
        "page_size": page_size,
        "filtered_total": len(classified),
        "items": [_ser(c) for c in paged],
    }
