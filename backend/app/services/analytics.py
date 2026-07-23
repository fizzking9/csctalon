"""分析查询服务 - 4 大看板的数据查询逻辑"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal


def _date_range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    if not end:
        end = datetime.now()
    if not start:
        start = end - timedelta(days=30)
    return start, end


# ---------- 看板1:运营分析 ----------


def operation_trend(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """退款趋势(按日):退款金额、笔数"""
    start, end = _date_range(start, end)
    rows = db.execute(
        select(
            func.to_char(Refund.created_at_raw, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(Refund.created_at_raw.between(start, end))
        .group_by("date")
        .order_by("date")
    ).all()
    return [{"date": r.date, "value": float(r.amount or 0), "count": r.count} for r in rows]


def operation_status_dist(db: Session) -> list[dict]:
    """退费状态分布"""
    rows = db.execute(
        select(Refund.status, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .group_by(Refund.status)
    ).all()
    total = sum(r[2] for r in rows) or 1
    return [
        {"name": r[0] or "(空)", "value": float(r[1] or 0), "count": r[2], "ratio": round(r[2] / total, 4)}
        for r in rows
    ]


def operation_reason_top(db: Session, limit: int = 10) -> list[dict]:
    """退费原因 TOP"""
    rows = db.execute(
        select(Refund.refund_reason, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .where(Refund.refund_reason.isnot(None))
        .group_by(Refund.refund_reason)
        .order_by(func.sum(Refund.refund_amount).desc())
        .limit(limit)
    ).all()
    return [{"name": r[0], "value": float(r[1] or 0), "count": r[2]} for r in rows]


def operation_method_dist(db: Session) -> list[dict]:
    """退款方式占比"""
    rows = db.execute(
        select(Refund.refund_method, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .group_by(Refund.refund_method)
    ).all()
    return [{"name": r[0] or "(空)", "value": float(r[1] or 0), "count": r[2]} for r in rows]


# ---------- 看板2:现金流分析 ----------


def cash_out_trend(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """真实现金流出趋势(商户号退款按日)"""
    start, end = _date_range(start, end)
    rows = db.execute(
        select(
            func.to_char(MerchantRefund.trade_time, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(MerchantRefund.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(MerchantRefund.trade_time.between(start, end), MerchantRefund.refund_amount > 0)
        .group_by("date")
        .order_by("date")
    ).all()
    return [{"date": r.date, "value": float(r.amount or 0), "count": r.count} for r in rows]


def wallet_vs_withdraw(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """退回钱包 vs 实际提现 对比(暴露提现缺口)"""
    start, end = _date_range(start, end)
    # 退回钱包: wallet 中 op_type='订单退费转入'
    wallet_in = db.execute(
        select(
            func.to_char(WalletWithdrawal.op_time, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(WalletWithdrawal.op_amount), 0).label("amount"),
        )
        .where(WalletWithdrawal.op_time.between(start, end), WalletWithdrawal.op_type == "订单退费转入")
        .group_by("date")
    ).all()
    wallet_in_map = {r.date: float(r.amount or 0) for r in wallet_in}
    # 实际提现: op_type='用户提现'
    withdraw = db.execute(
        select(
            func.to_char(WalletWithdrawal.op_time, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(WalletWithdrawal.op_amount), 0).label("amount"),
        )
        .where(WalletWithdrawal.op_time.between(start, end), WalletWithdrawal.op_type == "用户提现")
        .group_by("date")
    ).all()
    withdraw_map = {r.date: float(r.amount or 0) for r in withdraw}
    dates = sorted(set(wallet_in_map) | set(withdraw_map))
    return [
        {"date": d, "wallet_in": wallet_in_map.get(d, 0), "withdraw": withdraw_map.get(d, 0)}
        for d in dates
    ]


def merchant_cash_summary(db: Session) -> list[dict]:
    """商户号对账(按商户号)"""
    rows = db.execute(
        select(
            MerchantRefund.merchant_id,
            func.coalesce(func.sum(MerchantRefund.refund_amount), 0).label("amount"),
            func.count().label("count"),
            func.coalesce(func.sum(MerchantRefund.fee), 0).label("fee"),
        )
        .where(MerchantRefund.refund_amount > 0)
        .group_by(MerchantRefund.merchant_id)
        .order_by(func.sum(MerchantRefund.refund_amount).desc())
        .limit(50)
    ).all()
    return [
        {
            "merchant_id": r[0],
            "refund_amount": float(r[1] or 0),
            "count": r[2],
            "fee": float(r[3] or 0),
        }
        for r in rows
    ]


def wallet_balance_top(db: Session, limit: int = 20) -> list[dict]:
    """钱包余额分布(按账户,取最新余额)"""
    # 用窗口函数取每个账户最新一条记录的余额
    rows = db.execute(
        select(
            WalletWithdrawal.account_id,
            WalletWithdrawal.wallet_balance,
            WalletWithdrawal.op_time,
        )
        .order_by(WalletWithdrawal.account_id, WalletWithdrawal.op_time.desc())
        .limit(limit * 5)
    ).all()
    # 去重保留每个账户最新
    seen: dict[str, dict] = {}
    for r in rows:
        if r[0] and r[0] not in seen:
            seen[r[0]] = {"account_id": r[0], "balance": float(r[1] or 0), "op_time": r[2].isoformat() if r[2] else None}
    return list(seen.values())[:limit]


# ---------- 看板3:退款分类分析 ----------


def category_by_service_item(db: Session) -> list[dict]:
    """按服务项(退款原因分类)汇总"""
    rows = db.execute(
        select(ServiceTicket.service_item, func.coalesce(func.sum(ServiceTicket.refund_amount), 0), func.count())
        .where(ServiceTicket.service_item.isnot(None))
        .group_by(ServiceTicket.service_item)
        .order_by(func.sum(ServiceTicket.refund_amount).desc())
    ).all()
    total = sum(float(r[1] or 0) for r in rows) or 1
    return [
        {"name": r[0], "value": float(r[1] or 0), "count": r[2], "ratio": round(float(r[1] or 0) / total, 4)}
        for r in rows
    ]


def category_by_merchant(db: Session, limit: int = 20) -> list[dict]:
    """按商户汇总退款"""
    rows = db.execute(
        select(Refund.merchant_name, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .where(Refund.merchant_name.isnot(None))
        .group_by(Refund.merchant_name)
        .order_by(func.sum(Refund.refund_amount).desc())
        .limit(limit)
    ).all()
    return [{"name": r[0], "value": float(r[1] or 0), "count": r[2]} for r in rows]


def category_by_package(db: Session, limit: int = 20) -> list[dict]:
    """按套餐汇总退款"""
    rows = db.execute(
        select(Refund.package_name, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .where(Refund.package_name.isnot(None))
        .group_by(Refund.package_name)
        .order_by(func.sum(Refund.refund_amount).desc())
        .limit(limit)
    ).all()
    return [{"name": r[0], "value": float(r[1] or 0), "count": r[2]} for r in rows]


# ---------- 看板4:人员操作分析 ----------


def operator_ticket_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员工单处理统计(处理人)"""
    start, end = _date_range(start, end)
    rows = db.execute(
        select(
            ServiceTicket.handler,
            func.count().label("ticket_count"),
            func.coalesce(func.sum(ServiceTicket.refund_amount), 0).label("refund_amount"),
            func.coalesce(func.avg(ServiceTicket.handle_duration), 0).label("avg_duration"),
        )
        .where(ServiceTicket.handler.isnot(None), ServiceTicket.register_time.between(start, end))
        .group_by(ServiceTicket.handler)
        .order_by(func.count().desc())
    ).all()
    return [
        {
            "operator": r[0],
            "ticket_count": r[1],
            "refund_amount": float(r[2] or 0),
            "avg_handle_duration": round(float(r[3] or 0), 2),
        }
        for r in rows
    ]


def operator_audit_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员审核统计(审核人)"""
    start, end = _date_range(start, end)
    rows = db.execute(
        select(
            Refund.auditor,
            func.count().label("audit_count"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("audit_amount"),
        )
        .where(Refund.auditor.isnot(None), Refund.created_at_raw.between(start, end))
        .group_by(Refund.auditor)
        .order_by(func.sum(Refund.refund_amount).desc())
    ).all()
    return [
        {"operator": r[0], "audit_count": r[1], "audit_amount": float(r[2] or 0)}
        for r in rows
    ]


def operator_refund_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员退费操作统计(操作人,排除 sys/system)"""
    start, end = _date_range(start, end)
    rows = db.execute(
        select(
            Refund.operator,
            func.count().label("op_count"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("refund_amount"),
        )
        .where(
            Refund.operator.isnot(None),
            Refund.operator.notin_(["sys", "system"]),
            Refund.created_at_raw.between(start, end),
        )
        .group_by(Refund.operator)
        .order_by(func.sum(Refund.refund_amount).desc())
    ).all()
    return [
        {"operator": r[0], "op_count": r[1], "refund_amount": float(r[2] or 0)}
        for r in rows
    ]


# ---------- 总览卡片 ----------


def dashboard_summary(db: Session) -> dict[str, Any]:
    """首页汇总卡片"""
    refund_total = db.scalar(select(func.coalesce(func.sum(Refund.refund_amount), 0))) or 0
    refund_count = db.scalar(select(func.count()).select_from(Refund)) or 0
    cash_out = db.scalar(select(func.coalesce(func.sum(MerchantRefund.refund_amount), 0)).where(MerchantRefund.refund_amount > 0)) or 0
    return {
        "refund_total": float(refund_total),
        "refund_count": refund_count,
        "cash_out_total": float(cash_out),
        "wallet_balance_total": 0.0,
    }
