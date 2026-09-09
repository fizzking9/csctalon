"""现金流水统一宽表构建服务(A-20260908-004 LOCKED)

把 ABCD 经营四路每一笔出款物化为 cash_flow_wide 一行(route 区分),
服务 ①对账(A+B+C+D vs E) ②快速响应 ③运营多维分析。

口径与 analytics._cash_routes_total 保持一致(核心正确性要求):
- A: refunds 线上+已退款, 时间=created_at_raw, 金额=refund_amount
- B: wallet op_type='用户提现' 且备注非失败/冻结, 时间=op_time, 金额=op_amount
- C: shanmao status='已退款', 时间=COALESCE(audit_time, created_at), 金额=actual_amount
- D: manual is_refunded='是' 且排除 quality_status='dup_residual',
     时间=COALESCE(op_time, register_time), 金额=refund_amount

E(refund_record_reports) 不进本表; e_matched 仅作经营侧对账标记:
- A: platform_order_no 精确匹配 E.platform_order_no
- C/D: order_no 匹配 E.platform_order_no(弱)
- B: 不强匹配(钱包提现≠商户扣款记账视角, 置 None)

外部投诉按 device_sn 聚合(has_external_complaint)。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.cash_flow_wide import CashFlowWide
from app.models.external_complaint import ExternalComplaint
from app.models.manual_refund import ManualRefund
from app.models.refund import Refund
from app.models.refund_record_report import RefundRecordReport
from app.models.shanmao_refund import ShanmaoRefund
from app.models.wallet import WalletWithdrawal

logger = logging.getLogger(__name__)

# B 路失败/冻结备注关键词(与 analytics._WITHDRAW_FAIL_REMARK_KEYWORDS 一致)
_WITHDRAW_FAIL_KEYWORDS = ("冻结", "退款失败", "提现失败", "超期订单不允许退款", "驳回", "拒绝", "未到账")

# ⚠️ A-20260909-001 §2.7: 经营宽表排除的源头质量状态集合(与 analytics._EXCL_QSTATUS_SQL 一致)
# dup_residual=已治理重复; ignored=人工彻底忽略(A-20260909-001 新增, 由质量页 ignore 动作置位)
_EXCLUDED_QUALITY = ("dup_residual", "ignored")

_BATCH_SIZE = 5000


def build_cash_flow_wide(db: Session, progress_callback=None) -> dict:
    """全量重建 cash_flow_wide(清空后按 ABCD 四路物化)。

    Returns:
        {"a": n, "b": n, "c": n, "d": n, "total": n}
    """
    # 1. 清空(纯派生表, 重建安全)
    db.execute(CashFlowWide.__table__.delete())
    db.commit()

    # 2. 预载 E 对账键集
    e_order_keys: set[str] = set(db.scalars(
        select(RefundRecordReport.platform_order_no)
        .where(RefundRecordReport.refund_status.in_(("退款成功", "成功", "SUCCESS")))
    ))

    # 3. 外部投诉 SN 集
    ec_sns: set[str] = set(db.scalars(
        select(ExternalComplaint.device_sn).where(ExternalComplaint.device_sn.isnot(None))
    ))

    now = datetime.now(timezone.utc)

    def _flush(rows: list[dict]) -> None:
        if not rows:
            return
        # 全量重建已先清空整表, 直接批量插入(幂等: 任一步失败下次重建可重来)
        db.execute(pg_insert(CashFlowWide).values(rows))
        db.commit()
        rows.clear()

    counts = {"a": 0, "b": 0, "c": 0, "d": 0}
    rows: list[dict] = []

    # ── A: refunds 线上+已退款(created_at_raw 非空, 与 analytics _between 全表口径一致) ──
    a_rows = db.execute(
        select(
            Refund.refund_no, Refund.platform_order_no, Refund.device_sn,
            Refund.refund_amount, Refund.created_at_raw, Refund.operator,
            Refund.refund_reason, Refund.merchant_name, Refund.package_name,
            Refund.auditor, Refund.quality_status,
        ).where(
            Refund.refund_method == "线上",
            Refund.status == "已退款",
            Refund.created_at_raw.isnot(None),
        )
    ).all()
    for r in a_rows:
        if (r.quality_status or "") in _EXCLUDED_QUALITY:
            continue  # dup_residual/ignored 不纳入经营宽表(A-20260909-001)
        rows.append({
            "route": "A",
            "src_id": r.refund_no,
            "biz_no": r.refund_no,
            "pay_time": r.created_at_raw,
            "pay_amount": r.refund_amount,
            "device_sn": r.device_sn,
            "operator": r.operator,
            "e_key": r.platform_order_no,
            "e_matched": (r.platform_order_no in e_order_keys) if r.platform_order_no else False,
            "reason": r.refund_reason,
            "merchant": r.merchant_name,
            "package": r.package_name,
            "has_external_complaint": (r.device_sn in ec_sns) if r.device_sn else False,
            "auditor": r.auditor,
            "matched_at": now,
        })
        counts["a"] += 1
        if len(rows) >= _BATCH_SIZE:
            _flush(rows)
    _flush(rows)

    # ── B: wallet 用户提现(排除失败/冻结) ──
    # 注: wallet.operator 恒为 op_type 文本('用户提现'), 非人名 → B 路 operator 置 None
    #     (经手人分析主要看 A/C/D; B 路是系统/用户钱包提现, 无客服经手人语义, 见 L-4)
    b_rows = db.execute(
        select(
            WalletWithdrawal.id, WalletWithdrawal.device_sn,
            WalletWithdrawal.op_amount, WalletWithdrawal.op_time,
            WalletWithdrawal.remark,
            WalletWithdrawal.account_id, WalletWithdrawal.quality_status,
        ).where(
            WalletWithdrawal.op_type == "用户提现",
        )
    ).all()
    b_rows = [w for w in b_rows if not any(k in (w.remark or "") for k in _WITHDRAW_FAIL_KEYWORDS)]
    b_rows = [w for w in b_rows if (w.quality_status or "") not in _EXCLUDED_QUALITY]  # dup/ignored 排除
    # B 路 biz_no: 尝试从 remark 解析退费单号(尽力而为; 前缀含 RF/RE/RB, 2026-09-09 扩)
    import re
    _rf_re = re.compile(r"R[FEB]\d{10,}")
    for w in b_rows:
        m = _rf_re.search(w.remark or "")
        rows.append({
            "route": "B",
            "src_id": f"W{w.id}",
            "biz_no": m.group(0) if m else None,
            "pay_time": w.op_time,
            "pay_amount": w.op_amount,
            "device_sn": w.device_sn,
            "operator": None,  # wallet.operator 恒为 op_type 文本, 非人名(L-4)
            "e_key": m.group(0) if m else None,
            "e_matched": (m.group(0) in e_order_keys) if m else None,  # 弱关联, 未命中置 None
            "reason": None,
            "merchant": None,
            "package": None,
            "has_external_complaint": (w.device_sn in ec_sns) if w.device_sn else False,
            "auditor": None,
            "matched_at": now,
        })
        counts["b"] += 1
        if len(rows) >= _BATCH_SIZE:
            _flush(rows)
    _flush(rows)

    # ── C: shanmao 已退款 ──
    c_rows = db.execute(
        select(
            ShanmaoRefund.refund_no, ShanmaoRefund.order_no, ShanmaoRefund.device_sn,
            ShanmaoRefund.actual_amount, ShanmaoRefund.audit_time, ShanmaoRefund.created_at,
            ShanmaoRefund.operator, ShanmaoRefund.auditor, ShanmaoRefund.refund_reason,
            ShanmaoRefund.merchant_name, ShanmaoRefund.package_name, ShanmaoRefund.quality_status,
        ).where(ShanmaoRefund.status == "已退款")
    ).all()
    for r in c_rows:
        if (r.quality_status or "") in _EXCLUDED_QUALITY:
            continue  # dup_residual/ignored 不纳入经营宽表(A-20260909-001)
        pay_time = r.audit_time or r.created_at
        rows.append({
            "route": "C",
            "src_id": r.refund_no,
            "biz_no": r.refund_no or r.order_no,
            "pay_time": pay_time,
            "pay_amount": r.actual_amount,
            "device_sn": r.device_sn,
            "operator": r.operator or r.auditor,
            "e_key": r.order_no,
            "e_matched": (r.order_no in e_order_keys) if r.order_no else False,
            "reason": r.refund_reason,
            "merchant": r.merchant_name,
            "package": r.package_name,
            "has_external_complaint": (r.device_sn in ec_sns) if r.device_sn else False,
            "auditor": r.auditor,
            "matched_at": now,
        })
        counts["c"] += 1
        if len(rows) >= _BATCH_SIZE:
            _flush(rows)
    _flush(rows)

    # ── D: manual 已退款(排除 dup_residual/ignored) ──
    d_rows = db.execute(
        select(
            ManualRefund.id, ManualRefund.order_no, ManualRefund.device_sn,
            ManualRefund.refund_amount, ManualRefund.op_time, ManualRefund.register_time,
            ManualRefund.refund_operator, ManualRefund.registrant,
            ManualRefund.remark, ManualRefund.quality_status,
            ManualRefund.merchant_id,
        ).where(ManualRefund.is_refunded == "是")
    ).all()
    for r in d_rows:
        if (r.quality_status or "") in _EXCLUDED_QUALITY:
            continue  # dup_residual(已治理重复)/ignored(人工忽略) 不纳入经营估算(与 analytics 一致)
        pay_time = r.op_time or r.register_time
        rows.append({
            "route": "D",
            "src_id": f"M{r.id}",
            "biz_no": r.order_no,
            "pay_time": pay_time,
            "pay_amount": r.refund_amount,
            "device_sn": r.device_sn,
            "operator": r.refund_operator or r.registrant,
            "e_key": r.order_no,
            "e_matched": (r.order_no in e_order_keys) if r.order_no else False,
            "reason": None,
            "merchant": None,
            "package": None,
            "has_external_complaint": (r.device_sn in ec_sns) if r.device_sn else False,
            "auditor": r.registrant,
            "matched_at": now,
        })
        counts["d"] += 1
        if len(rows) >= _BATCH_SIZE:
            _flush(rows)
    _flush(rows)

    # 4. 统计
    total = counts["a"] + counts["b"] + counts["c"] + counts["d"]
    logger.info(f"cash_flow_wide 构建完成: A={counts['a']} B={counts['b']} "
                f"C={counts['c']} D={counts['d']} total={total}")
    return {"a": counts["a"], "b": counts["b"], "c": counts["c"], "d": counts["d"], "total": total}
