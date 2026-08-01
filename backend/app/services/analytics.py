"""分析查询服务 - 4 大看板的数据查询逻辑"""

import datetime as _dt
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select, text, union
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
    """退款趋势(按日):退款金额、笔数 【统一口径: 路线A 退费单-线上直退 refund_method='线上'】"""
    start, end = _date_range(start, end)
    rows = db.execute(
        select(
            func.to_char(Refund.created_at_raw, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(Refund.created_at_raw.between(start, end), *_route_a_online_refund_filter())
        .group_by("date")
        .order_by("date")
    ).all()
    return [{"date": r.date, "value": float(r.amount or 0), "count": r.count} for r in rows]


def operation_status_dist(db: Session) -> list[dict]:
    """退费状态分布 【统一口径: 路线A 退费单-线上直退】"""
    rows = db.execute(
        select(Refund.status, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .where(*_route_a_online_refund_filter())
        .group_by(Refund.status)
    ).all()
    total = sum(r[2] for r in rows) or 1
    return [
        {"name": r[0] or "(空)", "value": float(r[1] or 0), "count": r[2], "ratio": round(r[2] / total, 4)}
        for r in rows
    ]


def operation_reason_top(db: Session, limit: int = 10) -> list[dict]:
    """退费原因 TOP 【统一口径: 路线A 退费单-线上直退】"""
    rows = db.execute(
        select(Refund.refund_reason, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .where(Refund.refund_reason.isnot(None), *_route_a_online_refund_filter())
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


# ---------- 看板2:现金流分析 [全域统一退款核算规范 v3] ----------
#
# 本文件所有看板/接口/指标(退款笔数/金额/真实现金流)必须严格遵守
# 以下"四路经营出款 + 一路实际现金流 + 对账偏差"的统一算法,禁止局部私自改写:
#
# ┌─────────────────── 四路经营侧出款 (经营报表导出,data_source business类型) ────┐
# │ A. 退费单-线上直退  refunds 表 WHERE refund_method = '线上'                    │
# │   (严格按用户要求: 退费单管理里只有这条算退款路线)                              │
# │   sys/system 操作(钱包内部转)仍按 refund_method 字段保留,不做人工剔除           │
# │ B. 钱包-用户取现    wallet_withdrawals 表 WHERE op_type = '用户提现'           │
# │   (钱从钱包真实提出到用户银行卡,构成现金流)                                    │
# │ C. 闪猫退款         shenmao_refund 独立业务表 (data_sources.code=shenmao_refund)│
# │   物理表占位名 _placeholder_shenmao_refund(建表后代码零改动, 自动读新表)       │
# │   当前表未建 → 读 0; 建表成功后按约定状态(如 refund_status='SUCCESS')聚合      │
# │ D. 其他退款         other_refund 独立业务表 (data_sources.code=other_refund)   │
# │   物理表占位名 _placeholder_other_refund (同C,建表后自动生效)                  │
# │   当前表未建 → 读 0                                                           │
# ├─────────────────── 实际现金流 (财务侧,微信商户号真实扣款,独立第五张表) ──────────┤
# │ E. 商户号现金流     merchant_refunds 表 WHERE refund_status = 'SUCCESS'       │
# │   不区分 refund_type 字段 (ORIGINAL+BALANCE 全部合计,不分桶C/D)               │
# │   (data_sources.code=merchant_refund, name_cn="商户号现金流" = 对账基准)      │
# ├─────────────────── 合计口径 ───────────────────────────────────────────────────┤
# │ 经营估算 (经营侧四路总登记出款) = A + B + C + D  (C/D建表后自动并入)           │
# │ 实际现金流 (财务侧商户号真扣) = E  (merchant_refunds SUCCESS 合计)            │
# │ 对账偏差 = 经营估算 − 实际现金流 = (A+B+C+D) − E                               │
# │                                                                                │
# │ 术语对齐:                                                                      │
# │   "今日退款笔数/金额" = 路线 A  (退费单线上直退, 业务退款口径)                 │
# │   "真实现金流出 / 现金流出" = A+B+C+D (经营侧四路出款估算)                     │
# │   "商户号实际现金流" = E (merchant_refunds SUCCESS,对账基准)                  │
# │   "对账偏差" = 经营估算 − 实际现金流 (C/D未建表时临时 = A+B − E)              │
# └────────────────────────────────────────────────────────────────────────────────┘
#
# 【v2 → v3 改动】(2026-08-02,用户明确纠正):
#   v2错误: 把 C/D 当成 merchant_refunds.refund_type 分桶(ORIGINAL/BALANCE),
#           把 C+D 当成"财务实际"与 A+B 当成"经营估算",导致商户号流水被重复使用
#   v3纠正: C/D 是独立的两张业务表 (闪猫退款 / 其他退款, data_source已注册占位),
#           merchant_refunds 只对应 E = 实际现金流 (独立第五张表,对账基准),
#           经营估算 = A+B+C+D, 实际 = E, 对账偏差 = 经营 − 实际

# ---------- 路线A 退费单线上直退 ----------
def _route_a_online_refund_filter():
    """【路线A】退费单线上直退: refund_method='线上' (严格按用户要求的唯一筛选条件)"""
    return (Refund.refund_method == "线上",)

# ---------- 路线B 钱包用户取现 ----------
def _route_b_withdraw_filter():
    """【路线B】钱包-用户取现: op_type='用户提现'"""
    return (WalletWithdrawal.op_type == "用户提现",)

# ---------- 路线C 闪猫退款 (独立业务表) ----------
_C_SHANMAO_TABLE = "_placeholder_shenmao_refund"   # data_sources.code=shenmao_refund 的物理表占位名(未来建表后改真实表名即可)
# 建表后约定的列名 (若真实列名不同,只需改这里两个常量 无需改聚合逻辑):
_C_SHANMAO_AMOUNT_COL = "refund_amount"
_C_SHANMAO_TIME_COL = "refund_time"
_C_SHANMAO_STATUS_COL = "refund_status"    # None = 不按状态过滤(全表成功)
_C_SHANMAO_STATUS_OK = "SUCCESS"

def _route_c_shanmao_agg(db: Session, start, end) -> tuple[int, float]:
    """【路线C】闪猫退款独立表聚合 (表不存在则返回 0,0,不报错).

    PostgreSQL 中 SQL 失败会使整个事务处于 InFailedSqlTransaction 状态，
    即使 Python 层 except 吞异常，后续 SQL 也会被 PG 拒绝。
    必须用 savepoint (begin_nested) 隔离，失败只回滚子事务。
    """
    sql_tpl = f"""
    SELECT COUNT(*) AS cnt,
           COALESCE(SUM({_C_SHANMAO_AMOUNT_COL})::numeric,0) AS amt
    FROM {_C_SHANMAO_TABLE}
    WHERE {_C_SHANMAO_TIME_COL} IS NOT NULL
      {"AND " + _C_SHANMAO_STATUS_COL + " = :st" if _C_SHANMAO_STATUS_COL else ""}
      {"AND " + _C_SHANMAO_TIME_COL + " >= :s" if start else ""}
      {"AND " + _C_SHANMAO_TIME_COL + " <= :e" if end else ""}
    """
    params: dict[str, Any] = {}
    if _C_SHANMAO_STATUS_COL:
        params["st"] = _C_SHANMAO_STATUS_OK
    if start is not None:
        params["s"] = start
    if end is not None:
        params["e"] = end
    try:
        with db.begin_nested():
            r = db.execute(text(sql_tpl), params).first() or (0, 0)
        return int(r[0] or 0), float(r[1] or 0)
    except Exception:
        # 表未建或列未匹配: 静默返回 0, 等建表/改常量后自动生效
        return 0, 0.0

# ---------- 路线D 其他退款 (独立业务表) ----------
_D_OTHER_TABLE = "_placeholder_other_refund"   # data_sources.code=other_refund 的物理表占位名
_D_OTHER_AMOUNT_COL = "refund_amount"
_D_OTHER_TIME_COL = "refund_time"
_D_OTHER_STATUS_COL = "refund_status"
_D_OTHER_STATUS_OK = "SUCCESS"

def _route_d_other_agg(db: Session, start, end) -> tuple[int, float]:
    """【路线D】其他退款独立表聚合 (表不存在则返回 0,0,不报错).

    同 _route_c_shanmao_agg: 必须用 savepoint 隔离 PG 事务失败状态。
    """
    sql_tpl = f"""
    SELECT COUNT(*) AS cnt,
           COALESCE(SUM({_D_OTHER_AMOUNT_COL})::numeric,0) AS amt
    FROM {_D_OTHER_TABLE}
    WHERE {_D_OTHER_TIME_COL} IS NOT NULL
      {"AND " + _D_OTHER_STATUS_COL + " = :st" if _D_OTHER_STATUS_COL else ""}
      {"AND " + _D_OTHER_TIME_COL + " >= :s" if start else ""}
      {"AND " + _D_OTHER_TIME_COL + " <= :e" if end else ""}
    """
    params: dict[str, Any] = {}
    if _D_OTHER_STATUS_COL:
        params["st"] = _D_OTHER_STATUS_OK
    if start is not None:
        params["s"] = start
    if end is not None:
        params["e"] = end
    try:
        with db.begin_nested():
            r = db.execute(text(sql_tpl), params).first() or (0, 0)
        return int(r[0] or 0), float(r[1] or 0)
    except Exception:
        return 0, 0.0

# ---------- 实际现金流 E: 商户号现金流表 (独立对账基准) ----------
def _route_e_merchant_cashflow_filter():
    """【实际现金流 E】merchant_refunds: refund_status='SUCCESS' 全部 (不区分 refund_type 不分C/D桶)"""
    return (MerchantRefund.refund_status == "SUCCESS",)


def _cash_routes_total(db: Session,
                       col_a: Any = Refund.created_at_raw,
                       col_b: Any = WalletWithdrawal.op_time,
                       col_e: Any = MerchantRefund.trade_time,
                       start: _dt.datetime | None = None,
                       end: _dt.datetime | None = None) -> dict[str, Any]:
    """【统一核算聚合器 v3】四路经营A/B/C/D + 实际现金流E + 对账偏差.

    Args:
        col_a: 路线A 时间列 (默认 refunds.created_at_raw)
        col_b: 路线B 时间列 (默认 wallet_withdrawals.op_time)
        col_e: 实际现金流 E 时间列 (默认 merchant_refunds.trade_time)
        start/end: 时间窗边界 (None = 全表)
    Returns:
        {route_a/b/c/d: {count, amount},
         operational: {count, amount}         经营估算 = A+B+C+D,
         actual_cashflow: {count, amount}     实际现金流 = E (对账基准),
         financial: = actual_cashflow          向后兼容保留键(语义=财务实际),
         diff: {count, amount}                对账偏差 = 经营 − 实际
        }
    """
    def _between(col, s, e):
        if s is None and e is None:
            return col.isnot(None)
        conds = [col.isnot(None)]
        if s is not None:
            conds.append(col >= s)
        if e is not None:
            conds.append(col <= e)
        return and_(*conds)

    # ── A 退费单线上直退 ──
    a_row = db.execute(select(
        func.count(),
        func.coalesce(func.sum(Refund.refund_amount), 0),
    ).where(
        *_route_a_online_refund_filter(),
        _between(col_a, start, end),
    )).first() or (0, 0)
    a_cnt, a_amt = int(a_row[0] or 0), float(a_row[1] or 0)

    # ── B 钱包用户取现 ──
    b_row = db.execute(select(
        func.count(),
        func.coalesce(func.sum(WalletWithdrawal.op_amount), 0),
    ).where(
        *_route_b_withdraw_filter(),
        _between(col_b, start, end),
    )).first() or (0, 0)
    b_cnt, b_amt = int(b_row[0] or 0), float(b_row[1] or 0)

    # ── C 闪猫退款独立表 (占位,表未建→0) ──
    c_cnt, c_amt = _route_c_shanmao_agg(db, start, end)
    # ── D 其他退款独立表 (占位,表未建→0) ──
    d_cnt, d_amt = _route_d_other_agg(db, start, end)

    # ── E 实际现金流: merchant_refunds SUCCESS 合计 (不分 refund_type 桶) ──
    e_row = db.execute(select(
        func.count(),
        func.coalesce(func.sum(MerchantRefund.refund_amount), 0),
    ).where(
        *_route_e_merchant_cashflow_filter(),
        _between(col_e, start, end),
    )).first() or (0, 0)
    e_cnt, e_amt = int(e_row[0] or 0), float(e_row[1] or 0)

    # ── 合计: 经营估算 A+B+C+D / 实际 E / 对账偏差 经营−实际 ──
    op_cnt = a_cnt + b_cnt + c_cnt + d_cnt
    op_amt = a_amt + b_amt + c_amt + d_amt
    fc_cnt = e_cnt
    fc_amt = e_amt

    return {
        "route_a": {"count": a_cnt, "amount": round(a_amt, 2)},   # A.退费单线上直退
        "route_b": {"count": b_cnt, "amount": round(b_amt, 2)},   # B.钱包-用户取现
        "route_c": {"count": c_cnt, "amount": round(c_amt, 2)},   # C.闪猫退款(独立表,暂0)
        "route_d": {"count": d_cnt, "amount": round(d_amt, 2)},   # D.其他退款(独立表,暂0)
        "actual_cashflow":  # E.商户号实际现金流 SUCCESS合计 (对账基准,不分C/D桶)
            {"count": e_cnt, "amount": round(e_amt, 2)},
        "operational":     # 经营侧四路总登记出款估算 = A+B+C+D
            {"count": op_cnt, "amount": round(op_amt, 2)},
        "financial":       # 向后兼容键: 财务实际 = actual_cashflow = E
            {"count": fc_cnt, "amount": round(fc_amt, 2)},
        "diff":            # 对账偏差 = 经营估算 − 实际现金流 (C/D建表后自动并入)
            {"count": op_cnt - fc_cnt, "amount": round(op_amt - fc_amt, 2)},
    }


# ---------- 兼容旧接口保留: 直接退款 = 路线A (现在与 _route_a_online_refund_filter 一致) ----------
def _direct_refund_filter():
    """兼容旧名: 直接退款 = 路线A (退费单 refund_method='线上', 严格按用户要求)"""
    return _route_a_online_refund_filter()


def _real_cash_out_total(db: Session) -> tuple[float, float]:
    """兼容旧名: 真实现金流(A+B) = 直接退款A + 用户提现B,返回 (A, B)"""
    r = _cash_routes_total(db)
    return r["route_a"]["amount"], r["route_b"]["amount"]


def cash_out_trend(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """真实现金流出趋势(按日) 【全域统一核算 v3】

    四路经营出款 (A/B/C/D) 单独按日堆叠 = 经营估算 total;
    实际现金流 E (merchant_refunds SUCCESS合计,不分桶) 作为独立曲线 overlay;
    对账偏差 diff = 经营(total) − 实际(merchant);
    每 day 返回 direct(A) withdraw(B) shanmao(C) other_refund(D), total(=A+B+C+D),
    merchant(=E 实际现金流), diff(=total−merchant), 及各自 count.
    C/D 独立表未建 → 读 0, 建表后自动生效.
    """
    start, end = _date_range(start, end)
    s, e = start, end
    # ── A 直接退款按日 (路线A refunds线上直退) ──
    direct_rows = db.execute(
        select(
            func.to_char(Refund.created_at_raw, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(*_route_a_online_refund_filter(), Refund.created_at_raw.between(s, e))
        .group_by("date")
    ).all()
    direct_map = {r.date: {"amount": float(r.amount or 0), "count": r.count} for r in direct_rows}
    # ── B 用户提现按日 (路线B wallet) ──
    withdraw_rows = db.execute(
        select(
            func.to_char(WalletWithdrawal.op_time, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(WalletWithdrawal.op_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(WalletWithdrawal.op_time.between(s, e), *_route_b_withdraw_filter())
        .group_by("date")
    ).all()
    withdraw_map = {r.date: {"amount": float(r.amount or 0), "count": r.count} for r in withdraw_rows}
    # ── C 闪猫退款按日 (独立表 shenmao_refund 占位, 表不存在→空map) ──
    shanmao_map: dict[str, dict] = {}
    try:
        with db.begin_nested():
            sm_rows = db.execute(text(f"""
            SELECT to_char({_C_SHANMAO_TIME_COL}, 'YYYY-MM-DD') AS date,
                   COUNT(*) AS cnt,
                   COALESCE(SUM({_C_SHANMAO_AMOUNT_COL})::numeric,0) AS amt
            FROM {_C_SHANMAO_TABLE}
            WHERE {_C_SHANMAO_TIME_COL} IS NOT NULL
              {'AND ' + _C_SHANMAO_STATUS_COL + ' = :st' if _C_SHANMAO_STATUS_COL else ''}
              AND {_C_SHANMAO_TIME_COL} >= :s AND {_C_SHANMAO_TIME_COL} <= :e
            GROUP BY 1
            """), {
                **({"st": _C_SHANMAO_STATUS_OK} if _C_SHANMAO_STATUS_COL else {}),
                "s": s, "e": e,
            }).all()
        shanmao_map = {r[0]: {"amount": float(r[2] or 0), "count": int(r[1] or 0)} for r in sm_rows}
    except Exception:
        pass   # 表未建 → 空 (savepoint 已隔离 PG 事务失败状态)
    # ── D 其他退款按日 (独立表 other_refund 占位) ──
    other_map: dict[str, dict] = {}
    try:
        with db.begin_nested():
            ot_rows = db.execute(text(f"""
            SELECT to_char({_D_OTHER_TIME_COL}, 'YYYY-MM-DD') AS date,
                   COUNT(*) AS cnt,
                   COALESCE(SUM({_D_OTHER_AMOUNT_COL})::numeric,0) AS amt
            FROM {_D_OTHER_TABLE}
            WHERE {_D_OTHER_TIME_COL} IS NOT NULL
              {'AND ' + _D_OTHER_STATUS_COL + ' = :st' if _D_OTHER_STATUS_COL else ''}
              AND {_D_OTHER_TIME_COL} >= :s AND {_D_OTHER_TIME_COL} <= :e
            GROUP BY 1
            """), {
                **({"st": _D_OTHER_STATUS_OK} if _D_OTHER_STATUS_COL else {}),
                "s": s, "e": e,
            }).all()
        other_map = {r[0]: {"amount": float(r[2] or 0), "count": int(r[1] or 0)} for r in ot_rows}
    except Exception:
        pass
    # ── E 实际现金流按日 (merchant_refunds.SUCCESS 合计, 不分 refund_type 桶) ──
    actual_rows = db.execute(
        select(
            func.to_char(MerchantRefund.trade_time, "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(MerchantRefund.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(MerchantRefund.trade_time.between(s, e), *_route_e_merchant_cashflow_filter())
        .group_by("date")
    ).all()
    actual_map = {r.date: {"amount": float(r.amount or 0), "count": r.count} for r in actual_rows}

    # 合并日期 (A/B/C/D/E 五路去重并集)
    dates = sorted(
        set(direct_map) | set(withdraw_map) | set(shanmao_map) | set(other_map) | set(actual_map)
    )

    def _d(m, d, k):
        return m.get(d, {"amount": 0, "count": 0})[k]
    return [
        {
            "date": d,
            # A 退费单线上直退
            "direct":       _d(direct_map, d, "amount"),
            "direct_count": _d(direct_map, d, "count"),
            # B 钱包用户取现
            "withdraw":       _d(withdraw_map, d, "amount"),
            "withdraw_count": _d(withdraw_map, d, "count"),
            # C 闪猫退款 (独立表)
            "shanmao":        _d(shanmao_map, d, "amount"),
            "shanmao_count":  _d(shanmao_map, d, "count"),
            # D 其他退款 (独立表)
            "other_refund":        _d(other_map, d, "amount"),
            "other_refund_count":  _d(other_map, d, "count"),
            # 经营估算 = A+B+C+D (四路经营出款)
            "total": (_d(direct_map, d, "amount") + _d(withdraw_map, d, "amount")
                      + _d(shanmao_map, d, "amount") + _d(other_map, d, "amount")),
            "total_count": (_d(direct_map, d, "count") + _d(withdraw_map, d, "count")
                            + _d(shanmao_map, d, "count") + _d(other_map, d, "count")),
            # 实际现金流 E = merchant_refunds SUCCESS 合计 (对账基准)
            "merchant":       _d(actual_map, d, "amount"),
            "merchant_count": _d(actual_map, d, "count"),
            # 对账偏差 = 经营估算 − 实际现金流
            "diff": (_d(direct_map, d, "amount") + _d(withdraw_map, d, "amount")
                     + _d(shanmao_map, d, "amount") + _d(other_map, d, "amount"))
                  - _d(actual_map, d, "amount"),
            "diff_count": (_d(direct_map, d, "count") + _d(withdraw_map, d, "count")
                           + _d(shanmao_map, d, "count") + _d(other_map, d, "count"))
                        - _d(actual_map, d, "count"),
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
    """真实现金流构成明细 【全域统一核算 v3】

    四路经营出款: A 退费单线上直退 / B 钱包用户取现 / C 闪猫退款(独立表,暂0) / D 其他退款(独立表,暂0)
    经营估算 = A+B+C+D (四路登记出款合计, C/D建表后自动并入)
    实际现金流 = E (merchant_refunds SUCCESS 合计, 对账基准, 不分C/D桶)
    对账偏差 = 经营估算 − 实际现金流
    """
    routes = _cash_routes_total(db)
    a = routes["route_a"]
    b = routes["route_b"]
    c = routes["route_c"]
    d = routes["route_d"]
    op = routes["operational"]        # 经营估算 = A+B+C+D
    e = routes["actual_cashflow"]     # 实际现金流 = E = 商户号SUCCESS合计
    fc = routes["financial"]          # 向后兼容: financial = actual_cashflow = E
    diff = routes["diff"]             # 对账偏差 = 经营 − 实际
    return {
        # ── 四路线拆分 ──
        "route_a": a,  # A.退费单-线上直退 (refund_method='线上')
        "route_b": b,  # B.钱包-用户取现 (op_type='用户提现')
        "route_c": c,  # C.闪猫退款 (独立业务表 shenmao_refund, 暂未建表=0)
        "route_d": d,  # D.其他退款 (独立业务表 other_refund, 暂未建表=0)
        # ── 双视角合计 ──
        "direct_refund": a["amount"],       # 兼容旧键: 直接退款 = A
        "user_withdraw": b["amount"],       # 兼容旧键: 用户提现 = B
        "total": op["amount"],              # 兼容旧键: 经营估算 = A+B+C+D (原 total = A+B, 现升级含C/D)
        "total_count": op["count"],         # 经营侧估算笔数
        "actual_cashflow": e["amount"],     # (新) 实际现金流 E = 商户号SUCCESS合计
        "actual_cashflow_count": e["count"],# (新) 实际现金流笔数
        "merchant_cash": fc["amount"],      # 兼容旧键: 商户号现金流 = E = actual_cashflow (财务实际)
        "merchant_cash_count": fc["count"], # 兼容旧键: 财务侧实际笔数
        "diff": diff["amount"],             # 兼容旧键: 对账偏差 = 经营估算 − 实际现金流 (C/D未建表时=A+B−E)
        "diff_count": diff["count"],        # 对账偏差笔数
        "_spec": {
            "total": "经营估算 = 四路经营出款 A+B+C+D (C/D独立表建表后自动并入)",
            "merchant_cash": "实际现金流(对账基准) = merchant_refunds.refund_status='SUCCESS' 合计, 不分ORIGINAL/BALANCE桶",
            "diff": "对账偏差 = 经营估算 − 实际现金流 (C/D独立表未建时暂为 A+B − E)",
        },
    }


def merchant_cash_summary(db: Session) -> list[dict]:
    """商户号现金流对账(按商户号) 【统一口径: 实际现金流E, refund_status='SUCCESS' 不分桶】

    实际现金流 E = merchant_refunds.refund_status='SUCCESS' 全部合计 (ORIGINAL+BALANCE 均算入,
    不拆分 C/D 桶); 此结果对应对账基准(商户侧真实扣款), 与四路经营出款做对账差异分析.
    原 trade_status='REFUND' 口径废弃 (该字段是微信侧状态位, 混有非成功退款的中间态).
    """
    rows = db.execute(
        select(
            MerchantRefund.merchant_id,
            func.coalesce(func.sum(MerchantRefund.refund_amount), 0).label("amount"),
            func.count().label("count"),
            func.coalesce(func.sum(MerchantRefund.fee), 0).label("fee"),
        )
        .where(MerchantRefund.refund_status == "SUCCESS")
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

# 工单类别兜底 CASE:
#   service_category 为空 → 100%是投诉(已验证7.30-8.1共883条空类别,投诉占比100%)
#   其它保持原值(退款/套餐疑问/其他/换货/设备故障/网络故障)
# 用于所有按 service_category 聚合的场景,避免"空"类别在报表里散成孤立点
def _ticket_category_case(col=ServiceTicket.service_category):
    return case(
        (col.is_(None), "投诉"),
        else_=col,
    ).label("ticket_category")


# 投诉子类(从 service_tickets.service_item 归一化):
#   商品投诉/服务投诉/微信投诉/12315/工信部/公共服务平台 → 保留原名
#   其余 → "其它投诉"
def _complaint_subitem_case():
    si = ServiceTicket.service_item
    return case(
        (si == None, "未标注"),
        (si.ilike("%商品%"), "商品投诉"),
        (si.ilike("%服务%"), "服务投诉"),
        (si.ilike("%微信%"), "微信投诉"),
        (si.ilike("%12315%"), "全国12315平台"),
        (si.ilike("%工信部%"), "工信部投诉"),
        (si.ilike("%公共%"), "公共服务平台"),
        else_=si,
    ).label("complaint_sub")


# 投诉工单根因分类 CASE (用关联 cs_record.problem_category/problem_type 归并):
#   网络质量类: 网络问题/网卡/慢/信号差/无网络/离线/无WiFi/激活网卡
#   套餐争议类: 套餐问题/涨价/套餐升级/流量统计差异/360天套餐/连续包月 等
#   退款失败类: 退款大类 (cs_record.problem_category='退款' 表示客服在推退款方案仍升级投诉)
#   降成争议类: 降成问题/限速涨价
#   其它(注销/发票/退换货等): 其他问题/激活实名
def _complaint_root_case(pcat, ptype):
    net_kw = ("网", "卡", "信号", "离线", "WiFi", "wifi")
    pkg_kw = ("套餐", "涨价", "流量统计", "360天", "包月", "加油包", "返现", "预存", "切网")
    downgrade_kw = ("限速", "降成", "钻石VIP", "VIP", "赠送")
    other_kw = ("注销", "发票", "退换货")
    # 先判断 problem_type 关键词 (更细)
    pt_type = func.coalesce(ptype, func.literal(""))
    pc_cat = func.coalesce(pcat, func.literal(""))
    return case(
        # 网络质量: 类型含网/卡/信号/离线 或 大类是网络问题
        (
            or_(
                pc_cat == "网络问题",
                *[pt_type.ilike(f"%{k}%") for k in net_kw],
            ),
            "网络质量类",
        ),
        # 套餐争议: 大类=套餐问题 或 类型含套餐/涨价 等关键词
        (
            or_(
                pc_cat == "套餐问题",
                *[pt_type.ilike(f"%{k}%") for k in pkg_kw],
            ),
            "套餐争议类",
        ),
        # 退款方案仍升级成投诉 (客服在推退款但用户不满升级投诉)
        (pc_cat == "退款", "退款协商失败类"),
        # 降成/限速争议
        (
            or_(
                pc_cat == "降成问题",
                *[pt_type.ilike(f"%{k}%") for k in downgrade_kw],
            ),
            "降成争议类",
        ),
        # 其他问题 子项 (注销/发票/退换货)
        (
            or_(
                pc_cat == "其他问题",
                pc_cat == "激活实名",
                *[pt_type.ilike(f"%{k}%") for k in other_kw],
            ),
            "其它(注销/发票/退换)",
        ),
        else_="待标注",
    ).label("complaint_root")


def category_by_service_item(db: Session) -> list[dict]:
    """按服务项汇总(含类别兜底:空类别→投诉,返回外层category便于前端分组)"""
    rows = db.execute(
        select(
            _ticket_category_case(),
            ServiceTicket.service_item,
            func.coalesce(func.sum(ServiceTicket.refund_amount), 0),
            func.count(),
        )
        .where(ServiceTicket.service_item.isnot(None))
        .group_by("ticket_category", ServiceTicket.service_item)
        .order_by(func.sum(ServiceTicket.refund_amount).desc())
    ).all()
    total = sum(float(r[2] or 0) for r in rows) or 1
    return [
        {
            "category": r[0],  # 投诉/退款/套餐疑问/...
            "name": r[1],
            "value": float(r[2] or 0),
            "count": r[3],
            "ratio": round(float(r[2] or 0) / total, 4),
        }
        for r in rows
    ]


def category_by_merchant(db: Session, limit: int = 20) -> list[dict]:
    """按商户汇总退款 【统一口径: 路线A 退费单-线上直退】"""
    rows = db.execute(
        select(Refund.merchant_name, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .where(Refund.merchant_name.isnot(None), *_route_a_online_refund_filter())
        .group_by(Refund.merchant_name)
        .order_by(func.sum(Refund.refund_amount).desc())
        .limit(limit)
    ).all()
    return [{"name": r[0], "value": float(r[1] or 0), "count": r[2]} for r in rows]


def category_by_package(db: Session, limit: int = 20) -> list[dict]:
    """按套餐汇总退款 【统一口径: 路线A 退费单-线上直退】"""
    rows = db.execute(
        select(Refund.package_name, func.coalesce(func.sum(Refund.refund_amount), 0), func.count())
        .where(Refund.package_name.isnot(None), *_route_a_online_refund_filter())
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
    """人员审核统计(审核人) 【统一口径: 路线A 退费单-线上直退】"""
    start, end = _date_range(start, end)
    rows = db.execute(
        select(
            Refund.auditor,
            func.count().label("audit_count"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("audit_amount"),
        )
        .where(Refund.auditor.isnot(None),
               Refund.created_at_raw.between(start, end),
               *_route_a_online_refund_filter())
        .group_by(Refund.auditor)
        .order_by(func.sum(Refund.refund_amount).desc())
    ).all()
    return [
        {"operator": r[0], "audit_count": r[1], "audit_amount": float(r[2] or 0)}
        for r in rows
    ]


def operator_refund_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员退费操作统计(操作人,排除 sys/system) 【统一口径: 路线A 退费单-线上直退】"""
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
            *_route_a_online_refund_filter(),
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
    """首页汇总卡片 【全域统一退款核算 v3: 四路经营出款 + 实际现金流 + 对账偏差】"""
    # ── 统一核算: 四路A/B/C/D + 实际现金流E + 对账偏差 ──
    routes = _cash_routes_total(db)
    a = routes["route_a"]   # A.退费单线上直退 (refunds refund_method='线上')
    b = routes["route_b"]   # B.钱包用户取现 (wallet op_type='用户提现')
    c = routes["route_c"]   # C.闪猫退款 (独立业务表 shenmao_refund, 暂未建表=0)
    d = routes["route_d"]   # D.其他退款 (独立业务表 other_refund, 暂未建表=0)
    op = routes["operational"]          # 经营估算 = A+B+C+D (四路经营出款)
    e = routes["actual_cashflow"]       # 实际现金流 E = merchant_refunds SUCCESS合计 (对账基准,不分C/D桶)
    fc = routes["financial"]            # 向后兼容: financial = actual_cashflow = E
    diff = routes["diff"]               # 对账偏差 = 经营 − 实际 (C/D未建表时=A+B−E)

    # 兼容旧键: "退款总金额/笔数" 严格按路线A (退费单线上直退)
    refund_total = float(a["amount"])
    refund_count = a["count"]
    estimated = op["amount"]
    merchant_cash = fc["amount"]

    # 钱包余额总计(各账户最新余额之和)
    wallet_balance = db.scalar(
        select(func.coalesce(func.sum(WalletWithdrawal.wallet_balance), 0))
    ) or 0
    # 客服接待量(智能助手处理记录数)
    cs_reception_count = db.scalar(select(func.count()).select_from(CsRecord)) or 0

    # ---- 投诉统计 (service_category 兜底=投诉) ----
    # 1. 投诉工单总数 (service_category IS NULL → 投诉)
    complaint_total = db.scalar(
        select(func.count())
        .select_from(ServiceTicket)
        .where(_ticket_category_case() == "投诉")
    ) or 0
    # 2. 投诉子类分布
    complaint_sub_rows = db.execute(
        select(_complaint_subitem_case(), func.count().label("cnt"))
        .select_from(ServiceTicket)
        .where(_ticket_category_case() == "投诉")
        .group_by("complaint_sub")
        .order_by(text("cnt DESC"))
    ).all()
    complaint_by_sub = [
        {"name": r[0], "value": int(r[1] or 0)} for r in complaint_sub_rows
    ]
    # 3. 投诉状态分布 (待处理/已完成/终止)
    complaint_status_rows = db.execute(
        select(
            func.coalesce(ServiceTicket.status, "未标注").label("st"),
            func.count().label("cnt"),
        )
        .select_from(ServiceTicket)
        .where(_ticket_category_case() == "投诉")
        .group_by("st")
        .order_by(text("cnt DESC"))
    ).all()
    complaint_by_status = [
        {"name": r[0], "value": int(r[1] or 0)} for r in complaint_status_rows
    ]
    # 4. 工单总数(非投诉类)
    ticket_total = db.scalar(select(func.count()).select_from(ServiceTicket)) or 0

    return {
        # ── 兼容旧键: 保持向后兼容 ──
        "refund_total": refund_total,          # 退款总金额 = 路线A(退费单线上直退)
        "refund_count": refund_count,          # 退款总笔数 = 路线A(退费单线上直退)
        "cash_out_total": estimated,           # 经营估算现金流 = A+B+C+D (C/D建表后自动并入)
        "cash_out_direct": a["amount"],        # 直接退款 = A
        "cash_out_withdraw": b["amount"],      # 用户提现 = B
        "actual_cashflow": e["amount"],        # (新) 实际现金流 E = 商户号SUCCESS合计
        "merchant_cash": merchant_cash,        # 兼容旧键: 商户号现金流 = E = actual_cashflow
        "cash_diff": diff["amount"],           # 对账偏差 = 经营估算 − 实际现金流 (C/D未建=A+B−E)
        "wallet_balance_total": float(wallet_balance),
        "cs_reception_count": cs_reception_count,
        # --- 投诉维度 ---
        "ticket_total": ticket_total,
        "complaint_total": complaint_total,
        "complaint_ratio": round(complaint_total / ticket_total, 4) if ticket_total else 0,
        "complaint_by_sub": complaint_by_sub,
        "complaint_by_status": complaint_by_status,
        # ── 新增: 四路线拆分 + 实际现金流E + 对账偏差 (前端可直接渲染卡片) ──
        "cash_routes": {
            "route_a": a,    # {count, amount} A.退费单线上直退
            "route_b": b,    # {count, amount} B.钱包用户取现
            "route_c": c,    # {count, amount} C.闪猫退款 (独立表占位,暂0)
            "route_d": d,    # {count, amount} D.其他退款 (独立表占位,暂0)
            "operational": op,              # {count, amount} 经营估算 = A+B+C+D
            "actual_cashflow": e,           # {count, amount} 实际现金流 = E (商户号SUCCESS合计,对账基准)
            "financial": fc,                # {count, amount} 向后兼容: 财务实际 = actual_cashflow = E
            "diff": diff,                   # {count, amount} 对账偏差 = 经营 − 实际
        },
        # ── 口径说明注释 (前端tooltip可直接显示) ──
        "_spec": {
            "refund_total": "路线A.退费单线上直退 (refunds WHERE refund_method='线上')",
            "cash_out_total": "经营估算(四路经营出款合计) = A线上直退 + B用户取现 + C闪猫退款(独立表) + D其他退款(独立表), C/D建表后自动并入",
            "actual_cashflow": "实际现金流(对账基准) = merchant_refunds WHERE refund_status='SUCCESS' 合计, 不分ORIGINAL/BALANCE桶",
            "merchant_cash": "实际现金流(对账基准,同actual_cashflow) = 商户号SUCCESS退款合计",
            "cash_diff": "对账偏差 = 经营估算 − 实际现金流 (C/D独立表未建时临时为 A+B − E)",
        },
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


# 有效退款方式: A = 转入提现账户 (退款到钱包, 后续用户提现构成现金流出)
#                  B = 直接线上退款 (严格按统一规范: 只认 refund_method='线上' 才算一条退款路线)
#                  其他方式(线下/未填写/含"微信/支付宝/银联/银行卡/直退"关键词但不含"线上"二字) → 无效剔除
# 全域对齐规范: 统一核算的路线A = 这里的路径B (退费单线上直退), 路线B = 这里的路径A (钱包用户提现)
_B_METHOD_KEYS = ("线上",)


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
    """模块1: 今日KPI卡 + 昨日同时段环比 【全域统一退款核算 v3】

    口径严格对齐 v3 规范:
      - 今日退款笔数/金额 = 路线A (退费单 refund_method='线上')
      - 真实现金流出(经营估算) = A+B+C+D 四路经营出款 (C/D独立表未建时=A+B)
      - 实际现金流(对账基准) = E (merchant_refunds WHERE refund_status='SUCCESS' 合计, 不分ORIGINAL/BALANCE桶)
      - 对账偏差 = 经营估算 − 实际现金流
    date=None 表示今天(实时到当前小时); date='2026-07-31' 表示整天.
    """
    d = _parse_date(date)
    ts, te, ys, ye = _today_windows(d)

    def _between(col, s, e):
        return and_(col.isnot(None), col >= s, col <= e)

    # 1. 今日接待量 (cs_records.operation_time)
    cs_today = db.execute(select(func.count()).where(_between(CsRecord.operation_time, ts, te))).scalar() or 0
    cs_yoy = db.execute(select(func.count()).where(_between(CsRecord.operation_time, ys, ye))).scalar() or 0

    # 2/3. 今日退款笔数+金额 【统一口径=路线A refund_method='线上'】
    rf_row = db.execute(select(func.count(), func.coalesce(func.sum(Refund.refund_amount), 0))
                        .where(_between(Refund.created_at_raw, ts, te),
                               *_route_a_online_refund_filter())).first()
    rf_cnt_t, rf_amt_t = int(rf_row[0] or 0), float(rf_row[1] or 0)
    rf_row_y = db.execute(select(func.count(), func.coalesce(func.sum(Refund.refund_amount), 0))
                          .where(_between(Refund.created_at_raw, ys, ye),
                                 *_route_a_online_refund_filter())).first()
    rf_cnt_y, rf_amt_y = int(rf_row_y[0] or 0), float(rf_row_y[1] or 0)

    # 4. 真实现金流(经营估算) = A+B+C+D 四路经营出款 (通过统一聚合器计算)
    #    实际现金流 E = merchant_refunds SUCCESS合计
    routes_t = _cash_routes_total(db, start=ts, end=te)
    routes_y = _cash_routes_total(db, start=ys, end=ye)
    a_t, b_t = routes_t["route_a"], routes_t["route_b"]
    a_y, b_y = routes_y["route_a"], routes_y["route_b"]
    c_t, d_t = routes_t["route_c"], routes_t["route_d"]
    e_t = routes_t["actual_cashflow"]          # 实际现金流 E (今日)
    e_y = routes_y["actual_cashflow"]          # 实际现金流 E (昨日)
    cash_t = routes_t["operational"]["amount"] # 经营估算 A+B+C+D
    cash_y = routes_y["operational"]["amount"] # 经营估算 昨日
    merchant_today = e_t["count"]              # 实际现金流笔数 (注意是E的count,不是C+D)
    merchant_tip = None
    if (d is None or d == _dt.datetime.now(TZ).date()) and merchant_today == 0:
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

    # 7. 今日投诉单 (service_category 兜底=投诉, register_time 落当天)
    comp_t = db.execute(select(func.count())
                        .where(_between(ServiceTicket.register_time, ts, te),
                               _ticket_category_case() == "投诉")).scalar() or 0
    comp_y = db.execute(select(func.count())
                        .where(_between(ServiceTicket.register_time, ys, ye),
                               _ticket_category_case() == "投诉")).scalar() or 0

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
        "cash_out":       _k(round(cash_t, 2), round(cash_y, 2)),      # 经营估算 A+B+C+D
        "actual_cashflow":_k(round(e_t["amount"], 2), round(e_y["amount"], 2)), # (新) 实际现金流E
        "ticket_done":    _k(int(done_t), int(done_y)),
        "staff_on_duty":  _k(int(staff_t), int(staff_y)),
        "complaint":      _k(int(comp_t), int(comp_y)),
        "merchant_tip":   merchant_tip,
        # ── 新增: 今日四路线拆分 + 实际现金流E + 对账偏差 (前端tooltip/明细卡) ──
        "cash_routes_today": {
            "route_a": a_t,  # A.退费单线上直退
            "route_b": b_t,  # B.钱包用户取现
            "route_c": c_t,  # C.闪猫退款 (独立表占位,暂0)
            "route_d": d_t,  # D.其他退款 (独立表占位,暂0)
            "operational":     routes_t["operational"],     # 经营估算 A+B+C+D
            "actual_cashflow": routes_t["actual_cashflow"], # 实际现金流 E (商户号SUCCESS合计)
            "financial":       routes_t["financial"],       # 向后兼容: 财务实际 = E
            "diff":            routes_t["diff"],            # 对账偏差 = 经营 − 实际
        },
        # ── 口径注释 (前端标题tooltip) ──
        "_spec": {
            "refund_count":     "路线A: 退费单 refund_method='线上' (严格按用户要求:退费单管理里只这条算退款路线)",
            "refund_amount":    "路线A: 退费单 refund_method='线上'",
            "cash_out":         "经营估算 = 四路经营出款 A线上直退 + B用户取现 + C闪猫退款(独立表) + D其他退款(独立表), C/D建表后自动并入",
            "actual_cashflow":  "实际现金流(对账基准) = merchant_refunds WHERE refund_status='SUCCESS' 合计, 不分ORIGINAL/BALANCE桶",
        },
    }


def today_hourly_trend(db: Session, date: str | None = None) -> dict[str, Any]:
    """模块2: 今日小时节奏 (上海时区 00:00~23:00, 每小时一行) 【统一口径: 退款=路线A】

    左Y数量级: reception_count / refund_count(路线A) / complaint 三柱并列
    右Y金额级: refund_amount(路线A) 折线
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

    # 退款: 小时级聚合 笔数 + 金额 【统一口径=路线A refund_method='线上'】
    rf_rows = db.execute(
        select(_h(Refund.created_at_raw),
               func.count().label("cnt"),
               func.coalesce(func.sum(Refund.refund_amount), 0).label("amt"))
        .where(Refund.created_at_raw.isnot(None),
               Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
               *_route_a_online_refund_filter())
        .group_by("h")
    ).all()
    rf_cnt_map = {r.h.hour: int(r.cnt or 0) for r in rf_rows if r.h}
    rf_amt_map = {r.h.hour: float(r.amt or 0) for r in rf_rows if r.h}

    # 投诉: 小时级聚合 按 service_tickets.register_time, 空类别兜底=投诉
    cp_rows = db.execute(
        select(_h(ServiceTicket.register_time), func.count().label("cnt"))
        .where(ServiceTicket.register_time.isnot(None),
               ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te,
               _ticket_category_case() == "投诉")
        .group_by("h")
    ).all()
    cp_map = {r.h.hour: int(r.cnt or 0) for r in cp_rows if r.h}

    # 填充到当前小时(今天,上海时区)或整天24小时(历史日期)
    now_sh = _dt.datetime.now(TZ)
    is_today = d is None or d == now_sh.date()
    max_h = now_sh.hour if is_today else 23
    hours = list(range(24))
    return {
        "date": (d or now_sh.date()).isoformat(),
        "hours": hours,
        "hour_labels": [f"{h:02d}:00" for h in hours],
        "reception":     [cs_map.get(h, 0)     if h <= max_h else None for h in hours],
        "refund_count":  [rf_cnt_map.get(h, 0) if h <= max_h else None for h in hours],
        "refund_amount": [round(rf_amt_map.get(h, 0.0), 2) if h <= max_h else None for h in hours],
        "complaint":     [cp_map.get(h, 0)     if h <= max_h else None for h in hours],
        "_spec": {
            "refund_count":  "路线A: 退费单 refund_method='线上'",
            "refund_amount": "路线A: 退费单 refund_method='线上'",
        },
    }


def today_reception_breakdown(db: Session, date: str | None = None) -> dict[str, Any]:
    """模块3: 今日接待分项 = 分类饼图 + Top10客服柱图 + 投诉子类饼图 + 投诉根因柱图.
    分类: 6 类 (投诉/退款/套餐问题/咨询/综合问题/其他问题)
    投诉相关基于 service_tickets (service_category 兜底=投诉), 根因通过 device_sn ±24h 关 cs_record 判断
    """
    d = _parse_date(date)
    ts, te, _, _ = _today_windows(d)
    between = and_(CsRecord.operation_time.isnot(None),
                   CsRecord.operation_time >= ts, CsRecord.operation_time <= te)
    tk_between = and_(ServiceTicket.register_time.isnot(None),
                      ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te)

    # 左1: 智能接待分类饼 (复用 _category_case)
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
    present = {c["name"] for c in categories}
    for nm in order:
        if nm not in present:
            categories.append({"name": nm, "value": 0})

    # 右1: Top10客服 (按operator分组count, 关联PersonAlias→Person拿group_name/display_name)
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

    # ---------- 新增: 今日投诉统计 (service_tickets 表, 空类别兜底=投诉) ----------
    # 左2: 投诉子类饼图 (从 service_item 归一化, 仅统计今日投诉工单)
    comp_sub_rows = db.execute(
        select(_complaint_subitem_case(), func.count().label("cnt"))
        .where(tk_between, _ticket_category_case() == "投诉")
        .group_by("complaint_sub")
        .order_by(text("cnt DESC"))
    ).all()
    complaint_subs = [
        {"name": r[0] or "未标注", "value": int(r[1] or 0)}
        for r in comp_sub_rows
    ]
    # 今日投诉总数 (便于前端展示 "今日共XX条投诉")
    complaint_total = sum(v["value"] for v in complaint_subs) or 0

    # 右2: 投诉根因柱图 (投诉工单关联 cs_record 的 problem_category/type 归并到 6 大类根因)
    #   逻辑: 投诉工单(device_sn) JOIN cs_record(device_sn) ±24h, 取 diff_min 最小的 1 条
    #   再用 _complaint_root_case 归并根因
    comp_root_sql = text(
        """
        WITH comp_tickets AS (
            SELECT
                st.ticket_no,
                st.service_item,
                st.device_sn,
                st.register_time,
                st.remark,
                st.handle_opinion
            FROM service_tickets st
            WHERE st.register_time IS NOT NULL
              AND st.register_time >= :ts
              AND st.register_time <= :te
              AND (st.service_category IS NULL)  -- 投诉工单 (兜底规则)
        ),
        nearest_cs AS (
            SELECT DISTINCT ON (ct.ticket_no)
                ct.ticket_no,
                ct.service_item,
                c.problem_category,
                c.problem_type
            FROM comp_tickets ct
            JOIN cs_records c
              ON ct.device_sn = c.device_sn
              AND c.operation_time BETWEEN (ct.register_time - INTERVAL '24 hours')
                                       AND (ct.register_time + INTERVAL '24 hours')
            WHERE c.operation_time IS NOT NULL
            ORDER BY ct.ticket_no,
                     ABS(EXTRACT(EPOCH FROM (c.operation_time - ct.register_time))) ASC
        )
        SELECT
            CASE
                -- 网络质量
                WHEN COALESCE(problem_category, '') = '网络问题'
                     OR COALESCE(problem_type, '') ~* '(网|卡|信号|离线|WiFi|激活网卡)'
                THEN '网络质量类'
                -- 套餐争议
                WHEN COALESCE(problem_category, '') = '套餐问题'
                     OR COALESCE(problem_type, '') ~* '(套餐|涨价|流量统计|360天|包月|加油包|返现|预存|切网)'
                THEN '套餐争议类'
                -- 退款协商失败
                WHEN COALESCE(problem_category, '') = '退款'
                THEN '退款协商失败类'
                -- 降成争议
                WHEN COALESCE(problem_category, '') = '降成问题'
                     OR COALESCE(problem_type, '') ~* '(限速|降成|钻石VIP|VIP|赠送)'
                THEN '降成争议类'
                -- 其它 (注销/发票/退换货)
                WHEN COALESCE(problem_category, '') IN ('其他问题', '激活实名')
                     OR COALESCE(problem_type, '') ~* '(注销|发票|退换货)'
                THEN '其它(注销/发票/退换)'
                ELSE '待标注'
            END AS root_cause,
            COUNT(*) AS cnt
        FROM nearest_cs
        GROUP BY 1
        ORDER BY cnt DESC;
        """
    )
    comp_root_rows = db.execute(comp_root_sql, {"ts": ts, "te": te}).fetchall()
    complaint_roots = [
        {"name": r[0], "count": int(r[1] or 0)} for r in comp_root_rows
    ]
    # 已关联到接待记录的投诉数 (从子查询 GROUP BY 汇总, 再减去补齐的 0 项前计算)
    matched_count = sum(r["count"] for r in complaint_roots)
    # 补齐根因到常见的 6 类, 缺失补 0 (便于前端颜色固定)
    root_order = [
        "网络质量类", "套餐争议类", "退款协商失败类",
        "降成争议类", "其它(注销/发票/退换)", "待标注",
    ]
    present_root = {r["name"] for r in complaint_roots}
    for nm in root_order:
        if nm not in present_root:
            complaint_roots.append({"name": nm, "count": 0})
    complaint_roots.sort(key=lambda x: -x["count"])

    # 今日未关联 cs_record 的投诉数 (关联覆盖率提示)
    complaint_unmatched = max(0, complaint_total - matched_count)

    return {
        "date": (d or _dt.datetime.now(TZ).date()).isoformat(),
        # 原接待分类
        "categories": sorted(categories, key=lambda x: -x["value"]),
        "top_staff": top_staff,
        # 新增: 今日投诉
        "complaint_total": complaint_total,
        "complaint_subs": complaint_subs,     # 投诉子类 (饼图: 商品投诉/服务投诉/微信投诉/12315/工信部/公共服务平台)
        "complaint_roots": complaint_roots,   # 投诉根因 (柱图: 6 大类)
        "complaint_unmatched": complaint_unmatched,  # 未匹配到接待记录的投诉数
    }


def today_refund_breakdown(db: Session, date: str | None = None) -> dict[str, Any]:
    """模块4: 今日退款分项 = 退款细分原因Top10 + 退款方式分布 【统一口径: 路线A refund_method='线上'】

    v3 升级(2026-08-02): 用户明确 WiFi钱包/Ai套餐等商品名 不是"退款原因",
    修复 v2 两处缺陷:
      A. 时间窗过窄导致工单漏匹配: |register_time − created_at_raw| < 48h → < 30天
         (抽样发现大量10天~28天前的工单因48h窗被漏掉,落入错误桶)
      B. 兜底语义错误: 未匹配工单 之前 fallback 到 package_name(商品维度)
         → 现统一归入 [原因未登记] 未关联工单 桶；商品维度改在独立 packages 字段返回,不混入原因

    正确分类策略 (只用工单分类 / 统一未登记桶, 绝不混入商品名):
      1. 已匹配工单: <service_category or '投诉'>/<service_item>
         按 device_sn + |register_time - created_at_raw| < 30天 关联，
         每个 refund 用 PG DISTINCT ON 取时间差最小的 1 条工单（防 1 对多重复计数）
      2. 未匹配工单: 统一归为『[原因未登记] 未关联到工单』，不再用商品名兜底
    """
    d = _parse_date(date)
    ts, te, _, _ = _today_windows(d)
    between = and_(Refund.created_at_raw.isnot(None),
                   Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
                   *_route_a_online_refund_filter())
    _WIN_30D = 86400 * 30  # A缺陷修复: ±30天（从 ±48h 放宽）

    # ── ① 左: 细分原因 Top10 (按金额倒序) ──
    # CTE 1: 每个 refund 最佳匹配工单 (DISTINCT ON 取时间差最小的, 无工单时走左连接 NULL)
    best_stmt = select(
        Refund.refund_no.label("rid"),
        Refund.refund_amount.label("amt"),
        Refund.package_name.label("pkg"),
        Refund.refund_reason.label("rrsn"),
        # 工单字段 (左连接, 可能 NULL)
        ServiceTicket.service_category.label("scat"),
        ServiceTicket.service_item.label("sitm"),
        func.abs(func.extract("epoch",
            ServiceTicket.register_time - Refund.created_at_raw
        )).label("diff_sec"),
    ).select_from(Refund).outerjoin(ServiceTicket, and_(
        ServiceTicket.device_sn == Refund.device_sn,
        Refund.device_sn.isnot(None), Refund.device_sn != "",
        ServiceTicket.register_time.isnot(None),
        func.abs(func.extract("epoch",
            ServiceTicket.register_time - Refund.created_at_raw
        )) < _WIN_30D,
    )).where(between)
    # PostgreSQL 方言: DISTINCT ON (rid) 每个 refund 只保留 diff_sec 最小那行(含 NULL 工单行)
    best_cte = best_stmt.distinct(text("rid")).order_by(
        text("rid"), text("diff_sec NULLS LAST")
    ).cte("best_match")

    # CTE 2: 分类名推导（B缺陷修复: 不再用 package_name 混进原因维度）
    def _name_case():
        sc = best_cte.c.scat
        si = best_cte.c.sitm
        # 投诉工单的 service_category 空 → 按既有规范统一回退到 '投诉'
        sc_disp = case(
            (sc.is_(None), "投诉"),
            (sc == "", "投诉"),
            else_=sc,
        )
        has_both = sc.isnot(None) & (sc != "") & si.isnot(None) & (si != "")
        has_sc = sc.isnot(None) & (sc != "")
        matched = case(
            (has_both, sc_disp + " / " + si),
            (has_sc, sc_disp),
            else_=None,
        )
        return case(
            (matched.isnot(None), matched),                    # 1. 工单分类
            else_="[原因未登记] 未关联到工单",                # 2. 未匹配统一桶（B修复: 不混商品）
        )

    reason_agg = db.execute(select(
        _name_case().label("name"),
        func.count().label("cnt"),
        func.coalesce(func.sum(best_cte.c.amt), 0).label("amt"),
    ).group_by(text("name")).order_by(func.sum(best_cte.c.amt).desc())).all()
    reasons = [
        {"name": r[0] or "未填写", "count": int(r.cnt or 0), "amount": round(float(r.amt or 0), 2)}
        for r in reason_agg
    ][:10]  # Top10 按金额

    # ── ② 补充字段: 退款商品分布 Top10 (独立于原因维度, 用于业务分析但不混原因榜) ──
    pkg_agg = db.execute(select(
        Refund.package_name.label("pkg"),
        func.count().label("cnt"),
        func.coalesce(func.sum(Refund.refund_amount), 0).label("amt"),
    ).where(between).group_by(Refund.package_name
    ).order_by(func.sum(Refund.refund_amount).desc()).limit(10)).all()
    packages = [
        {"name": r[0] or "商品名空", "count": int(r.cnt or 0), "amount": round(float(r.amt or 0), 2)}
        for r in pkg_agg
    ]

    # ── ③ 右: 退款方式饼图（主 WHERE 已限定路线A refund_method='线上', 这里仅兼容旧结构保留）
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
        .where(between).group_by(Refund.refund_method)
    ).all()
    method_agg: dict[str, int] = {}
    for r in method_rows:
        b = _bucket_method(r[0])
        method_agg[b] = method_agg.get(b, 0) + int(r.cnt or 0)
    methods = [{"name": k, "value": v} for k, v in method_agg.items()]

    return {
        "date": (d or _dt.datetime.now(TZ).date()).isoformat(),
        "reasons": reasons,                 # 退款原因榜 (仅工单分类 + 未登记统一桶, 无商品名)
        "packages": packages,               # 新增独立榜: 退款商品分布 Top10 (商品维度)
        "methods": methods,
        "_spec": {
            "scope": "路线A: 退费单 refund_method='线上'",
            "reason_strategy": (
                "v3: 1)[service_category or '投诉']/service_item (设备SN±30天关联工单,每退款取时间差最小的1条) → "
                "2)未匹配工单 → '[原因未登记] 未关联到工单' 统一桶 (v3修复: 不再用package_name商品名混入原因维度)"
            ),
            "time_window_seconds": _WIN_30D,
            "packages_strategy": "独立字段 packages: 按 package_name 商品 Top10 (原因榜与商品榜彻底解耦)",
        },
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
    # 2. 退款操作 per operator (排除 sys/system) 【统一口径: 路线A refund_method='线上'】
    rf_q = (select(Refund.operator.label("op"),
                   func.count().label("c"),
                   func.coalesce(func.sum(Refund.refund_amount), 0).label("a"))
            .where(Refund.created_at_raw.isnot(None),
                   Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
                   Refund.operator.isnot(None),
                   Refund.operator.notin_(["sys", "system", "SYS", "System"]),
                   *_route_a_online_refund_filter())
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


