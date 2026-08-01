"""分析查询服务 - 4 大看板的数据查询逻辑"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select, union
from sqlalchemy.orm import Session

from app.models.cs_record import CsRecord
from app.models.merchant_refund import MerchantRefund
from app.models.person import Person, PersonAlias
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.todo import TodoItem
from app.models.task import SpecialTask
from app.models.incident import Incident
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
# 业务定义:
#   直接退款 = 退款单中 refund_method='线上' 且 operator 为人工(非 sys/system)
#              → 客服在线操作,钱直接退回用户支付账户,构成真金白银流出
#   用户提现 = 提现表中 op_type='用户提现'
#              → 退到会员钱包后用户再提现,同样是真金白银流出
#   真实现金流 = 直接退款 + 用户提现
#   ("转入提现账户"不算直接流出,钱进了钱包,等用户提现时才计入)


def _direct_refund_filter():
    """直接退款过滤条件:线上 + 人工操作(排除 sys/system 自动退款)"""
    return (
        Refund.refund_method == "线上",
        Refund.operator.isnot(None),
        Refund.operator.notin_(["sys", "system", "SYS", "System"]),
    )


def _real_cash_out_total(db: Session) -> tuple[float, float]:
    """真实现金流总额 = 直接退款 + 用户提现,返回 (直接退款, 用户提现)"""
    # 直接退款(线上+人工)
    direct = db.scalar(
        select(func.coalesce(func.sum(Refund.refund_amount), 0)).where(*_direct_refund_filter())
    ) or 0
    # 用户提现
    withdraw = db.scalar(
        select(func.coalesce(func.sum(WalletWithdrawal.op_amount), 0)).where(
            WalletWithdrawal.op_type == "用户提现"
        )
    ) or 0
    return float(direct), float(withdraw)


def cash_out_trend(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """真实现金流出趋势(按日)= 直接退款 + 用户提现

    两部分按日聚合后合并:direct(直接退款) + withdraw(用户提现) = total(真实现金流)
    """
    start, end = _date_range(start, end)
    # 直接退款按日
    direct_rows = db.execute(
        select(
            func.to_char(Refund.created_at_raw, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(*_direct_refund_filter(), Refund.created_at_raw.between(start, end))
        .group_by("date")
    ).all()
    direct_map = {r.date: {"amount": float(r.amount or 0), "count": r.count} for r in direct_rows}
    # 用户提现按日
    withdraw_rows = db.execute(
        select(
            func.to_char(WalletWithdrawal.op_time, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(WalletWithdrawal.op_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(WalletWithdrawal.op_time.between(start, end), WalletWithdrawal.op_type == "用户提现")
        .group_by("date")
    ).all()
    withdraw_map = {r.date: {"amount": float(r.amount or 0), "count": r.count} for r in withdraw_rows}
    # 商户号现金流(实际)按日:微信侧真实扣款,trade_status='REFUND' 的退款金额
    merchant_rows = db.execute(
        select(
            func.to_char(MerchantRefund.trade_time, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(MerchantRefund.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(MerchantRefund.trade_status == "REFUND", MerchantRefund.trade_time.between(start, end))
        .group_by("date")
    ).all()
    merchant_map = {r.date: {"amount": float(r.amount or 0), "count": r.count} for r in merchant_rows}
    # 合并:direct+withdraw 为已登记估算(组成部分),merchant 为实际值,两者差额=闪猫+其他退款
    dates = sorted(set(direct_map) | set(withdraw_map) | set(merchant_map))
    return [
        {
            "date": d,
            "direct": direct_map.get(d, {"amount": 0})["amount"],
            "direct_count": direct_map.get(d, {"count": 0})["count"],
            "withdraw": withdraw_map.get(d, {"amount": 0})["amount"],
            "withdraw_count": withdraw_map.get(d, {"count": 0})["count"],
            "total": direct_map.get(d, {"amount": 0})["amount"] + withdraw_map.get(d, {"amount": 0})["amount"],
            "merchant": merchant_map.get(d, {"amount": 0})["amount"],
            "merchant_count": merchant_map.get(d, {"count": 0})["count"],
        }
        for d in dates
    ]


def wallet_vs_withdraw(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """退回钱包 vs 实际提现 对比(暴露提现缺口)

    - 钱包转入(订单退费转入):钱进了会员钱包,尚未流出
    - 用户提现:钱从钱包提出,构成真金白银流出
    两者的差额即为"退到钱包但尚未提现"的沉淀资金
    """
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


def cash_out_breakdown(db: Session) -> dict[str, Any]:
    """真实现金流构成明细:已登记估算(直接退款+用户提现) vs 商户号实际现金流

    业务关系:商户号现金流(微信侧真实扣款)= 包含 直接退款 + 用户提现 + 闪猫退款 + 其他退款
    因此这里分别给"已登记估算"和"商户号实际",差额=尚未登记的闪猫/其他退款
    """
    direct, withdraw = _real_cash_out_total(db)
    # 商户号现金流(实际):trade_status='REFUND' 的退款金额总和(微信侧真实扣款)
    merchant_cash = db.scalar(
        select(func.coalesce(func.sum(MerchantRefund.refund_amount), 0))
        .where(MerchantRefund.trade_status == "REFUND")
    ) or 0
    merchant_cash = float(merchant_cash)
    estimated = direct + withdraw
    return {
        "direct_refund": direct,  # 直接退款(线上+人工)
        "user_withdraw": withdraw,  # 用户提现
        "total": estimated,  # 已登记估算现金流(组成部分之和)
        "merchant_cash": merchant_cash,  # 商户号实际现金流(微信侧真实扣款)
        "diff": round(merchant_cash - estimated, 2),  # 偏差=实际-估算(闪猫+其他未登记退款)
    }


def merchant_cash_summary(db: Session) -> list[dict]:
    """商户号现金流对账(按商户号):仅统计 trade_status='REFUND' 的实际退款"""
    rows = db.execute(
        select(
            MerchantRefund.merchant_id,
            func.coalesce(func.sum(MerchantRefund.refund_amount), 0).label("amount"),
            func.count().label("count"),
            func.coalesce(func.sum(MerchantRefund.fee), 0).label("fee"),
        )
        .where(MerchantRefund.trade_status == "REFUND")
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
    # 已登记估算现金流 = 直接退款(线上+人工) + 用户提现(商户号实际现金流的组成部分)
    direct, withdraw = _real_cash_out_total(db)
    estimated = direct + withdraw
    # 商户号实际现金流(微信侧真实扣款,包含直接退款/用户提现/闪猫/其他退款)
    merchant_cash = db.scalar(
        select(func.coalesce(func.sum(MerchantRefund.refund_amount), 0))
        .where(MerchantRefund.trade_status == "REFUND")
    ) or 0
    merchant_cash = float(merchant_cash)
    # 钱包余额总计(各账户最新余额之和)
    wallet_balance = db.scalar(
        select(func.coalesce(func.sum(WalletWithdrawal.wallet_balance), 0))
    ) or 0
    # 客服接待量(智能助手处理记录数)
    cs_reception_count = db.scalar(select(func.count()).select_from(CsRecord)) or 0
    return {
        "refund_total": float(refund_total),  # 退款总金额(含非现金,如退回钱包/无需退款)
        "refund_count": refund_count,
        "cash_out_total": estimated,  # 已登记估算现金流(直接退款+用户提现)
        "cash_out_direct": direct,  # 直接退款(线上+人工)
        "cash_out_withdraw": withdraw,  # 用户提现
        "merchant_cash": merchant_cash,  # 商户号实际现金流(微信侧真实扣款)
        "cash_diff": round(merchant_cash - estimated, 2),  # 偏差=实际-估算(闪猫+其他未登记退款)
        "wallet_balance_total": float(wallet_balance),
        "cs_reception_count": cs_reception_count,
    }


# ---------- 看板5: 客服接待看板 (对齐周报4模块) ----------

# 接待分项分类(对齐周报:投诉/退款/其他问题/套餐问题/咨询/综合问题)
# cs_record.problem_category 实际值可能不完全一致,此处做归并映射
_CATEGORY_BUCKETS = [
    ("投诉", ["投诉"]),
    ("退款", ["退款"]),
    ("套餐问题", ["套餐", "套餐问题"]),
    ("咨询", ["咨询", "网络问题"]),
    ("综合问题", ["综合", "综合问题", "其他"]),
    ("其他问题", []),  # 兜底:非上述任一的非空值 OR 空值
]


def _category_case():
    """返回 CASE WHEN 表达式把 problem_category 映射到 6 个周报复分类
    不匹配任何关键词的未知值 / NULL 统一归入最后一个兜底桶("其他问题")
    """
    whens = []
    for bucket, keywords in _CATEGORY_BUCKETS[:-1]:  # 除最后一项"其他问题"(兜底)
        if keywords:
            for kw in keywords:
                whens.append((CsRecord.problem_category.ilike(f"%{kw}%"), bucket))
    fallback = _CATEGORY_BUCKETS[-1][0]  # "其他问题"
    return case(*whens, else_=fallback).label("category_bucket")


def _operator_to_person_cte():
    """返回 operator → display_name, department, group_name, specialty, position 的子查询 CTE

    通过 PersonAlias.alias (归一化匹配) → Person。找不到则 operator 作为 display_name。
    """
    return (
        select(
            PersonAlias.alias.label("operator"),
            func.coalesce(Person.display_name, PersonAlias.alias).label("display_name"),
            Person.department.label("department"),
            Person.group_name.label("group_name"),
            Person.specialty.label("specialty"),
            Person.position.label("position"),
        )
        .select_from(PersonAlias)
        .outerjoin(Person, PersonAlias.person_id == Person.id)
        .cte(name="op_person")
    )


def reception_overview_trend(
    db: Session, start: datetime | None = None, end: datetime | None = None
) -> list[dict[str, Any]]:
    """模块1: 总体接待量 - 按周聚合

    输出: [{week_start, week_no, reception_total, day_count, daily_avg,
            staff_count, per_capita_weekly, per_capita_daily}]

    - week_start: 周一起始日(ISO)
    - week_no: 当年周号 (IW)
    - day_count: 本周有数据的天数 (用于日均计算)
    - staff_count: 本周实际有接待的 distinct 操作人数
    - per_capita_weekly: 人均接待量/周 = reception_total / staff_count
    - per_capita_daily: 人均接待量/天 = reception_total / staff_count / day_count
    """
    start, end = _date_range(start, end)
    week_trunc = func.date_trunc("week", CsRecord.operation_time).label("week_start")
    rows = db.execute(
        select(
            week_trunc,
            func.to_char(func.date_trunc("week", CsRecord.operation_time), "IW").label("week_no"),
            func.count().label("total"),
            func.count(func.distinct(func.to_char(CsRecord.operation_time, "YYYY-MM-DD"))).label("day_count"),
            func.count(func.distinct(CsRecord.operator)).filter(CsRecord.operator.isnot(None)).label("staff_count"),
        )
        .where(CsRecord.operation_time.between(start, end))
        .group_by("week_start")
        .order_by("week_start")
    ).all()
    result = []
    for r in rows:
        total = int(r.total or 0)
        day_count = int(r.day_count or 1) or 1
        staff_count = int(r.staff_count or 0) or 0
        result.append({
            "week_start": r.week_start.strftime("%Y-%m-%d") if r.week_start else None,
            "week_no": int(r.week_no or 0),
            "reception_total": total,
            "day_count": day_count,
            "daily_avg": round(total / day_count, 2) if day_count else 0,
            "staff_count": staff_count,
            "per_capita_weekly": round(total / staff_count, 2) if staff_count else 0,
            "per_capita_daily": round(total / staff_count / day_count, 2) if staff_count and day_count else 0,
        })
    return result


def reception_category_summary(
    db: Session, start: datetime | None = None, end: datetime | None = None
) -> list[dict[str, Any]]:
    """模块2: 接待分项汇总 - 按周 × 6 个分类 (投诉/退款/其他问题/套餐问题/咨询/综合问题)

    输出: [{week_start, week_no, 投诉, 退款, 其他问题, 套餐问题, 咨询, 综合问题}]
    """
    start, end = _date_range(start, end)
    week_trunc = func.date_trunc("week", CsRecord.operation_time).label("week_start")
    bucket = _category_case()
    rows = db.execute(
        select(
            week_trunc,
            func.to_char(func.date_trunc("week", CsRecord.operation_time), "IW").label("week_no"),
            bucket,
            func.count().label("cnt"),
        )
        .where(CsRecord.operation_time.between(start, end))
        .group_by("week_start", "category_bucket")
        .order_by("week_start", "category_bucket")
    ).all()
    # 按周归并
    per_week: dict[str, dict] = {}
    order = ["投诉", "退款", "其他问题", "套餐问题", "咨询", "综合问题"]
    for r in rows:
        ws = r.week_start.strftime("%Y-%m-%d") if r.week_start else ""
        if ws not in per_week:
            per_week[ws] = {
                "week_start": ws,
                "week_no": int(r.week_no or 0),
                **{k: 0 for k in order},
            }
        cat = r.category_bucket if r.category_bucket in order else "其他问题"
        per_week[ws][cat] += int(r.cnt or 0)
    return sorted(per_week.values(), key=lambda x: x["week_start"])


def reception_per_capita(
    db: Session, start: datetime | None = None, end: datetime | None = None
) -> list[dict[str, Any]]:
    """模块3: 接待分项人均工作 - 按周 × 按专项(投诉/退款/套餐/咨询)分组计算组别人数+人均

    每人按"本周接待量最大的分类"归入对应专项组。
    输出: [{week_start, week_no,
            complaint_total, complaint_staff, complaint_pc,
            refund_total, refund_staff, refund_pc,
            package_total, package_staff, package_pc,
            consult_total, consult_staff, consult_pc}]
    - *_total: 该专项本周总接待量
    - *_staff: 该专项本周人数(归入该专项组的操作人数)
    - *_pc: 该专项本周人均接待量 = total / staff (对齐周报表格数值)
    """
    start, end = _date_range(start, end)
    bucket = _category_case()

    # 步骤1: 按周 × operator × bucket 聚合每人每类接待数
    op_cat_rows = db.execute(
        select(
            func.date_trunc("week", CsRecord.operation_time).label("week_start"),
            func.to_char(func.date_trunc("week", CsRecord.operation_time), "IW").label("week_no"),
            CsRecord.operator.label("operator"),
            bucket,
            func.count().label("cnt"),
        )
        .where(CsRecord.operation_time.between(start, end), CsRecord.operator.isnot(None))
        .group_by("week_start", "week_no", "operator", "category_bucket")
    ).all()

    # 步骤2: 每人每周围攻分类(接待量最大的 bucket) → 归入对应专项组
    # 同时按周按 bucket 汇总 total_cnt 和 distinct staff
    SPECIALTY_ORDER = [
        ("投诉", "complaint"),
        ("退款", "refund"),
        ("套餐问题", "package"),
        ("咨询", "consult"),
        ("综合问题", "consult"),  # 综合问题并入咨询组
        ("其他问题", "consult"),  # 其他问题并入咨询组
    ]

    def bucket_to_specialty(b):
        for src, dst in SPECIALTY_ORDER:
            if b == src:
                return dst
        return "consult"

    # 临时存储: per_week[ws][specialty] → {"total": int, "staff": set()}
    per_week: dict[str, dict[str, dict]] = {}
    per_op_week: dict[tuple, dict[str, int]] = {}  # (week_start, operator) -> {bucket: cnt}
    for r in op_cat_rows:
        key = (str(r.week_start), r.operator)
        per_op_week.setdefault(key, {})[r.category_bucket] = int(r.cnt or 0)

    for (ws_str, operator), buckets in per_op_week.items():
        # 找该操作员本周接待量最大的分类
        dom_bucket, dom_cnt = max(buckets.items(), key=lambda x: x[1])
        specialty = bucket_to_specialty(dom_bucket)
        ws = ws_str
        per_week.setdefault(ws, {"complaint": {"total": 0, "staff": set()},
                                 "refund": {"total": 0, "staff": set()},
                                 "package": {"total": 0, "staff": set()},
                                 "consult": {"total": 0, "staff": set()}})
        # 该操作员接待的所有数据计入其主攻专项组
        for b, c in buckets.items():
            s = bucket_to_specialty(b)
            per_week[ws][s]["total"] += c
        # 操作员本身加入其主攻组 staff
        per_week[ws][specialty]["staff"].add(operator)

    # 构建结果
    result = []
    # 额外查询每周 week_no (用于输出)
    for ws, buckets in per_week.items():
        # 从 ws 反推 week_start 对象 -> week_no
        try:
            import datetime as _dt
            week_dt = _dt.datetime.strptime(ws.split(".")[0][:10], "%Y-%m-%d")
            week_no = int(week_dt.strftime("%V"))
        except Exception:
            week_no = 0
        row = {
            "week_start": ws.split(".")[0][:10],
            "week_no": week_no,
        }
        for sn, cn_name in [
            ("complaint", "投诉"),
            ("refund", "退款"),
            ("package", "套餐问题"),
            ("consult", "咨询"),
        ]:
            total = buckets[sn]["total"]
            staff = len(buckets[sn]["staff"])
            row[f"{sn}_total"] = total
            row[f"{sn}_staff"] = staff
            row[f"{sn}_pc"] = round(total / staff, 2) if staff else 0
        # 综合问题并入咨询,兼容列名(前端不使用此列,留作扩展)
        row["other_total"] = 0
        row["other_staff"] = 0
        row["other_pc"] = 0
        result.append(row)
    return sorted(result, key=lambda x: x["week_start"])


def reception_group_detail(
    db: Session, start: datetime | None = None, end: datetime | None = None
) -> dict[str, Any]:
    """模块4: 各组接待 - 按组名 × 人员的日接待明细 + 平均

    输出:
    {
      groups: [
        {
          group_name: "董梦玲组",
          people: [
            {
              display_name: "某某",
              category_type: "咨询",       # 该人员主攻接待类型(本周接待量最大的分类)
              daily: {"2026-07-06": 123, ...},
              daily_list: [123, ...],     # 按 start..end 日期顺序排序
              date_labels: ["07/06", ...],
              avg: 123.4,
              total: 863
            }, ...
          ],
          group_avg: 100.2,            # 组内人员平均接待量(每人的平均的平均)
          group_total: 12345           # 组内总计接待量
        }, ...
      ],
      ungrouped: [ ...same as people... ],   # 未分组人员
      date_labels: ["07/05", ...],           # 本周期日期标签(MM/DD)
      date_keys: ["2026-07-05", ...]         # 本周期完整日期
    }

    时间范围:默认取最近一周(7天),若 start/end 传入则按其范围。
    group_name 来源: Person.group_name(归并后)。operator 无法归并的人员放入 ungrouped。
    """
    start, end = _date_range(start, end)

    # 构建本周期完整日期列表(用于对齐 daily_list)
    def daterange(s, e):
        cur = s.date() if hasattr(s, "date") else s
        end_d = e.date() if hasattr(e, "date") else e
        while cur <= end_d:
            yield cur
            cur += timedelta(days=1)
    date_keys = [d.isoformat() for d in daterange(start, end)]
    date_labels = [d[5:] for d in date_keys]  # MM-DD

    op_person = _operator_to_person_cte()

    # 按 (operator, display_name, group_name, specialty, position, date, category_bucket) 聚合
    bucket = _category_case()
    rows = db.execute(
        select(
            CsRecord.operator.label("operator"),
            op_person.c.display_name.label("display_name"),
            op_person.c.group_name.label("group_name"),
            op_person.c.specialty.label("specialty"),
            func.to_char(CsRecord.operation_time, "YYYY-MM-DD").label("day"),
            bucket,
            func.count().label("cnt"),
        )
        .outerjoin(op_person, CsRecord.operator == op_person.c.operator)
        .where(CsRecord.operation_time.between(start, end), CsRecord.operator.isnot(None))
        .group_by(
            CsRecord.operator,
            op_person.c.display_name,
            op_person.c.group_name,
            op_person.c.specialty,
            "day",
            "category_bucket",
        )
    ).all()

    # 归并: per_person[display_name or operator]
    per_person: dict[str, dict] = {}
    person_info: dict[str, dict] = {}  # 保存 group/specialty 等
    for r in rows:
        key = r.display_name or r.operator
        per_person.setdefault(key, {})
        per_person[key][r.day] = per_person[key].get(r.day, 0) + int(r.cnt or 0)
        # 记录人员基本信息 + 每日分类信息(用于推断 category_type)
        info = person_info.setdefault(key, {
            "group_name": r.group_name,
            "specialty": r.specialty,
            "category_counts": {},
        })
        info["category_counts"][r.category_bucket] = (
            info["category_counts"].get(r.category_bucket, 0) + int(r.cnt or 0)
        )

    # 构建 people 对象列表
    groups_map: dict[str, list] = {}
    ungrouped = []
    for name, daily in per_person.items():
        total = sum(daily.values())
        day_count_nonzero = sum(1 for d in date_keys if daily.get(d, 0) > 0) or 1
        avg = round(total / day_count_nonzero, 2)
        daily_list = [daily.get(d, 0) for d in date_keys]
        info = person_info.get(name, {})
        # 推断 category_type: 从 category_counts 取最大的分类
        cc = info.get("category_counts", {})
        if cc:
            category_type = max(cc.items(), key=lambda x: x[1])[0]
        else:
            category_type = info.get("specialty") or "咨询"
        person_obj = {
            "name": name,
            "category_type": category_type,
            "total": total,
            "avg": avg,
            "daily": daily,
            "daily_list": daily_list,
        }
        gn = info.get("group_name")
        if gn:
            groups_map.setdefault(gn, []).append(person_obj)
        else:
            ungrouped.append(person_obj)

    # 组排序 + 组内按 total desc; 计算组均值/总计
    group_list = []
    for gn in sorted(groups_map.keys()):
        plist = sorted(groups_map[gn], key=lambda p: p["total"], reverse=True)
        g_total = sum(p["total"] for p in plist)
        g_avg = round(sum(p["avg"] for p in plist) / len(plist), 2) if plist else 0
        group_list.append({
            "group_name": gn,
            "people": plist,
            "group_total": g_total,
            "group_avg": g_avg,
        })

    ungrouped_sorted = sorted(ungrouped, key=lambda p: p["total"], reverse=True)

    return {
        "date_keys": date_keys,
        "date_labels": date_labels,
        "groups": group_list,
        "ungrouped": ungrouped_sorted,
    }


# ---------- 看板5: 退款-提现全链路看板 ----------
# 串联: refunds(发起退款) → wallet_withdrawals(订单退费转入=入钱包) → wallet_withdrawals(用户提现=实际出款)
# 两条路径:
#   A 转入提现账户 → 现金真正到账的时刻 = 用户提现 op_time
#   B 线上/线下/微信/支付宝直退 → 现金真正到账的时刻 = refunds updated_at_raw (接口回调成功)

import re as _re

_RFN_RE = _re.compile(r'退费单\s*([A-Za-z0-9]+)')
_TIME_BINS = [
    ("<0异常", -1e9, 0),
    ("=0天(秒级)", 0, 0),
    ("0~1天", 0, 1),
    ("1~3天", 1, 3),
    ("3~7天", 3, 7),
    ("7~15天", 7, 15),
    ("15~30天", 15, 30),
    ("30天+", 30, 1e9),
]


def _bin_label(days: float) -> str:
    if days < 0:
        return "<0异常"
    if days == 0:
        return "=0天(秒级)"
    for lbl, lo, hi in _TIME_BINS[2:]:
        if lo < days <= hi:
            return lbl
    return "30天+"


def _naive(dt: datetime | None) -> datetime | None:
    """把 timestamptz 感知时间统一转成 naive(去掉 tzinfo), 用于和 fromisoformat 得到的 naive 日期进行比较."""
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        # 取 DB 层存储的"墙上时间"直接剥离 tz, 不做 UTC 换算
        # (数据库写入和本地查询都在同一个 TZ 语境下做日粒度筛选, 这是最稳定的口径)
        return dt.replace(tzinfo=None)
    return dt


# 有效退款方式: A = 转入提现账户 (退款到钱包); B = 直接线上退款 (线上/微信/支付宝/银联/银行卡/直退 等线上渠道关键词)
_B_METHOD_KEYS = ("线上", "微信", "支付宝", "银联", "银行卡", "直退")


def _classify_method(m: str | None) -> str | None:
    """返回 'A' / 'B' / None(无效方法, 从看板剔除)."""
    if not m:
        return None
    if "转入" in m:
        return "A"
    if any(k in m for k in _B_METHOD_KEYS):
        return "B"
    return None  # 线下 / 未填写 / 其他 → 非实际现金退款路径, 剔除


def _build_chain_rows(db: Session, start: datetime | None = None, end: datetime | None = None):
    """构建"一笔实际出款"的全链路记录 (只有 A=转入钱包+B=线上直退两条有效路径, 其他无效剔除)。

    筛选时间窗口作用在 **D4 (实际现金出款时刻)**, 而非 D1 发起退款时刻:
      - A 路径 D4 = 用户提现 op_time (钱包真实出款给用户的时刻)
      - B 路径 D4 = 退费单 updated_at_raw (接口回调成功 ≈ 实际到账)
    """
    # 1. 取全量数据 (66k rows, 内存可容纳)
    all_rf = list(db.execute(select(Refund)).scalars().all())
    all_wd = list(db.execute(select(WalletWithdrawal).where(WalletWithdrawal.op_type == "用户提现")).scalars().all())
    all_wi = list(db.execute(select(WalletWithdrawal).where(WalletWithdrawal.op_type == "订单退费转入")).scalars().all())

    # 所有 datetime 先统一 naive, 避免 tz 比较报错
    for r in all_rf:
        r.created_at_raw = _naive(r.created_at_raw)
        r.created_at = _naive(r.created_at)
        r.updated_at_raw = _naive(r.updated_at_raw)
        r.updated_at = _naive(r.updated_at)
    for w in all_wd + all_wi:
        w.op_time = _naive(w.op_time)

    # 2. 按 refund_no 建 用户提现 索引
    wd_by_rfn: dict[str, list] = {}
    for w in all_wd:
        m = _RFN_RE.search(w.remark or "")
        if not m:
            continue
        wd_by_rfn.setdefault(m.group(1), []).append(w)

    # 3. 订单退费转入 按 (account_id, device_sn, amount) 建桶 (贪心匹配A路径的入钱包时刻)
    from collections import defaultdict as _dd
    wi_bucket: dict[tuple, list] = _dd(list)
    for w in all_wi:
        sn = (w.device_sn or "").strip() or ""
        key = ((w.account_id or ""), sn, round(float(w.op_amount or 0), 2))
        wi_bucket[key].append(w)
    for k in wi_bucket:
        wi_bucket[k].sort(key=lambda x: x.op_time or datetime.min)

    rows: list[dict[str, Any]] = []
    for r in all_rf:
        m_raw = (r.refund_method or "").strip() or "未填写"
        cat = _classify_method(m_raw)
        if cat is None:
            continue  # 线下/未填写/其他 → 非实际现金退款, 从看板统计剔除

        amt = round(float(r.refund_amount or 0), 2)
        d1 = r.created_at_raw or r.created_at
        if not d1:
            continue

        # 分类 A / B
        if cat == "A":
            best_wd = None
            for w in wd_by_rfn.get(r.refund_no, []):
                if (w.op_time or datetime.min) < d1:
                    continue
                if abs(float(w.op_amount or 0) - amt) >= 0.01:
                    continue
                if best_wd is None or w.op_time < best_wd.op_time:
                    best_wd = w
            # 兜底: device_sn + 金额
            if best_wd is None and r.device_sn:
                sn = r.device_sn.strip()
                for w in all_wd:
                    if (w.device_sn or "").strip() != sn:
                        continue
                    if abs(float(w.op_amount or 0) - amt) >= 0.01:
                        continue
                    if (w.op_time or datetime.min) < d1:
                        continue
                    if best_wd is None or w.op_time < best_wd.op_time:
                        best_wd = w
            if best_wd is None:
                continue  # A路径没找到用户提现行 → 无真实出款 → 不计入出款看板
            d_out = best_wd.op_time
            # 匹配对应的 订单退费入钱包时刻 (D2)
            d2 = None
            sn_key = (best_wd.device_sn or "").strip() or (r.device_sn or "").strip() or ""
            key = ((best_wd.account_id or ""), sn_key, amt)
            key2 = ((best_wd.account_id or ""), "", amt)
            for cand in (key, key2):
                for w in wi_bucket.get(cand, []):
                    if getattr(w, "_used", False):
                        continue
                    if (w.op_time or datetime.min) < d1:
                        continue
                    if w.op_time > (d_out or datetime.max):
                        continue
                    d2 = w.op_time
                    w._used = True  # type: ignore[attr-defined]
                    break
                if d2:
                    break
            withdraw_id = best_wd.id
            withdraw_remark = best_wd.remark or ""
            account_id = best_wd.account_id or ""
            device_sn = (best_wd.device_sn or r.device_sn or "").strip() or None
        else:  # cat == "B"
            # 直接线上退款 (线上/微信/支付宝...): 真实到账是"接口回调成功"
            # 由于没有单独的 refunded_at 字段, 用 updated_at_raw 作近似, 但 updated_at_raw 会被后续改单/同步污染
            # → 经验口径: 直接退款的真实回调到账不可能晚于 D1 超过 3 天.
            #   若 updated_at_raw 在 (D1, D1+3天] 以内 → 视为有效回调时间, 采纳;
            #   若 updated_at_raw 超过 D1+3 天 → 判定为后续改单/同步时间戳, 回落到 D1 近似秒级到账;
            #   若 updated_at_raw 早于 D1 → 用 D1.
            MAX_B_DAYS = 3
            d2_upd = r.updated_at_raw or r.updated_at or d1
            if d2_upd <= d1 + timedelta(days=MAX_B_DAYS):
                d_out = d2_upd if d2_upd >= d1 else d1
            else:
                d_out = d1
            d2 = None
            withdraw_id = None
            withdraw_remark = ""
            account_id = ""
            device_sn = (r.device_sn or "").strip() or None

        # 时间筛选: **D4 实际出款时间** 落在筛选窗口 (不再是 D1 发起时间)
        if start and d_out < start:
            continue
        if end and d_out > end:
            continue

        # 三个时长(天)
        def _days(a, b):
            if not a or not b:
                return None
            return round((a - b).total_seconds() / 86400.0, 4)

        rows.append({
            "path": cat,
            "refund_no": r.refund_no,
            "platform_order_no": r.platform_order_no or "",
            "device_sn": device_sn,
            "account_id": account_id,
            "refund_method": m_raw,
            "refund_reason": (r.refund_reason or "").strip() or "未填写",
            "operator": (r.operator or "").strip() or "SYS/系统",
            "auditor": (r.auditor or "").strip() or "",
            "status": (r.status or "").strip() or "",
            "package_name": (r.package_name or "")[:40],
            "merchant_name": (r.merchant_name or "")[:40],
            "refund_amount": amt,
            "actual_amount": round(float(r.actual_amount or 0), 2),
            "d1_refund_created": d1.isoformat() if d1 else None,
            "d2_wallet_in": d2.isoformat() if d2 else None,
            "d4_actual_cashout": d_out.isoformat() if d_out else None,
            "days_1_4": _days(d_out, d1),
            "days_1_2": _days(d2, d1),
            "days_2_4": _days(d_out, d2),
            "withdraw_id": withdraw_id,
            "withdraw_remark": withdraw_remark,
        })
    return rows


def _pct(v: float, tot: float) -> float:
    return round(100.0 * v / tot, 2) if tot else 0.0


def refund_withdrawal_overview(db: Session, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """看板顶部 KPI 汇总卡片 + 路径对比 + 到账率分档 (窗口按实际出款D4统计)。"""
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = _build_chain_rows(db, s, e)

    a = [r for r in rows if r["path"] == "A"]
    b = [r for r in rows if r["path"] == "B"]
    ab = a + b

    def _arr(field: str, rs):
        return [r[field] for r in rs if r[field] is not None]

    def _quantile(arr, p):
        if not arr:
            return None
        a_s = sorted(arr)
        return round(a_s[min(len(a_s) - 1, int(len(a_s) * p // 100))], 3)

    def _metrics(rs):
        d = _arr("days_1_4", rs)
        cnt = len(d)
        if not cnt:
            return {}
        total_amt = sum(r["refund_amount"] for r in rs)
        return {
            "count": cnt,
            "amount": round(total_amt, 2),
            "avg_days": round(sum(d) / cnt, 3),
            "median_days": _quantile(d, 50),
            "p75_days": _quantile(d, 75),
            "p95_days": _quantile(d, 95),
            "max_days": _quantile(d, 100),
            "pct_within_1d": _pct(sum(1 for v in d if v <= 1), cnt),
            "pct_within_3d": _pct(sum(1 for v in d if v <= 3), cnt),
            "pct_within_7d": _pct(sum(1 for v in d if v <= 7), cnt),
            "pct_over_15d": _pct(sum(1 for v in d if v > 15), cnt),
        }

    # A路径未提现率 (全局口径, 不随D4窗口变化: 统计全量转入提现账户工单 vs 已匹配提现)
    rf_all = list(db.execute(select(Refund.refund_method, func.count(), func.sum(Refund.refund_amount))
                             .group_by(Refund.refund_method)).all())
    a_total_cnt = 0; a_total_amt = 0.0
    for m, c, am in rf_all:
        if m and "转入" in m:
            a_total_cnt += (c or 0); a_total_amt += float(am or 0)
    # a_matched_all = 全部历史已匹配提现的 A 路径 (不限筛选窗口)
    all_time_a = [r for r in _build_chain_rows(db, None, None) if r["path"] == "A"]
    a_matched_cnt = len(all_time_a)
    a_sleep_cnt = max(a_total_cnt - a_matched_cnt, 0)
    a_sleep_amt = max(a_total_amt - sum(r["refund_amount"] for r in all_time_a), 0)

    a_metrics = _metrics(a)
    b_metrics = _metrics(b)
    ab_metrics = _metrics(ab)
    # A 钱包路径两个子阶段耗时: D1→D2 = 工单到入钱包速度; D2→D4 = 钱躺在钱包里的时长
    a_d12 = [r["days_1_2"] for r in a if r["days_1_2"] is not None]
    a_d24 = [r["days_2_4"] for r in a if r["days_2_4"] is not None]
    if a_metrics:
        a_metrics["avg_days_1_2"] = round(sum(a_d12) / len(a_d12), 3) if a_d12 else 0.0
        a_metrics["median_days_1_2"] = round(sorted(a_d12)[len(a_d12) // 2], 3) if a_d12 else 0.0
        a_metrics["avg_days_2_4"] = round(sum(a_d24) / len(a_d24), 3) if a_d24 else 0.0
        a_metrics["median_days_2_4"] = round(sorted(a_d24)[len(a_d24) // 2], 3) if a_d24 else 0.0
    # 本窗口内实际出款 两条流结构汇总 (用中文命名, 直接供前端顶部醒目三张KPI卡展示)
    split = {
        "total_count": len(ab),
        "total_amount": round((ab_metrics or {}).get("amount", 0.0) or 0.0, 2),
        # B 流: 本窗口内直接线上退款(当天发起当天退, 或历史发起今天才线上完成)
        "direct_refund": {
            "count": (b_metrics or {}).get("count", 0) or 0,
            "amount": round((b_metrics or {}).get("amount", 0.0) or 0.0, 2),
            "count_ratio": _pct((b_metrics or {}).get("count", 0) or 0, len(ab)),
        },
        # A 流: 之前已经进了钱包, 本窗口内才被用户提现拿走 (相当于"历史沉淀今天才真正出款")
        "wallet_withdrawal": {
            "count": (a_metrics or {}).get("count", 0) or 0,
            "amount": round((a_metrics or {}).get("amount", 0.0) or 0.0, 2),
            "count_ratio": _pct((a_metrics or {}).get("count", 0) or 0, len(ab)),
            # A 流内部两个阶段, 用于解释这个"拖延效应"
            "avg_days_1_2": a_metrics.get("avg_days_1_2", 0.0),
            "avg_days_2_4": a_metrics.get("avg_days_2_4", 0.0),
        },
    }

    return {
        "scope": {"start": start, "end": end, "rows_total": len(rows), "filter_on": "D4_actual_cashout"},
        "cashout_split": split,  # 顶部三条流结构看板 (本窗口实际出款 = 直接线上退款 + 钱包内历史款今日提现)
        "paths": {
            "A_wallet": a_metrics,
            "B_direct": b_metrics,
            "AB_merged": ab_metrics,
        },
        "wallet_sleep": {  # 全局沉淀口径, 不随窗口变化 (总览指标)
            "a_total_refund_count": a_total_cnt,
            "a_matched_withdraw_count": a_matched_cnt,
            "still_in_wallet_count": a_sleep_cnt,
            "still_in_wallet_pct": _pct(a_sleep_cnt, a_total_cnt),
            "still_in_wallet_amount": round(a_sleep_amt, 2),
        },
    }


def refund_withdrawal_time_distribution(db: Session, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """两条路径各自的耗时分桶 (用于 ECharts 堆叠柱状图)。"""
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = _build_chain_rows(db, s, e)
    result: list[dict[str, Any]] = []
    for lbl, *_ in _TIME_BINS:
        if lbl == "<0异常":
            continue
        row: dict[str, Any] = {"bin": lbl}
        for path, pname in (("A", "wallet_count",), ("B", "direct_count",)):
            rs = [r for r in rows if r["path"] == path and r["days_1_4"] is not None]
            c = sum(1 for r in rs if _bin_label(r["days_1_4"]) == lbl)
            a = round(sum(r["refund_amount"] for r in rs if _bin_label(r["days_1_4"]) == lbl), 2)
            row[pname] = c
            row[pname.replace("count", "amount")] = a
            row[pname.replace("count", "ratio")] = _pct(c, len(rs))
        result.append(row)
    return result


def refund_withdrawal_dimensions(db: Session, start: str | None = None, end: str | None = None, topn: int = 20) -> dict[str, Any]:
    """聚合维度: 按处理人 / 退款原因 / 退款方式, 返回 Top N。"""
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = [r for r in _build_chain_rows(db, s, e) if r["path"] in ("A", "B")]

    def _group(key):
        g: dict[str, list] = {}
        for r in rows:
            k = str(r.get(key) or "未填写") or "未填写"
            if key == "operator" and ("SYS" in k or "系统" in k or "未填" in k):
                k = "SYS/系统自动"
            g.setdefault(k, []).append(r)
        out = []
        for k, rs in g.items():
            d = [r["days_1_4"] for r in rs if r["days_1_4"] is not None]
            if not d:
                continue
            n = len(d); tot = len(rows)
            out.append({
                "name": k, "count": n, "count_ratio": _pct(n, tot),
                "amount": round(sum(r["refund_amount"] for r in rs), 2),
                "avg_amount": round(sum(r["refund_amount"] for r in rs) / n, 2),
                "median_days": round(sorted(d)[n // 2], 3),
                "avg_days": round(sum(d) / n, 3),
                "p95_days": round(sorted(d)[min(n - 1, n * 95 // 100)], 3),
                "max_days": round(max(d), 3),
            })
        out.sort(key=lambda x: -x["count"])
        return out[:topn]

    return {
        "by_operator": _group("operator"),
        "by_reason": _group("refund_reason"),
        "by_method": _group("refund_method"),
    }


def refund_withdrawal_detail(db: Session, start: str | None = None, end: str | None = None,
                              sort: str = "days_desc", page: int = 1, page_size: int = 50,
                              slow_only: bool = False, path: str = "ALL") -> dict[str, Any]:
    """明细列表: 支持分页、排序、路径筛选、慢单筛选。"""
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = _build_chain_rows(db, s, e)
    if path in ("A", "B"):
        rows = [r for r in rows if r["path"] == path]
    elif path == "AB":
        rows = [r for r in rows if r["path"] in ("A", "B")]
    rows = [r for r in rows if r["days_1_4"] is not None]
    if slow_only:
        rows = [r for r in rows if (r["days_1_4"] or 0) > 3]
    # 排序
    sort = sort or "days_desc"
    key_fn_map = {
        "days_desc": lambda r: -(r["days_1_4"] or 0),
        "days_asc": lambda r: (r["days_1_4"] or 0),
        "amount_desc": lambda r: -(r["refund_amount"] or 0),
        "d1_desc": lambda r: (r["d1_refund_created"] or ""),
    }
    rows.sort(key=key_fn_map.get(sort, key_fn_map["days_desc"]), reverse=("d1" in sort))
    total = len(rows)
    page = max(1, page or 1); ps = max(1, min(500, page_size or 50))
    sl = slice((page - 1) * ps, page * ps)
    return {"total": total, "page": page, "page_size": ps, "list": rows[sl]}


def refund_withdrawal_daily_split(db: Session, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """按**实际出款日 D4** 逐天拆分两条流: 直接线上退款出款 vs 钱包沉淀后提现出款.

    用于 ECharts 按日堆叠柱状图: 让用户一眼看到"每天这 N 笔实际退款里, 多少是今天直退的, 多少是之前进了钱包今天才被提现的".
    返回数组按 date 升序, 每天一行:
      date, direct_count/amount (B直退), wallet_count/amount (A钱包提现), total_count/amount
      同时 A 流附带 avg_days_1_2 (工单→入钱包) / avg_days_2_4 (入钱包→用户提现拖延) 两个子阶段日均值.
    """
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = _build_chain_rows(db, s, e)
    # 按 D4 出款日 (YYYY-MM-DD) 分组
    from collections import defaultdict as _dd
    g: dict[str, dict[str, Any]] = _dd(dict)
    for r in rows:
        d4 = r["d4_actual_cashout"]
        if not d4:
            continue
        day = d4[:10]
        if day not in g:
            g[day] = {
                "date": day,
                "direct_count": 0, "direct_amount": 0.0,
                "wallet_count": 0, "wallet_amount": 0.0,
                "a_d12_sum": 0.0, "a_d12_n": 0,
                "a_d24_sum": 0.0, "a_d24_n": 0,
            }
        bucket = g[day]
        amt = r["refund_amount"] or 0.0
        if r["path"] == "B":
            bucket["direct_count"] += 1
            bucket["direct_amount"] += amt
        elif r["path"] == "A":
            bucket["wallet_count"] += 1
            bucket["wallet_amount"] += amt
            if r.get("days_1_2") is not None:
                bucket["a_d12_sum"] += r["days_1_2"]
                bucket["a_d12_n"] += 1
            if r.get("days_2_4") is not None:
                bucket["a_d24_sum"] += r["days_2_4"]
                bucket["a_d24_n"] += 1
    out = []
    for day in sorted(g.keys()):
        b = g[day]
        tc = b["direct_count"] + b["wallet_count"]
        ta = round(b["direct_amount"] + b["wallet_amount"], 2)
        out.append({
            "date": day,
            "direct_count": b["direct_count"],
            "direct_amount": round(b["direct_amount"], 2),
            "wallet_count": b["wallet_count"],
            "wallet_amount": round(b["wallet_amount"], 2),
            "total_count": tc,
            "total_amount": ta,
            # A 流两个子阶段日均值 (帮助定位到底是入钱包慢还是用户提现慢)
            "wallet_avg_days_1_2": round(b["a_d12_sum"] / b["a_d12_n"], 3) if b["a_d12_n"] else None,
            "wallet_avg_days_2_4": round(b["a_d24_sum"] / b["a_d24_n"], 3) if b["a_d24_n"] else None,
        })
    return out


# ---------- 看板7: 今日看板 (单日切面 + 实时节奏) ----------

import datetime as _dt

# 用于"昨日同时段"对比: 比较 00:00 ~ 当前时间(而不是整天), 避免白天看跌90%的误导.
# 用法: _today_window(None) -> (今天00:00, 现在), _yesterday_same_window(None) -> (昨天00:00, 昨天此时)
# 当 date 显式传入(如选了昨天或某历史日期), 窗口就是整天 00:00 ~ 23:59:59.

TZ = __import__("zoneinfo").ZoneInfo("Asia/Shanghai")


def _today_windows(date: _dt.date | None) -> tuple[_dt.datetime, _dt.datetime, _dt.datetime, _dt.datetime]:
    """返回 (today_start, today_end, yoy_start, yoy_end).
    date=None → 实时窗口 (上海时区今天 00:00 ~ now, 昨天 00:00 ~ 昨天此时)
    date=具体日期 → 整天窗口 (上海时区 00:00 ~ 23:59:59.999999, 同比前一天)
    说明:DB 里 TIMESTAMPTZ 存 UTC,PostgreSQL session TIMEZONE=Etc/UTC;
         因此需把 Asia/Shanghai 窗口先 .astimezone(UTC) 再 replace(tzinfo=None) 传给 DB。
    """
    now = _dt.datetime.now(TZ)
    is_today = date is None or date == now.date()
    if is_today:
        t_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        t_end = now
        y_start = t_start - timedelta(days=1)
        y_end = t_end - timedelta(days=1)
    else:
        t_start = _dt.datetime.combine(date, _dt.time(0, 0, 0), tzinfo=TZ)
        t_end = t_start + timedelta(days=1) - timedelta(microseconds=1)
        y_start = t_start - timedelta(days=1)
        y_end = t_end - timedelta(days=1)
    # 转 UTC 后再去 tzinfo, 匹配 DB session(UTC) 对 naive datetime 的解释
    _utc = _dt.timezone.utc
    return (t_start.astimezone(_utc).replace(tzinfo=None),
            t_end.astimezone(_utc).replace(tzinfo=None),
            y_start.astimezone(_utc).replace(tzinfo=None),
            y_end.astimezone(_utc).replace(tzinfo=None))


def _parse_date(s: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(s) if s else None


def _pct_delta(cur, prev) -> float | None:
    """环比百分比: (cur-prev)/prev*100, prev为0返回None."""
    if prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100.0, 1)


def today_kpi(db: Session, date: str | None = None) -> dict[str, Any]:
    """模块1: 今日6张KPI卡 + 昨日同时段环比.

    date=None 表示今天(实时到当前小时); date='2026-07-31' 表示整天.
    每张卡 {value, yoy_value, yoy_pct, note?}
    """
    d = _parse_date(date)
    ts, te, ys, ye = _today_windows(d)

    def _between(col, s, e):
        return and_(col.isnot(None), col >= s, col <= e)

    # 1. 今日接待量 (cs_records.operation_time)
    cs_today = db.execute(select(func.count()).where(_between(CsRecord.operation_time, ts, te))).scalar() or 0
    cs_yoy = db.execute(select(func.count()).where(_between(CsRecord.operation_time, ys, ye))).scalar() or 0

    # 2/3. 退款笔数+金额 (refunds.created_at_raw)
    rf_row = db.execute(select(func.count(), func.coalesce(func.sum(Refund.refund_amount), 0))
                        .where(_between(Refund.created_at_raw, ts, te))).first()
    rf_cnt_t, rf_amt_t = int(rf_row[0] or 0), float(rf_row[1] or 0)
    rf_row_y = db.execute(select(func.count(), func.coalesce(func.sum(Refund.refund_amount), 0))
                          .where(_between(Refund.created_at_raw, ys, ye))).first()
    rf_cnt_y, rf_amt_y = int(rf_row_y[0] or 0), float(rf_row_y[1] or 0)

    # 4. 真实现金流 = 直接退款(线上+人工) + 用户提现
    direct_t = db.scalar(select(func.coalesce(func.sum(Refund.refund_amount), 0))
                         .where(*_direct_refund_filter(), _between(Refund.created_at_raw, ts, te))) or 0
    withdraw_t = db.scalar(select(func.coalesce(func.sum(WalletWithdrawal.op_amount), 0))
                           .where(WalletWithdrawal.op_type == "用户提现",
                                  _between(WalletWithdrawal.op_time, ts, te))) or 0
    cash_t = float(direct_t or 0) + float(withdraw_t or 0)
    direct_y = db.scalar(select(func.coalesce(func.sum(Refund.refund_amount), 0))
                         .where(*_direct_refund_filter(), _between(Refund.created_at_raw, ys, ye))) or 0
    withdraw_y = db.scalar(select(func.coalesce(func.sum(WalletWithdrawal.op_amount), 0))
                           .where(WalletWithdrawal.op_type == "用户提现",
                                  _between(WalletWithdrawal.op_time, ys, ye))) or 0
    cash_y = float(direct_y or 0) + float(withdraw_y or 0)
    # 商户号现金流 T+1 提示: 如果是今天且商户号数据为空,note提示
    mr_cnt_today = db.scalar(select(func.count()).where(_between(MerchantRefund.trade_time, ts, te),
                                                         MerchantRefund.trade_status == "REFUND")) or 0
    merchant_tip = None
    if (d is None or d == _dt.datetime.now(TZ).date()) and mr_cnt_today == 0:
        merchant_tip = "商户号流水通常T+1导入，今日数据可能为空"

    # 5. 今日已完工单 (service_tickets.finish_time 且 status='已完成')
    done_t = db.execute(select(func.count())
                        .where(_between(ServiceTicket.finish_time, ts, te),
                               ServiceTicket.status == "已完成")).scalar() or 0
    done_y = db.execute(select(func.count())
                        .where(_between(ServiceTicket.finish_time, ys, ye),
                               ServiceTicket.status == "已完成")).scalar() or 0

    # 6. 今日在岗人数 (cs_records distinct operator)
    staff_t = db.execute(select(func.count(func.distinct(CsRecord.operator)))
                         .where(_between(CsRecord.operation_time, ts, te),
                                CsRecord.operator.isnot(None))).scalar() or 0
    staff_y = db.execute(select(func.count(func.distinct(CsRecord.operator)))
                         .where(_between(CsRecord.operation_time, ys, ye),
                                CsRecord.operator.isnot(None))).scalar() or 0

    def _k(v, yv):
        return {"value": v, "yoy_value": yv, "yoy_pct": _pct_delta(v, yv)}

    # 返回的window字段用上海时区展示(前端显示友好); ts/te仍用于DB比较
    _utc = _dt.timezone.utc
    def _sh_iso(naive_utc: _dt.datetime) -> str:
        return naive_utc.replace(tzinfo=_utc).astimezone(TZ).isoformat()

    return {
        "date": (d or _dt.datetime.now(TZ).date()).isoformat(),
        "is_realtime": d is None,
        "window": {
            "today_start": _sh_iso(ts), "today_end": _sh_iso(te),
            "yoy_start": _sh_iso(ys), "yoy_end": _sh_iso(ye),
        },
        "reception":      _k(int(cs_today), int(cs_yoy)),
        "refund_count":   _k(rf_cnt_t, rf_cnt_y),
        "refund_amount":  _k(round(rf_amt_t, 2), round(rf_amt_y, 2)),
        "cash_out":       _k(round(cash_t, 2), round(cash_y, 2)),
        "ticket_done":    _k(int(done_t), int(done_y)),
        "staff_on_duty":  _k(int(staff_t), int(staff_y)),
        "merchant_tip":   merchant_tip,
    }


def today_hourly_trend(db: Session, date: str | None = None) -> dict[str, Any]:
    """模块2: 今日小时节奏 (上海时区 00:00~23:00, 每小时一行).
    双Y轴: reception_count 柱左, refund_amount 折线右. 若 date=今天, 当前小时之后的行缺省为0不填充.
    """
    d = _parse_date(date)
    ts, te, _, _ = _today_windows(d)
    # 小时截断: 先 operation_time AT TIME ZONE 'Asia/Shanghai' → 再 date_trunc('hour')
    # 保证 h08 对应真实的北京时间 08:00~08:59
    def _h(col):
        return func.date_trunc("hour", func.timezone("Asia/Shanghai", col)).label("h")
    cs_rows = db.execute(
        select(_h(CsRecord.operation_time), func.count().label("cnt"))
        .where(CsRecord.operation_time.isnot(None),
               CsRecord.operation_time >= ts, CsRecord.operation_time <= te)
        .group_by("h")
    ).all()
    cs_map = {r.h.hour: int(r.cnt or 0) for r in cs_rows if r.h}

    rf_rows = db.execute(
        select(_h(Refund.created_at_raw),
               func.coalesce(func.sum(Refund.refund_amount), 0).label("amt"))
        .where(Refund.created_at_raw.isnot(None),
               Refund.created_at_raw >= ts, Refund.created_at_raw <= te)
        .group_by("h")
    ).all()
    rf_map = {r.h.hour: float(r.amt or 0) for r in rf_rows if r.h}

    # 填充到当前小时(今天,上海时区)或整天24小时(历史日期)
    now_sh = _dt.datetime.now(TZ)
    is_today = d is None or d == now_sh.date()
    max_h = now_sh.hour if is_today else 23
    hours = list(range(24))
    return {
        "date": (d or now_sh.date()).isoformat(),
        "hours": hours,
        "hour_labels": [f"{h:02d}:00" for h in hours],
        "reception": [cs_map.get(h, 0) if h <= max_h else None for h in hours],
        "refund_amount": [round(rf_map.get(h, 0.0), 2) if h <= max_h else None for h in hours],
    }


def today_reception_breakdown(db: Session, date: str | None = None) -> dict[str, Any]:
    """模块3: 今日接待分项 = 分类饼图 + Top10客服柱图.
    分类: 6 类 (投诉/退款/套餐问题/咨询/综合问题/其他问题)
    """
    d = _parse_date(date)
    ts, te, _, _ = _today_windows(d)
    between = and_(CsRecord.operation_time.isnot(None),
                   CsRecord.operation_time >= ts, CsRecord.operation_time <= te)

    # 左: 分类饼 (复用 _category_case)
    bucket = _category_case()
    cat_rows = db.execute(
        select(bucket, func.count().label("cnt"))
        .where(between)
        .group_by("category_bucket")
    ).all()
    categories = []
    order = ["投诉", "退款", "套餐问题", "咨询", "综合问题", "其他问题"]
    for r in cat_rows:
        cat = r.category_bucket if r.category_bucket in order else "其他问题"
        categories.append({"name": cat, "value": int(r.cnt or 0)})
    # 补齐0分类
    present = {c["name"] for c in categories}
    for nm in order:
        if nm not in present:
            categories.append({"name": nm, "value": 0})

    # 右: Top10客服 (按operator分组count, 关联PersonAlias→Person拿group_name/display_name)
    op_person = _operator_to_person_cte()
    top_rows = db.execute(
        select(
            CsRecord.operator.label("raw_op"),
            op_person.c.display_name.label("display_name"),
            op_person.c.department.label("department"),
            op_person.c.group_name.label("group_name"),
            func.count().label("cnt"),
        )
        .outerjoin(op_person, CsRecord.operator == op_person.c.operator)
        .where(between, CsRecord.operator.isnot(None))
        .group_by("raw_op", op_person.c.display_name, op_person.c.department, op_person.c.group_name)
        .order_by(func.count().desc())
        .limit(10)
    ).all()
    top_staff = [
        {"name": r.display_name or r.raw_op or "未知",
         "department": r.department or "未设置",
         "group": r.group_name or "未分组",
         "count": int(r.cnt or 0)}
        for r in top_rows
    ]

    return {
        "date": (d or _dt.datetime.now(TZ).date()).isoformat(),
        "categories": sorted(categories, key=lambda x: -x["value"]),
        "top_staff": top_staff,
    }


def today_refund_breakdown(db: Session, date: str | None = None) -> dict[str, Any]:
    """模块4: 今日退款分项 = 退款原因Top10 + 退款方式分布."""
    d = _parse_date(date)
    ts, te, _, _ = _today_windows(d)
    between = and_(Refund.created_at_raw.isnot(None),
                   Refund.created_at_raw >= ts, Refund.created_at_raw <= te)

    # 左: 原因 Top10 (按金额倒序)
    reason_rows = db.execute(
        select(Refund.refund_reason,
               func.count().label("cnt"),
               func.coalesce(func.sum(Refund.refund_amount), 0).label("amt"))
        .where(between, Refund.refund_reason.isnot(None))
        .group_by(Refund.refund_reason)
        .order_by(func.sum(Refund.refund_amount).desc())
        .limit(10)
    ).all()
    reasons = [{"name": r[0] or "未填写", "count": int(r.cnt or 0), "amount": round(float(r.amt or 0), 2)}
               for r in reason_rows]

    # 右: 退款方式饼图
    def _bucket_method(m: str | None) -> str:
        if not m:
            return "未填写"
        if "转入" in m:
            return "退回钱包"
        for kw in ("线上", "微信", "支付宝", "银联", "银行卡", "直退"):
            if kw in m:
                return "线上直退"
        if "线下" in m:
            return "线下退款"
        return "其他"

    method_rows = db.execute(
        select(Refund.refund_method, func.count().label("cnt"))
        .where(between)
        .group_by(Refund.refund_method)
    ).all()
    method_agg: dict[str, int] = {}
    for r in method_rows:
        b = _bucket_method(r[0])
        method_agg[b] = method_agg.get(b, 0) + int(r.cnt or 0)
    methods = [{"name": k, "value": v} for k, v in method_agg.items()]

    return {
        "date": (d or _dt.datetime.now(TZ).date()).isoformat(),
        "reasons": reasons,
        "methods": methods,
    }


def today_queue(db: Session, date: str | None = None) -> dict[str, Any]:
    """模块5: 今日处理队列 = 今日截止待办 + 进行中专项 + 今日异常事件.
    每项最多10条, 点击可跳转详情页.
    """
    d = _parse_date(date)
    # today_window = 上海时区该日期的整天窗口 (00:00 ~ 23:59:59.999999 → UTC naive)
    _utc = _dt.timezone.utc
    target_date = d or _dt.datetime.now(TZ).date()
    sh_start = _dt.datetime.combine(target_date, _dt.time(0, 0, 0), tzinfo=TZ)
    sh_end = sh_start + timedelta(days=1) - timedelta(microseconds=1)
    today_start = sh_start.astimezone(_utc).replace(tzinfo=None)
    today_end = sh_end.astimezone(_utc).replace(tzinfo=None)
    # 待办 status: pending/processing/confirming 是未完成, done 已完成
    not_done = TodoItem.status.notin_(["done"])
    todo_rows = db.execute(
        select(TodoItem.id, TodoItem.title, TodoItem.priority, TodoItem.status,
               TodoItem.deadline, TodoItem.related_refund_no)
        .where(TodoItem.deadline.isnot(None),
               TodoItem.deadline >= today_start, TodoItem.deadline <= today_end,
               not_done)
        .order_by(case(
            (TodoItem.priority == "urgent", 0),
            (TodoItem.priority == "high", 1),
            (TodoItem.priority == "medium", 2),
            (TodoItem.priority == "low", 3),
            else_=4
        ), TodoItem.deadline.asc())
        .limit(10)
    ).all()
    todos = [{
        "id": r.id, "title": r.title, "priority": r.priority, "status": r.status,
        "deadline": r.deadline.isoformat() if r.deadline else None,
        "related_refund_no": r.related_refund_no,
    } for r in todo_rows]

    # 2. 进行中专项: status in (pending_start/executing/following) + deadline在7天内或无deadline
    active_status = ["pending_start", "executing", "following"]
    week_later = today_end + timedelta(days=7)
    task_rows = db.execute(
        select(SpecialTask.id, SpecialTask.title, SpecialTask.status,
               SpecialTask.task_type, SpecialTask.deadline)
        .where(SpecialTask.status.in_(active_status))
        .order_by(case(
            (SpecialTask.deadline.is_(None), 1),
            (SpecialTask.deadline <= week_later, 0),
            else_=2
        ), SpecialTask.deadline.asc().nullslast())
        .limit(10)
    ).all()
    tasks = [{
        "id": r.id, "title": r.title, "status": r.status, "type": r.task_type,
        "deadline": r.deadline.isoformat() if r.deadline else None,
    } for r in task_rows]

    # 3. 今日异常事件: occurred_at或created_at 落在今天
    inc_between = or_(
        and_(Incident.occurred_at.isnot(None), Incident.occurred_at >= today_start, Incident.occurred_at <= today_end),
        and_(Incident.created_at.isnot(None), Incident.created_at >= today_start, Incident.created_at <= today_end),
    )
    inc_rows = db.execute(
        select(Incident.id, Incident.title, Incident.status, Incident.subject,
               Incident.occurred_at, Incident.duration_minutes)
        .where(inc_between)
        .order_by(Incident.occurred_at.desc().nullslast(), Incident.created_at.desc())
        .limit(10)
    ).all()
    incidents = [{
        "id": r.id, "title": r.title, "status": r.status, "subject": r.subject,
        "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
        "duration_minutes": r.duration_minutes,
    } for r in inc_rows]

    return {
        "date": (d or _dt.datetime.now(TZ).date()).isoformat(),
        "todos": todos,
        "tasks": tasks,
        "incidents": incidents,
    }


def today_staff_efficiency(db: Session, date: str | None = None, topn: int = 30) -> list[dict[str, Any]]:
    """模块6: 今日人均效率表 (按operator聚合, 关联Person拿display_name/group_name).
    列: name, group, reception_count, refund_op_count, refund_op_amount, ticket_done_count, work_score.
    按 work_score(接待量+退款笔数*2+完工单*3) 倒序, 取TopN.
    """
    d = _parse_date(date)
    ts, te, _, _ = _today_windows(d)
    op_person = _operator_to_person_cte()

    # 1. 接待量 per operator
    cs_q = (select(CsRecord.operator.label("op"), func.count().label("c"))
            .where(CsRecord.operation_time.isnot(None),
                   CsRecord.operation_time >= ts, CsRecord.operation_time <= te,
                   CsRecord.operator.isnot(None))
            .group_by(CsRecord.operator)).cte("cs_q")
    # 2. 退款操作 per operator (排除 sys/system)
    rf_q = (select(Refund.operator.label("op"),
                   func.count().label("c"),
                   func.coalesce(func.sum(Refund.refund_amount), 0).label("a"))
            .where(Refund.created_at_raw.isnot(None),
                   Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
                   Refund.operator.isnot(None),
                   Refund.operator.notin_(["sys", "system", "SYS", "System"]))
            .group_by(Refund.operator)).cte("rf_q")
    # 3. 已完工单 per handler
    tk_q = (select(ServiceTicket.handler.label("op"), func.count().label("c"))
            .where(ServiceTicket.finish_time.isnot(None),
                   ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te,
                   ServiceTicket.handler.isnot(None),
                   ServiceTicket.status == "已完成")
            .group_by(ServiceTicket.handler)).cte("tk_q")
    # 合并三个cte的op并集, 左关联各自的统计值和人员信息
    # union(UNION DISTINCT) 自动去重, 无需 .distinct()
    ops_union = union(
        select(cs_q.c.op), select(rf_q.c.op), select(tk_q.c.op)
    ).cte("ops_u")
    rows = db.execute(
        select(
            ops_union.c.op,
            func.coalesce(op_person.c.display_name, ops_union.c.op).label("name"),
            op_person.c.department.label("dept"),
            op_person.c.group_name.label("grp"),
            func.coalesce(cs_q.c.c, 0).label("rc"),
            func.coalesce(rf_q.c.c, 0).label("fc"),
            func.coalesce(rf_q.c.a, 0).label("fa"),
            func.coalesce(tk_q.c.c, 0).label("dc"),
        )
        .select_from(ops_union)
        .outerjoin(cs_q, ops_union.c.op == cs_q.c.op)
        .outerjoin(rf_q, ops_union.c.op == rf_q.c.op)
        .outerjoin(tk_q, ops_union.c.op == tk_q.c.op)
        .outerjoin(op_person, ops_union.c.op == op_person.c.operator)
    ).all()
    out = []
    for r in rows:
        rc = int(r.rc or 0)
        fc = int(r.fc or 0)
        dc = int(r.dc or 0)
        score = rc + fc * 2 + dc * 3  # 工作量合成分 (可后续调整权重)
        out.append({
            "operator": r.op,
            "name": r.name or r.op or "未知",
            "department": r.dept or "未设置",
            "group": r.grp or "未分组",
            "reception_count": rc,
            "refund_op_count": fc,
            "refund_op_amount": round(float(r.fa or 0), 2),
            "ticket_done_count": dc,
            "work_score": score,
        })
    out.sort(key=lambda x: -x["work_score"])
    return out[: max(1, min(200, topn or 30))]


