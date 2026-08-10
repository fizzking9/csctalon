"""分析查询服务 - 4 大看板的数据查询逻辑"""

import datetime as _dt
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select, text, union
from sqlalchemy.orm import Session

from app.models.cs_record import CsRecord
from app.models.external_complaint import ExternalComplaint
from app.models.card_complaint import CardComplaint
from app.models.merchant_refund import MerchantRefund
from app.models.person import Person, PersonAlias
from app.models.group import Group, PersonGroupMembership
from app.models.specialty import Specialty, PersonSpecialtyAssignment
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.todo import TodoItem
from app.models.task import SpecialTask
from app.models.incident import Incident
from app.models.wallet import WalletWithdrawal


# ============================================================
# 通用人员筛选 (按部门/分组/专项)
# ============================================================
def _person_filter_conds(department: str | None = None,
                         group_id: int | None = None,
                         specialty_id: int | None = None):
    """返回对Person表的筛选条件列表,用于JOIN后WHERE"""
    conds = []
    if department:
        conds.append(Person.department == department)
    if group_id is not None:
        # 通过PersonGroupMembership关联 (使用EXISTS子查询)
        pass  # group_id/specialty_id通过JOIN处理,这里返回None表示需要额外JOIN
    if specialty_id is not None:
        pass
    return conds


def _apply_person_filters(stmt, person_alias,
                          department: str | None = None,
                          group_id: int | None = None,
                          specialty_id: int | None = None):
    """在已有查询语句上应用人员筛选条件 (person_alias是Person表或其别名)

    通过JOIN/EXISTS方式添加分组和专项筛选,返回修改后的stmt
    """
    if department:
        stmt = stmt.where(person_alias.department == department)
    if group_id is not None:
        # EXISTS子查询检查是否在指定分组
        exists_g = select(1).where(
            PersonGroupMembership.person_id == person_alias.id,
            PersonGroupMembership.group_id == group_id,
            PersonGroupMembership.is_current == True,
        )
        stmt = stmt.where(exists_g.exists())
    if specialty_id is not None:
        # EXISTS子查询检查是否分配了指定专项
        exists_s = select(1).where(
            PersonSpecialtyAssignment.person_id == person_alias.id,
            PersonSpecialtyAssignment.specialty_id == specialty_id,
            PersonSpecialtyAssignment.is_current == True,
        )
        stmt = stmt.where(exists_s.exists())
    return stmt


# ============================================================
# 通用工具: 手机号清洗归一化 (_clean_phone)
# ============================================================
def _clean_phone(raw: str | None) -> str | None:
    """清洗并归一化手机号,用于 COUNT(DISTINCT) / TOP 去重 / JOIN

    规则 (严格保持与规范一致):
      1. 空/纯空白 → None
      2. 去掉前缀 +86 / 86 / 0086 / 0 (仅首字符为0时) 及 ( ) - 空格 等分隔符
      3. 含掩码字符(**** / **** / XXXX) → None (不进去重计数,避免归并)
      4. 清洗后长度 != 11 或不是 1 开头纯数字 → None
      5. 其余 → 11 位纯数字字符串
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # 掩码: 任意含 4 位连续 * 或 X 的直接丢
    if "****" in s or "XXXX" in s or "****" in s:
        return None
    # 去所有非数字字符
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    # 掉 0086 / +86 → 等价于去数字前导 86 (仅当总长 13 时)
    if digits.startswith("0086") and len(digits) == 15:
        digits = digits[4:]
    elif digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 12:
        digits = digits[1:]
    if len(digits) != 11 or not digits.startswith("1"):
        return None
    return digits


def _clean_col_expr(col):
    """SQLAlchemy 表达式版本的 _clean_phone (生成 CASE WHEN 表达式返回归一化后的手机号或 NULL)

    用于 COUNT(DISTINCT) 聚合,避免先拉回 Python 再去重。
    """
    # 简化版本: 先 trim, 如果含 ****/XXXX 直接 NULL; 长度 11 且 1 开头的才保留; 其它 NULL。
    # 去前缀 86/+86 的部分: 去掉所有非数字字符后再判断
    raw = func.coalesce(col, literal(""))
    # 去掉非数字字符 (regexp_replace only available in PG, which we're on)
    digits_ = func.regexp_replace(raw, r"\D", "", "g")
    digits = case(
        (func.substring(digits_, 1, 4) == "0086", func.substring(digits_, 5)),
        (func.substring(digits_, 1, 2) == "86", func.substring(digits_, 3)),
        else_=digits_,
    )
    mask_hit = or_(raw.ilike("%****%"), raw.ilike("%XXXX%"))
    ok = and_(func.length(digits) == 11, func.substring(digits, 1, 1) == "1")
    return case(
        (mask_hit, None),
        (ok, digits),
        else_=None,
    ).label("phone_clean")


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
# 真实表名: shanmao_refunds (alembic: b8c9d0e1f2a3_create_shanmao_refunds)
# 字段映射关系(2026-08-03 实际 Excel 样例):
#   金额: actual_amount(实际退款金额)  而非 refund_amount(申请退款金额)
#   时间: audit_time(审核通过时间 ≈ 退款完成时间)  Excel 无独立 refund_time 列
#   状态列: status  成功值: '已退款'(中文), 其他状态(申请中/已驳回/退款失败等)不计入经营出款
_C_SHANMAO_TABLE = "shanmao_refunds"
_C_SHANMAO_AMOUNT_COL = "actual_amount"
_C_SHANMAO_TIME_COL = "audit_time"
_C_SHANMAO_STATUS_COL = "status"
_C_SHANMAO_STATUS_OK = "已退款"

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

# ---------- 路线C/D 按时间粒度分组聚合 (小时/日) ----------
def _route_c_shanmao_trend(db: Session, start, end, grain: str, slot_idx_fn) -> tuple[dict[int, int], dict[int, float]]:
    """【路线C】闪猫退款按时间分组聚合 (表不存在则返回空map)."""
    trunc_expr = "date_trunc(:grain, timezone('Asia/Shanghai', " + _C_SHANMAO_TIME_COL + "))" if grain == "hour" else "date_trunc(:grain, timezone('Asia/Shanghai', " + _C_SHANMAO_TIME_COL + "))"
    sql_tpl = f"""
    SELECT {trunc_expr} AS trunc_t,
           COUNT(*) AS cnt,
           COALESCE(SUM({_C_SHANMAO_AMOUNT_COL})::numeric,0) AS amt
    FROM {_C_SHANMAO_TABLE}
    WHERE {_C_SHANMAO_TIME_COL} IS NOT NULL
      {"AND " + _C_SHANMAO_STATUS_COL + " = :st" if _C_SHANMAO_STATUS_COL else ""}
      {"AND " + _C_SHANMAO_TIME_COL + " >= :s" if start else ""}
      {"AND " + _C_SHANMAO_TIME_COL + " <= :e" if end else ""}
    GROUP BY trunc_t
    """
    params: dict[str, Any] = {"grain": grain}
    if _C_SHANMAO_STATUS_COL:
        params["st"] = _C_SHANMAO_STATUS_OK
    if start is not None:
        params["s"] = start
    if end is not None:
        params["e"] = end
    cnt_map: dict[int, int] = {}
    amt_map: dict[int, float] = {}
    try:
        with db.begin_nested():
            rows = db.execute(text(sql_tpl), params).all()
        for r in rows:
            tt = r[0]
            if tt is None:
                continue
            idx = slot_idx_fn(tt)
            if idx is None:
                continue
            cnt_map[idx] = int(r[1] or 0)
            amt_map[idx] = float(r[2] or 0)
    except Exception:
        pass
    return cnt_map, amt_map


def _route_d_other_trend(db: Session, start, end, grain: str, slot_idx_fn) -> tuple[dict[int, int], dict[int, float]]:
    """【路线D】其他退款按时间分组聚合 (表不存在则返回空map)."""
    trunc_expr = "date_trunc(:grain, timezone('Asia/Shanghai', " + _D_OTHER_TIME_COL + "))"
    sql_tpl = f"""
    SELECT {trunc_expr} AS trunc_t,
           COUNT(*) AS cnt,
           COALESCE(SUM({_D_OTHER_AMOUNT_COL})::numeric,0) AS amt
    FROM {_D_OTHER_TABLE}
    WHERE {_D_OTHER_TIME_COL} IS NOT NULL
      {"AND " + _D_OTHER_STATUS_COL + " = :st" if _D_OTHER_STATUS_COL else ""}
      {"AND " + _D_OTHER_TIME_COL + " >= :s" if start else ""}
      {"AND " + _D_OTHER_TIME_COL + " <= :e" if end else ""}
    GROUP BY trunc_t
    """
    params: dict[str, Any] = {"grain": grain}
    if _D_OTHER_STATUS_COL:
        params["st"] = _D_OTHER_STATUS_OK
    if start is not None:
        params["s"] = start
    if end is not None:
        params["e"] = end
    cnt_map: dict[int, int] = {}
    amt_map: dict[int, float] = {}
    try:
        with db.begin_nested():
            rows = db.execute(text(sql_tpl), params).all()
        for r in rows:
            tt = r[0]
            if tt is None:
                continue
            idx = slot_idx_fn(tt)
            if idx is None:
                continue
            cnt_map[idx] = int(r[1] or 0)
            amt_map[idx] = float(r[2] or 0)
    except Exception:
        pass
    return cnt_map, amt_map


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
#   service_category 含"投诉"字样 → 识别为投诉(如 SCRM/平台同步过来的 "业务投诉""用户投诉"等)
#   service_item 含外部投诉关键词(12315/工信部/消协/公共服务平台等) → 识别为投诉
#   其它保持原值(退款/套餐疑问/其他/换货/设备故障/网络故障)
# 用于所有按 service_category 聚合的场景,避免"空"类别在报表里散成孤立点,
# 同时兼容 SCRM 平台把"业务投诉"写进 service_category 的新同步口径。
_EXTERNAL_ITEM_COMPLAINT_KW = (
    "12315", "工信部", "消协", "公共服务平台", "消费者协会",
    "监督局", "市场监管", "市监", "315平台", "12345", "政务平台",
)
def _ticket_category_case(
    col=ServiceTicket.service_category,
    item_col=ServiceTicket.service_item,
):
    # item 命中外部投诉关键词 → 强制归为"投诉"
    item_whens = [func.coalesce(item_col, literal("")).ilike(f"%{k}%") for k in _EXTERNAL_ITEM_COMPLAINT_KW]
    # item 命中内部投诉关键词 → 归为"投诉"
    internal_kws = ("商品投诉", "服务投诉", "微信投诉")
    internal_whens = [func.coalesce(item_col, literal("")).ilike(f"%{k}%") for k in internal_kws]
    return case(
        (col.is_(None), "投诉"),
        (col.ilike("%投诉%"), "投诉"),
        (or_(*(item_whens + internal_whens)), "投诉"),
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
        (si.ilike("%消费者协会%"), "消费者协会"),
        (si.ilike("%消协%"), "消费者协会"),
        else_=si,
    ).label("complaint_sub")


# 投诉来源类型(从 service_tickets.service_item 归一化): 区分外部投诉 vs 内部投诉
#   外部投诉: 工信部投诉 / 全国12315平台 / 公共服务平台 / 消费者协会 / 315 / 监督局 等
#   内部投诉: 商品投诉 / 服务投诉 / 微信投诉 / 未标注 / 其它
# 用于分析时独立核算、独立展示、独立出报表
_EXTERNAL_COMPLAINT_KEYWORDS = (
    "工信部", "12315", "公共服务平台", "消费者协会", "消协",
    "监督局", "市场监管", "市监", "315平台", "12345", "政务平台",
)
def _complaint_source_type_case(col=ServiceTicket.service_item):
    """返回 CASE WHEN 表达式: '外部投诉' / '内部投诉'"""
    si = func.coalesce(col, literal(""))
    whens = [si.ilike(f"%{k}%") for k in _EXTERNAL_COMPLAINT_KEYWORDS]
    return case(
        (or_(*whens), "外部投诉"),
        else_="内部投诉",
    ).label("complaint_source_type")


# ============================================================
# 模块: 工单有效完工时间推算 (effective_finish_time)
# ============================================================
# 背景: ticket.finish_time 89% 被批量覆盖/同步填充(详见 docs/数据时间审核报告.md L67-L70)
#   - 50% finish > register+12h (BMS 03:15 批量覆盖)
#   - 23% finish == register (导入时同步填充)
#   - 17% 更早于 register (同瞬填充)
#   - 仅 10.7% 是真实完成时间 (status=已完成, handler 非空, finish 落在 register..register+12h)
#
# 因此在统计"今日完工单量 / 人员绩效结单量"时,必须走以下推断链:
#   1) 先过白名单,真实完成 → 直接用 finish_time
#   2) 否则若 handle_duration 存在 → effective_finish = register_time + handle_duration 秒
#   3) 否则若该 handler 在近 30 天有真实平均耗时 → effective_finish = register_time + avg(handler)
#   4) 否则 → effective_finish = register_time + 全局平均(近 30 天真实完成样本)
#
# 最终只算 status == '已完成' 的工单 (status != 已完成意味着仍在途,不能视为"完工")。
# ============================================================

def _ticket_effective_finish_time_where(db: Session, start_time: datetime, end_time: datetime,
                                        lookback_days: int = 30) -> tuple[int, int]:
    """返回 (status=已完成 且 推算后 effective_finish 落在 [start_time,end_time] 里的工单数,
               其中「真实完工样本」数量(用于前端口径说明))

    用两层查询:
      第一层 SQL: 取近 lookback_days 每个 handler(+全局) 的平均 handle_duration(仅 真实完成样本参与, 避免平均被批量覆盖污染)
      第二层: 取 status='已完成' 且 register_time < end_time + 12h 的工单, Python 侧算 effective_finish 再计数。
      这比单条巨型 SQL 可读性高得多,性能也足够(量级 <= 30 天 ≈ 9k 行)。
    """
    SYS_USERS = {"sys", "system", "SYS", "System", "客户", None}

    # 统一为带 UTC tzinfo 的 aware datetime, 避免 naive <-> aware 比较 TypeError
    import datetime as __dt
    def _as_utc_aware(dt: datetime) -> datetime:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=__dt.timezone.utc)
        return dt.astimezone(__dt.timezone.utc)
    _s = _as_utc_aware(start_time)
    _e = _as_utc_aware(end_time)

    # ── Step 1: 近 30 天 真实完成样本, 按 handler 聚合平均处理时长 ──
    hist_since = _s - timedelta(days=lookback_days)
    # 真实完成样本 = status=已完成 AND handler 非空非系统 AND handler 字面像人名 AND finish 在 register..register+12h
    real_done_cond = and_(
        ServiceTicket.status == "已完成",
        ServiceTicket.handler.isnot(None),
        ~ServiceTicket.handler.in_(list(SYS_USERS) + ["客户"]),
        ServiceTicket.finish_time.isnot(None),
        ServiceTicket.register_time.isnot(None),
        ServiceTicket.finish_time >= ServiceTicket.register_time,
        ServiceTicket.finish_time <= func.timeadd('hour', 12, ServiceTicket.register_time) if False
        else ServiceTicket.finish_time <= ServiceTicket.register_time + text("INTERVAL '12 hours'"),
    )
    # a) 按 handler 平均 (只对有真实完成样本的 handler 建索引)
    handler_avg_rows = db.execute(
        select(
            ServiceTicket.handler,
            func.avg(
                case(
                    (ServiceTicket.handle_duration > 0, ServiceTicket.handle_duration),
                    else_=func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time),
                )
            ).label("avg_dur_s"),
            func.count().label("n"),
        )
        .where(real_done_cond, ServiceTicket.register_time >= hist_since)
        .group_by(ServiceTicket.handler)
    ).all()
    handler_avg: dict[str, float] = {}
    weighted_total, weighted_count = 0.0, 0
    for r in handler_avg_rows:
        dur = float(r._mapping["avg_dur_s"] or 0.0)
        n = int(r._mapping["n"] or 0)
        if n <= 0 or dur <= 0:
            continue
        handler_avg[str(r.handler).strip()] = dur
        weighted_total += dur * n
        weighted_count += n
    global_avg = (weighted_total / weighted_count) if weighted_count > 0 else (60.0 * 28)  # fallback = 28 分钟

    # ── Step 2: 拉 status='已完成' 且 register_time 落在 [hist_since, end_time+12h] 范围内的工单
    #            ( register > end_time 绝对不可能今日完工, 提前过滤) ──
    rows = db.execute(
        select(
            ServiceTicket.register_time,
            ServiceTicket.finish_time,
            ServiceTicket.handle_duration,
            ServiceTicket.handler,
            ServiceTicket.status,
        )
        .where(
            ServiceTicket.status == "已完成",
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= hist_since,
            ServiceTicket.register_time <= _e + timedelta(hours=13),
        )
    ).all()

    done_cnt = 0
    real_done_cnt = 0
    for r in rows:
        reg, fin, hd, hdlr, st = (r.register_time, r.finish_time, r.handle_duration, r.handler, r.status)
        if not reg:
            continue
        # 把 reg/fin 都转成 UTC aware, 保证加 timedelta 后仍能和 _s/_e 比较
        reg_a = _as_utc_aware(reg)
        fin_a = _as_utc_aware(fin) if fin else None
        # 判定是否真实完成样本 (和上面 real_done_cond 保持一致)
        is_real = bool(
            fin_a is not None
            and hdlr is not None
            and hdlr not in SYS_USERS
            and fin_a >= reg_a
            and fin_a <= reg_a + timedelta(hours=12)
        )
        if is_real:
            eff = fin_a
        else:
            # 2) handle_duration > 0 直接用
            if isinstance(hd, (int, float)) and hd > 0:
                eff = reg_a + timedelta(seconds=float(hd))
            else:
                # 3) handler 近 30 天真实平均
                avg = handler_avg.get(str(hdlr).strip()) if hdlr else None
                if avg and avg > 0:
                    eff = reg_a + timedelta(seconds=avg)
                else:
                    # 4) 全局 fallback
                    eff = reg_a + timedelta(seconds=global_avg)
        if _s <= eff <= _e:
            done_cnt += 1
            if is_real:
                real_done_cnt += 1

    return done_cnt, real_done_cnt


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
    pt_type = func.coalesce(ptype, literal(""))
    pc_cat = func.coalesce(pcat, literal(""))
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


def _strip_qw_prefix(x: str | None) -> str | None:
    """终极兜底: Python端去掉"企微-"前缀, 避免维表漏登记导致同一人分两桶。仅作为 display_name 兜底用"""
    if not x:
        return x
    if isinstance(x, str) and x.startswith("企微-"):
        return x[len("企微-"):]
    return x


def operator_ticket_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员工单处理统计(处理人) — 姓名归一: PersonAlias.alias = handler → Person.display_name"""
    start, end = _date_range(start, end)
    op_prs = _operator_to_person_cte()
    rows = db.execute(
        select(
            func.coalesce(op_prs.c.display_name, ServiceTicket.handler).label("operator"),
            func.count().label("ticket_count"),
            func.coalesce(func.sum(ServiceTicket.refund_amount), 0).label("refund_amount"),
            func.coalesce(func.avg(ServiceTicket.handle_duration), 0).label("avg_duration"),
        )
        .select_from(ServiceTicket)
        .outerjoin(op_prs, op_prs.c.operator == ServiceTicket.handler)
        .where(ServiceTicket.handler.isnot(None), ServiceTicket.register_time.between(start, end))
        .group_by(text("1"))
        .order_by(func.count().desc())
    ).all()
    return [
        {
            "operator": _strip_qw_prefix(r[0]),
            "ticket_count": r[1],
            "refund_amount": float(r[2] or 0),
            "avg_handle_duration": round(float(r[3] or 0), 2),
        }
        for r in rows
    ]


def operator_audit_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员审核统计(审核人) 【统一口径: 路线A 退费单-线上直退】
    姓名归一: PersonAlias.alias = auditor → Person.display_name
    """
    start, end = _date_range(start, end)
    op_prs = _operator_to_person_cte()
    rows = db.execute(
        select(
            func.coalesce(op_prs.c.display_name, Refund.auditor).label("operator"),
            func.count().label("audit_count"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("audit_amount"),
        )
        .select_from(Refund)
        .outerjoin(op_prs, op_prs.c.operator == Refund.auditor)
        .where(Refund.auditor.isnot(None),
               Refund.created_at_raw.between(start, end),
               *_route_a_online_refund_filter())
        .group_by(text("1"))
        .order_by(func.sum(Refund.refund_amount).desc())
    ).all()
    return [
        {"operator": _strip_qw_prefix(r[0]), "audit_count": r[1], "audit_amount": float(r[2] or 0)}
        for r in rows
    ]


def operator_refund_stats(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """人员退费操作统计(操作人,排除 sys/system) 【统一口径: 路线A 退费单-线上直退】
    姓名归一: PersonAlias.alias = operator → Person.display_name;
    sys/system/SYS 等系统变体由于已在 alias 里链接到"系统自动", 所以额外再过滤掉 display_name='系统自动' 确保一致
    """
    start, end = _date_range(start, end)
    op_prs = _operator_to_person_cte()
    rows = db.execute(
        select(
            func.coalesce(op_prs.c.display_name, Refund.operator).label("operator"),
            func.count().label("op_count"),
            func.coalesce(func.sum(Refund.refund_amount), 0).label("refund_amount"),
        )
        .select_from(Refund)
        .outerjoin(op_prs, op_prs.c.operator == Refund.operator)
        .where(
            Refund.operator.isnot(None),
            Refund.operator.notin_(["sys", "system"]),
            Refund.created_at_raw.between(start, end),
            *_route_a_online_refund_filter(),
        )
        .group_by(text("1"))
        .having(func.coalesce(op_prs.c.display_name, Refund.operator) != "系统自动")
        .order_by(func.sum(Refund.refund_amount).desc())
    ).all()
    return [
        {"operator": _strip_qw_prefix(r[0]), "op_count": r[1], "refund_amount": float(r[2] or 0)}
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

    # ---- 投诉统计 (口径 = service_tickets 投诉 + external_complaints + card_complaints 三表 UNION) ----
    # 1. 投诉工单总数 (service_tickets: _ticket_category_case()==投诉; 另叠加两张独立投诉表)
    complaint_st = db.scalar(
        select(func.count())
        .select_from(ServiceTicket)
        .where(_ticket_category_case() == "投诉")
    ) or 0
    complaint_ec = db.scalar(select(func.count()).select_from(ExternalComplaint)) or 0
    complaint_cc = db.scalar(select(func.count()).select_from(CardComplaint)) or 0
    complaint_total = complaint_st + complaint_ec + complaint_cc
    # 2. 投诉子类分布 (service_tickets 内部投诉分类 + 外部投诉合并)
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
    # 把 external_complaints 的 complaint_category 归并为 "外部投诉平台" 子类 (避免与内部投诉子类混淆,同时累加独立投诉表)
    if complaint_ec or complaint_cc:
        _name_ec = "外部投诉(监督局/315等)"
        _name_cc = "卡商群投诉"
        _existing_subs = {x["name"] for x in complaint_by_sub}
        if complaint_ec:
            complaint_by_sub.append({"name": _name_ec, "value": int(complaint_ec)})
        if complaint_cc:
            complaint_by_sub.append({"name": _name_cc, "value": int(complaint_cc)})
        complaint_by_sub.sort(key=lambda x: -x["value"])
    # 3. 投诉状态分布 (待处理/已完成/终止) — 仅 service_tickets (两张独立投诉表状态口径不同暂不合并展示,避免饼图失真)
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
    # 补齐外部投诉/卡商投诉的状态缺失时,按 result 字段粗略估算 已完成(有效值)/待处理(未标注)
    if complaint_ec:
        ec_done = db.scalar(
            select(func.count()).select_from(ExternalComplaint)
            .where(func.coalesce(ExternalComplaint.result, "").ilike("%有效%"))
        ) or 0
        complaint_by_status.append({"name": "外部投诉-已处理", "value": int(ec_done)})
        if complaint_ec - ec_done > 0:
            complaint_by_status.append({"name": "外部投诉-待跟进", "value": int(complaint_ec - ec_done)})
    if complaint_cc:
        cc_done = db.scalar(
            select(func.count()).select_from(CardComplaint)
            .where(func.coalesce(CardComplaint.result, "").ilike("%完成%"))
        ) or 0
        complaint_by_status.append({"name": "卡商投诉-已处理", "value": int(cc_done)})
        if complaint_cc - cc_done > 0:
            complaint_by_status.append({"name": "卡商投诉-待跟进", "value": int(complaint_cc - cc_done)})
    # 4. 工单总数(非投诉类) — 保持原 service_tickets 全量,仅用于投诉占比分母
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


def _operator_to_person_cte(department: str | None = None,
                            group_id: int | None = None,
                            specialty_id: int | None = None):
    """返回 operator → person_id, display_name, department, group_name, specialty, position, qw_user_id 的子查询 CTE

    通过 PersonAlias.alias (归一化匹配) → Person。找不到则 operator 作为 display_name。
    qw_user_id: 非空表示该别名所对应的 Person 已绑定企微 (有企微账号)
    department/group_id/specialty_id: 可选筛选条件
    group_name: 通过 PersonGroupMembership → Group 关联获取当前有效分组名称 (不依赖镜像字段)
    """
    person_conds = []
    if department:
        person_conds.append(Person.department == department)

    return (
        select(
            PersonAlias.alias.label("operator"),
            Person.id.label("person_id"),
            func.coalesce(Person.display_name, PersonAlias.alias).label("display_name"),
            Person.department.label("department"),
            func.coalesce(Group.name, Person.group_name).label("group_name"),
            Person.specialty.label("specialty"),
            Person.position.label("position"),
            Person.qw_user_id.label("qw_user_id"),
        )
        .select_from(PersonAlias)
        .outerjoin(Person, PersonAlias.person_id == Person.id)
        .outerjoin(
            PersonGroupMembership,
            and_(
                PersonGroupMembership.person_id == Person.id,
                PersonGroupMembership.is_current == True,
            ),
        )
        .outerjoin(Group, PersonGroupMembership.group_id == Group.id)
        .where(and_(*person_conds) if person_conds else True)
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


def _resolve_windows(
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[_dt.datetime, _dt.datetime, _dt.datetime, _dt.datetime, bool, tuple[str, str] | None]:
    """统一时间窗口解析: 优先 start/end 范围, 否则退回 date 单日.

    Returns:
        (cur_start, cur_end, prev_start, prev_end, is_realtime, (display_start_iso, display_end_iso) or None)
        - prev_* 是用于同比/环比的前一个完整周期:
          * 单日 → 前一天平移 (和 _today_windows 完全一致)
          * 周/月/季度 → 向前平移 range_length 天 (保证周期长度一致)
        - is_realtime: 是否实时(今天未结束)窗口
        - display_*: Asia/Shanghai 时区 ISO 字符串 (前端友好显示/水印), 单日模式下为 None 让 today_* 自行沿用 date 字段
    """
    _utc = _dt.timezone.utc

    def _sh_to_naive_utc(dt_sh: _dt.datetime) -> _dt.datetime:
        return dt_sh.astimezone(_utc).replace(tzinfo=None)

    # ── 优先 start/end 模式 ──
    if start and end:
        s_d = _parse_date(start)
        e_d = _parse_date(end)
        if s_d is None or e_d is None:
            # 解析失败, 退回今日实时
            return _today_windows(None) + (True, None)
        if s_d > e_d:
            s_d, e_d = e_d, s_d
        now_sh = _dt.datetime.now(TZ)
        today_d = now_sh.date()
        span_days = (e_d - s_d).days + 1

        # 周期结束日 = end 当天; 若 end == 今天 → 实时窗口截止 now; 否则整天 23:59:59
        if e_d == today_d:
            cur_end_sh = now_sh
            is_realtime = True
        else:
            cur_end_sh = _dt.datetime.combine(e_d, _dt.time(23, 59, 59, 999999), tzinfo=TZ)
            is_realtime = False
        cur_start_sh = _dt.datetime.combine(s_d, _dt.time(0, 0, 0), tzinfo=TZ)

        # 上一个周期: 向前平移 span_days 天 (保证两个周期长度严格一致)
        prev_start_sh = cur_start_sh - timedelta(days=span_days)
        prev_end_sh = cur_end_sh - timedelta(days=span_days)

        display = (cur_start_sh.isoformat(), cur_end_sh.isoformat())
        return (
            _sh_to_naive_utc(cur_start_sh),
            _sh_to_naive_utc(cur_end_sh),
            _sh_to_naive_utc(prev_start_sh),
            _sh_to_naive_utc(prev_end_sh),
            is_realtime,
            display,
        )

    # ── 退回 date 单日模式 (和原 _today_windows 完全一致) ──
    d = _parse_date(date)
    now = _dt.datetime.now(TZ)
    is_today = d is None or d == now.date()
    if is_today:
        t_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        t_end = now
        y_start = t_start - timedelta(days=1)
        y_end = t_end - timedelta(days=1)
    else:
        t_start = _dt.datetime.combine(d, _dt.time(0, 0, 0), tzinfo=TZ)
        t_end = t_start + timedelta(days=1) - timedelta(microseconds=1)
        y_start = t_start - timedelta(days=1)
        y_end = t_end - timedelta(days=1)
    return (
        _sh_to_naive_utc(t_start),
        _sh_to_naive_utc(t_end),
        _sh_to_naive_utc(y_start),
        _sh_to_naive_utc(y_end),
        is_today,
        None,
    )


def _today_windows(date: _dt.date | None) -> tuple[_dt.datetime, _dt.datetime, _dt.datetime, _dt.datetime]:
    """返回 (today_start, today_end, yoy_start, yoy_end) —— 向后兼容, 直接调 _resolve_windows()."""
    date_str = date.isoformat() if date else None
    ts, te, ys, ye, _, _ = _resolve_windows(date=date_str)
    return ts, te, ys, ye


def _parse_date(s: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(s) if s else None


def _pct_delta(cur, prev) -> float | None:
    """环比百分比: (cur-prev)/prev*100, prev为0返回None."""
    if prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100.0, 1)


def today_kpi(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块1: KPI卡 + 前一周期环比 【全域统一退款核算 v4】

    支持三种时间调用方式(按优先级):
      1. start+end 明确范围 (周/月/季度粒度, 多日汇总) → 上一个同长度周期做环比
      2. date='YYYY-MM-DD' 指定整天 → 昨天整天做环比
      3. date=None 实时今天 → 昨天同时段做环比
    口径严格对齐 v4 规范 (2026-08-10 用户调整):
      - 退款笔数/金额 = 四路经营合计 A+B+C+D (经营侧登记的所有退款出款)
      - 真实现金流 = E (merchant_refunds WHERE refund_status='SUCCESS' 合计, 对账基准)
      - 对账偏差 = 经营估算 − 实际现金流
    """
    ts, te, ys, ye, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    d_end_iso = None
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()

    def _between(col, s, e):
        return and_(col.isnot(None), col >= s, col <= e)

    # 1. 今日接待量 (cs_records.operation_time)
    cs_today = db.execute(select(func.count()).where(_between(CsRecord.operation_time, ts, te))).scalar() or 0
    cs_yoy = db.execute(select(func.count()).where(_between(CsRecord.operation_time, ys, ye))).scalar() or 0
    # 1b. 今日服务设备数 (cs_records.device_sn 去重, 排除 NULL/空串) — 补充口径：1台设备多次进线算1次，体现覆盖面
    cs_device_today = db.execute(select(
        func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), "")))
    ).where(_between(CsRecord.operation_time, ts, te))).scalar() or 0
    cs_device_yoy = db.execute(select(
        func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), "")))
    ).where(_between(CsRecord.operation_time, ys, ye))).scalar() or 0

    # 2/3/4. 四路经营退款+实际现金流 (通过统一聚合器一次性计算)
    routes_t = _cash_routes_total(db, start=ts, end=te)
    routes_y = _cash_routes_total(db, start=ys, end=ye)
    a_t, b_t = routes_t["route_a"], routes_t["route_b"]
    a_y, b_y = routes_y["route_a"], routes_y["route_b"]
    c_t, d_t = routes_t["route_c"], routes_t["route_d"]
    c_y, d_y = routes_y["route_c"], routes_y["route_d"]
    e_t = routes_t["actual_cashflow"]          # 实际现金流 E (今日)
    e_y = routes_y["actual_cashflow"]          # 实际现金流 E (昨日)
    op_t = routes_t["operational"]             # 经营估算 A+B+C+D (今日)
    op_y = routes_y["operational"]             # 经营估算 A+B+C+D (昨日)
    # 退款笔数/金额 = 四路经营合计
    rf_cnt_t, rf_amt_t = op_t["count"], op_t["amount"]
    rf_cnt_y, rf_amt_y = op_y["count"], op_y["amount"]
    # 真实现金流 = 实际现金流 E (merchant_refunds SUCCESS合计)
    cash_t = e_t["amount"]
    cash_y = e_y["amount"]
    merchant_today = e_t["count"]              # 实际现金流笔数
    merchant_tip = None
    today_d_iso = _dt.datetime.now(TZ).date().isoformat()
    range_end_iso = d_end_iso or d_iso
    if range_end_iso == today_d_iso and merchant_today == 0:
        merchant_tip = "商户号流水通常T+1导入，本日/本周期数据可能为空"

    # 5. 今日已完工单 (新口径:有效完工时间推算)
    #    - 背景: finish_time 89% 被批量覆盖/同步填充(详见 docs/数据时间审核报告.md L67-L70)
    #    - 推算链: 真实完成样本(白名单) → handle_duration 秒 → handler 近 30 天真实平均 → 全局真实平均
    #    - 仅统计 status='已完成' 的工单 (在途不视为完工)
    done_t, real_done_t = _ticket_effective_finish_time_where(db, ts, te)
    done_y, real_done_y = _ticket_effective_finish_time_where(db, ys, ye)

    # 6. 今日在岗人数 (cs_records distinct operator)
    staff_t = db.execute(select(func.count(func.distinct(CsRecord.operator)))
                         .where(_between(CsRecord.operation_time, ts, te),
                                CsRecord.operator.isnot(None))).scalar() or 0
    staff_y = db.execute(select(func.count(func.distinct(CsRecord.operator)))
                         .where(_between(CsRecord.operation_time, ys, ye),
                                CsRecord.operator.isnot(None))).scalar() or 0

    # 7. 今日投诉单 (统计口径 = service_tickets 投诉 + external_complaints + card_complaints 三表 UNION)
    #    - service_tickets: _ticket_category_case() == "投诉" (含 IS NULL / category含投诉 / item含关键词)
    #    - external_complaints: 315/监督局/消协等外部平台投诉 (complaint_date 落当天)
    #    - card_complaints: 卡商群投诉 (complaint_date 落当天)
    comp_t_st = db.execute(select(func.count())
                        .where(_between(ServiceTicket.register_time, ts, te),
                               _ticket_category_case() == "投诉")).scalar() or 0
    comp_t_ec = db.execute(select(func.count())
                        .where(_between(ExternalComplaint.complaint_date, ts, te))).scalar() or 0
    comp_t_cc = db.execute(select(func.count())
                        .where(_between(CardComplaint.complaint_date, ts, te))).scalar() or 0
    comp_t = comp_t_st + comp_t_ec + comp_t_cc

    comp_y_st = db.execute(select(func.count())
                        .where(_between(ServiceTicket.register_time, ys, ye),
                               _ticket_category_case() == "投诉")).scalar() or 0
    comp_y_ec = db.execute(select(func.count())
                        .where(_between(ExternalComplaint.complaint_date, ys, ye))).scalar() or 0
    comp_y_cc = db.execute(select(func.count())
                        .where(_between(CardComplaint.complaint_date, ys, ye))).scalar() or 0
    comp_y = comp_y_st + comp_y_ec + comp_y_cc

    def _k(v, yv):
        return {"value": v, "yoy_value": yv, "yoy_pct": _pct_delta(v, yv)}

    # 返回的window字段用上海时区展示(前端显示友好); ts/te仍用于DB比较
    _utc = _dt.timezone.utc
    def _sh_iso(naive_utc: _dt.datetime) -> str:
        return naive_utc.replace(tzinfo=_utc).astimezone(TZ).isoformat()

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "is_realtime": is_realtime,
        "window": {
            "today_start": _sh_iso(ts), "today_end": _sh_iso(te),
            "yoy_start": _sh_iso(ys), "yoy_end": _sh_iso(ye),
        },
        "reception":      {**_k(int(cs_today), int(cs_yoy)), "device_count": int(cs_device_today), "device_count_yoy": int(cs_device_yoy)},
        "refund_count":   _k(rf_cnt_t, rf_cnt_y),
        "refund_amount":  _k(round(rf_amt_t, 2), round(rf_amt_y, 2)),
        "cash_out":       _k(round(cash_t, 2), round(cash_y, 2)),      # 经营估算 A+B+C+D
        "actual_cashflow":_k(round(e_t["amount"], 2), round(e_y["amount"], 2)), # (新) 实际现金流E
        "ticket_done":    {**_k(int(done_t), int(done_y)), "real_done_today": int(real_done_t), "real_done_yoy": int(real_done_y)},
        "staff_on_duty":  _k(int(staff_t), int(staff_y)),
        "complaint":      _k(int(comp_t), int(comp_y)),
        "merchant_tip":   merchant_tip,
        # ── 新增: 今日四路线拆分 + 实际现金流E + 对账偏差 (前端tooltip/明细卡) ──
        "cash_routes_today": {
            "route_a": a_t,  # A.退费单线上直退
            "route_b": b_t,  # B.钱包用户取现
            "route_c": c_t,  # C.闪猫退款 (独立表 shanmao_refunds, status=已退款 计入)
            "route_d": d_t,  # D.其他退款 (独立表占位,暂0)
            "operational":     routes_t["operational"],     # 经营估算 A+B+C+D
            "actual_cashflow": routes_t["actual_cashflow"], # 实际现金流 E (商户号SUCCESS合计)
            "financial":       routes_t["financial"],       # 向后兼容: 财务实际 = E
            "diff":            routes_t["diff"],            # 对账偏差 = 经营 − 实际
        },
        # ── 口径注释 (前端标题tooltip) ──
        "_spec": {
            "refund_count":     "退款笔数 = 四路经营合计 A线上直退 + B用户取现 + C闪猫退款 + D其他退款",
            "refund_amount":    "退款金额 = 四路经营合计 A+B+C+D (经营侧登记的所有退款出款)",
            "cash_out":         "真实现金流(对账基准) = merchant_refunds WHERE refund_status='SUCCESS' 合计, 不分ORIGINAL/BALANCE桶",
            "actual_cashflow":  "实际现金流(对账基准) = merchant_refunds WHERE refund_status='SUCCESS' 合计, 不分ORIGINAL/BALANCE桶",
            "ticket_done":      "有效完工时间推算: 10.7% 真实完成(白名单:status=已完成且finish在register+12h内且handler非空非SYS) → handle_duration秒 → 该handler近30天真实平均耗时 → 全局真实平均。仅status=已完成视为完工, 避免finish_time被BMS 03:15批量覆盖导致的虚高。",
        },
    }


def today_hourly_trend(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块2: 时间节奏 (小时/日/周/月 按跨度自动选择粒度) v4

    粒度规则:
      - 单日 → 按小时聚合 (00:00~23:00, 对应今日实时到当前小时)
      - 多日 → 按天聚合 (date_trunc day, 标签为 MM-DD)
    按三组分类展示:
      【工作量组】左Y柱:
        - 会话数(次) / 接待数(个): 独立柱
      【款项组】左Y柱(笔数堆叠) + 右Y面积(金额堆叠):
        - 退款笔数: 四路堆叠柱 → A(线上直退) + B(钱包提现) + C(闪猫退款) + D(其他退款) = 经营合计
        - 退款金额: 四路堆叠面积 → A+B+C+D = 经营合计; 独立虚线 = E(商户号实际现金流)
      【投诉组】左Y柱:
        - 投诉量: 独立柱
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)

    # 跨度判断:
    #   1. 当传入 date(单日模式) → 永远按小时聚合 (不管 UTC 跨日界 bug, 直接固定 hour 粒度)
    #   2. 当传入 start+end(范围模式) → 转北京时间后算日历跨度, >1 天按 day, =1 天按 hour
    if date and not (start or end):
        span_days = 1
        grain = "hour"
    else:
        # 转北京时间后计算日历日跨度 (避免 UTC 日界导致单日被误算成 2 天)
        ts_sh = _dt.datetime.fromtimestamp(ts.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ).date()
        te_sh = _dt.datetime.fromtimestamp(te.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ).date()
        span_days = (te_sh - ts_sh).days + 1
        grain = "day" if span_days > 1 else "hour"
    # ── 时间截断函数 ──
    def _time_trunc(col):
        if grain == "hour":
            return func.date_trunc("hour", func.timezone("Asia/Shanghai", col)).label("trunc_t")
        return func.date_trunc("day", func.timezone("Asia/Shanghai", col)).label("trunc_t")
    # 2. 小时/日 标签与横轴: 填充连续槽位
    now_sh = _dt.datetime.now(TZ)
    if grain == "hour":
        # 小时轴: 0~23 小时
        # 注意: _time_trunc(col) = date_trunc('hour', timezone('Asia/Shanghai', col))
        #  -> 返回的是 naive datetime, 已经是北京时间本地时刻 (例如 08:00 就是北京早8点)
        #  -> 直接取 .hour 即可，不要再做 UTC→北京时间的二次换算！
        slots_hours = list(range(24))
        is_today_like = is_realtime
        max_slot = now_sh.hour if is_today_like else 23
        def _slot_idx(trunc_t_local: _dt.datetime) -> int | None:
            # trunc_t_local = naive 北京datetime (例如 2026-08-01 08:00:00)，.hour 就是北京时间小时
            return int(trunc_t_local.hour)
        def _slot_label(slot: int) -> str:
            return f"{slot:02d}:00"
        x_label = "小时节奏 (北京时间)"
    else:
        # 日轴: 从 start 到 end 连续日期 (按 Asia/Shanghai 日历日)
        ts_sh = _dt.datetime.fromtimestamp(ts.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ).date()
        te_sh = _dt.datetime.fromtimestamp(te.replace(tzinfo=_dt.timezone.utc).timestamp(), tz=TZ).date()
        slots_days: list[_dt.date] = []
        cur = ts_sh
        while cur <= te_sh:
            slots_days.append(cur)
            cur += timedelta(days=1)
        max_slot = len(slots_days) - 1
        today_date = now_sh.date()
        def _slot_idx(trunc_t_local: _dt.datetime) -> int | None:
            # trunc_t_local = date_trunc('day', timezone('Asia/Shanghai', col)) → 已naive北京date
            d_local = trunc_t_local.date() if hasattr(trunc_t_local, 'date') else trunc_t_local
            if isinstance(d_local, _dt.datetime):
                d_local = d_local.date()
            if d_local in slots_days:
                return slots_days.index(d_local)
            return None
        def _slot_label(slot: int) -> str:
            return slots_days[slot].strftime("%m-%d")
        x_label = "日期 (跨度 {} 天)".format(len(slots_days))
    n_slots = len(slots_days) if grain=="day" else 24
    cs_rows = db.execute(
        select(_time_trunc(CsRecord.operation_time),
               func.count().label("cnt"),
               func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("dev_cnt"))
        .where(CsRecord.operation_time.isnot(None),
               CsRecord.operation_time >= ts, CsRecord.operation_time <= te)
        .group_by("trunc_t")
    ).all()
    cs_map: dict[int, int] = {}
    cs_dev_map: dict[int, int] = {}
    for r in cs_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        cs_map[idx] = int(r.cnt or 0)
        cs_dev_map[idx] = int(r.dev_cnt or 0)

    # --- 路线A: 退费单线上直退(按粒度 笔数+金额) ---
    rf_rows = db.execute(
        select(_time_trunc(Refund.created_at_raw),
               func.count().label("cnt"),
               func.coalesce(func.sum(Refund.refund_amount), 0).label("amt"))
        .where(Refund.created_at_raw.isnot(None),
               Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
               *_route_a_online_refund_filter())
        .group_by("trunc_t")
    ).all()
    a_cnt_map: dict[int, int] = {}
    a_amt_map: dict[int, float] = {}
    for r in rf_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        a_cnt_map[idx] = int(r.cnt or 0)
        a_amt_map[idx] = float(r.amt or 0)

    # --- 路线B: 钱包提现(按粒度 笔数+金额) ---
    wb_rows = db.execute(
        select(_time_trunc(WalletWithdrawal.op_time),
               func.count().label("cnt"),
               func.coalesce(func.sum(WalletWithdrawal.op_amount), 0).label("amt"))
        .where(WalletWithdrawal.op_time.isnot(None),
               WalletWithdrawal.op_time >= ts, WalletWithdrawal.op_time <= te,
               *_route_b_withdraw_filter())
        .group_by("trunc_t")
    ).all()
    b_cnt_map: dict[int, int] = {}
    b_amt_map: dict[int, float] = {}
    for r in wb_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        b_cnt_map[idx] = int(r.cnt or 0)
        b_amt_map[idx] = float(r.amt or 0)

    # --- 路线C: 闪猫退款(按粒度 笔数+金额) ---
    c_cnt_map, c_amt_map = _route_c_shanmao_trend(db, ts, te, grain, _slot_idx)

    # --- 路线D: 其他退款(按粒度 笔数+金额) ---
    d_cnt_map, d_amt_map = _route_d_other_trend(db, ts, te, grain, _slot_idx)

    # --- 路线E: 商户号实际现金流(按粒度 笔数+金额) ---
    e_rows = db.execute(
        select(_time_trunc(MerchantRefund.trade_time),
               func.count().label("cnt"),
               func.coalesce(func.sum(MerchantRefund.refund_amount), 0).label("amt"))
        .where(MerchantRefund.trade_time.isnot(None),
               MerchantRefund.trade_time >= ts, MerchantRefund.trade_time <= te,
               *_route_e_merchant_cashflow_filter())
        .group_by("trunc_t")
    ).all()
    e_cnt_map: dict[int, int] = {}
    e_amt_map: dict[int, float] = {}
    for r in e_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        e_cnt_map[idx] = int(r.cnt or 0)
        e_amt_map[idx] = float(r.amt or 0)

    # 投诉: 按粒度聚合 按 service_tickets.register_time, 三表 UNION 计数
    cp_rows = db.execute(
        select(_time_trunc(ServiceTicket.register_time), func.count().label("cnt"))
        .where(ServiceTicket.register_time.isnot(None),
               ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te,
               _ticket_category_case() == "投诉")
        .group_by("trunc_t")
    ).all()
    cp_map: dict[int, int] = {}
    for r in cp_rows:
        tt = r._mapping["trunc_t"]
        if tt is None:
            continue
        idx = _slot_idx(tt)
        if idx is None:
            continue
        cp_map[idx] = int(r.cnt or 0)
    # 叠加 ExternalComplaint 小时/日节奏
    ec_rows = db.execute(
        select(_time_trunc(ExternalComplaint.complaint_date), func.count().label("cnt"))
        .where(ExternalComplaint.complaint_date.isnot(None),
               ExternalComplaint.complaint_date >= ts, ExternalComplaint.complaint_date <= te)
        .group_by("trunc_t")
    ).all()
    for r in ec_rows:
        tt = r._mapping["trunc_t"]
        if tt is None: continue
        idx = _slot_idx(tt)
        if idx is not None:
            cp_map[idx] = cp_map.get(idx, 0) + int(r.cnt or 0)
    # 叠加 CardComplaint 小时/日节奏
    cc_rows = db.execute(
        select(_time_trunc(CardComplaint.complaint_date), func.count().label("cnt"))
        .where(CardComplaint.complaint_date.isnot(None),
               CardComplaint.complaint_date >= ts, CardComplaint.complaint_date <= te)
        .group_by("trunc_t")
    ).all()
    for r in cc_rows:
        tt = r._mapping["trunc_t"]
        if tt is None: continue
        idx = _slot_idx(tt)
        if idx is not None:
            cp_map[idx] = cp_map.get(idx, 0) + int(r.cnt or 0)

    # ── 填充到槽位: 单日填当前小时之前, 多日填全部(最后一天可能实时) ──
    slots = list(range(n_slots))
    def _fill_list(*maps, kind='count'):
        """将多个map按slot合并成list, 返回各路线list + 合计list"""
        n_maps = len(maps)
        lists = [[] for _ in range(n_maps)]
        total_list = []
        for i in slots:
            if i > max_slot:
                for L in lists: L.append(None)
                total_list.append(None)
            else:
                total = 0
                for j, m in enumerate(maps):
                    v = m.get(i, 0)
                    lists[j].append(v if kind == 'count' else v)
                    total += v
                total_list.append(total if kind == 'count' else round(total, 2))
        return lists + [total_list]

    # 退款笔数: A/B/C/D 四路 + 合计
    cnt_lists = _fill_list(a_cnt_map, b_cnt_map, c_cnt_map, d_cnt_map, kind='count')
    r_cnt_a, r_cnt_b, r_cnt_c, r_cnt_d, r_cnt_total = cnt_lists
    # 退款金额: A/B/C/D 四路 + 合计(经营估算)
    amt_lists = _fill_list(a_amt_map, b_amt_map, c_amt_map, d_amt_map, kind='amount')
    r_amt_a, r_amt_b, r_amt_c, r_amt_d, r_amt_total = amt_lists
    # 实际现金流E (笔数+金额)
    e_cnt_list = [e_cnt_map.get(i, 0) if i <= max_slot else None for i in slots]
    e_amt_list = [round(e_amt_map.get(i, 0), 2) if i <= max_slot else None for i in slots]

    # 旧兼容字段 = 四路合计
    refund_count = r_cnt_total
    refund_amount = r_amt_total

    # 展示用日期 (单日 → date; 范围 → start date, 另附 date_end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso: str | None = disp_range[1][:10]
    else:
        d_iso = (now_sh.date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None

    slot_labels = [_slot_label(i) for i in slots]
    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "grain": grain,
        "x_label": x_label,
        "hours": slots if grain == "hour" else None,   # 旧兼容
        "hour_labels": slot_labels,                     # 前端统一用 hour_labels
        "slot_labels": slot_labels,
        # === 工作量组 ===
        "reception":          [cs_map.get(i, 0)         if i <= max_slot else None for i in slots],
        "reception_devices":  [cs_dev_map.get(i, 0)     if i <= max_slot else None for i in slots],
        # === 款项组 - 退款笔数: 四路拆分 + 合计 ===
        "refund_count":       refund_count,      # 旧兼容 = 四路合计
        "refund_count_a":     r_cnt_a,           # 路线A 退费单线上直退
        "refund_count_b":     r_cnt_b,           # 路线B 钱包用户取现
        "refund_count_c":     r_cnt_c,           # 路线C 闪猫退款
        "refund_count_d":     r_cnt_d,           # 路线D 其他退款
        "refund_count_direct":  r_cnt_a,         # 向后兼容: direct = A
        "refund_count_withdraw": r_cnt_b,        # 向后兼容: withdraw = B
        "refund_count_total":  r_cnt_total,      # 四路合计 A+B+C+D
        # === 款项组 - 退款金额: 四路拆分 + 合计 + 实际现金流E ===
        "refund_amount":      refund_amount,     # 旧兼容 = 四路合计
        "refund_amount_a":    r_amt_a,           # 路线A 金额
        "refund_amount_b":    r_amt_b,           # 路线B 金额
        "refund_amount_c":    r_amt_c,           # 路线C 金额
        "refund_amount_d":    r_amt_d,           # 路线D 金额
        "refund_amount_e":    e_amt_list,        # 路线E 实际现金流金额
        "refund_amount_direct":  r_amt_a,        # 向后兼容
        "refund_amount_withdraw": r_amt_b,       # 向后兼容
        "refund_amount_total":  r_amt_total,     # 四路经营合计 A+B+C+D
        "cashflow_e_count":   e_cnt_list,        # 实际现金流E 笔数
        "cashflow_e_amount":  e_amt_list,        # 实际现金流E 金额
        # === 投诉组 ===
        "complaint":          [cp_map.get(i, 0)         if i <= max_slot else None for i in slots],
        "_spec": {
            "refund_count_a":  "路线A: 退费单 refund_method='线上' (线上直退)",
            "refund_count_b":  "路线B: 钱包提现 op_type='用户提现'",
            "refund_count_c":  "路线C: 闪猫退款 (shanmao_refunds status=已退款)",
            "refund_count_d":  "路线D: 其他退款 (独立表占位)",
            "refund_count_total": "四路经营合计 A+B+C+D",
            "refund_amount_a": "路线A: 退费单线上直退金额",
            "refund_amount_b": "路线B: 钱包用户取现金额",
            "refund_amount_c": "路线C: 闪猫退款金额",
            "refund_amount_d": "路线D: 其他退款金额",
            "refund_amount_e": "路线E: 商户号实际现金流 (merchant_refunds SUCCESS)",
            "refund_amount_total": "四路经营合计金额 A+B+C+D",
            "reception":     "会话数(次) = cs_record 计数；接待数(个) = 去重设备SN计数(去NULL/空串)",
            "groups":        "工作量: 会话/接待 · 款项: 退款笔数(四路堆叠)/退款金额(四路堆叠面积+实际现金流E线) · 投诉: 投诉量",
            "grain":         f"时间粒度: {grain} (跨度{span_days}天 → 自动选择粒度)",
        },
    }


def today_reception_breakdown(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块3: 今日接待分项 = 分类饼图 + Top10客服柱图 + 投诉子类饼图 + 投诉根因柱图.
    分类: 6 类 (投诉/退款/套餐问题/咨询/综合问题/其他问题)
    投诉相关基于 service_tickets (service_category 兜底=投诉), 根因通过 device_sn ±24h 关 cs_record 判断
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None
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
    #     2026-08-02 加设备SN去重计数: 设备接待台数, 体现覆盖面, 1台设备多次进线算1台
    op_person = _operator_to_person_cte()
    top_rows = db.execute(
        select(
            CsRecord.operator.label("raw_op"),
            op_person.c.display_name.label("display_name"),
            op_person.c.department.label("department"),
            op_person.c.group_name.label("group_name"),
            func.count().label("cnt"),
            func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("dcnt"),
        )
        .outerjoin(op_person, CsRecord.operator == op_person.c.operator)
        .where(between, CsRecord.operator.isnot(None))
        .group_by("raw_op", op_person.c.display_name, op_person.c.department, op_person.c.group_name)
        .order_by(func.count().desc())
        .limit(10)
    ).all()
    top_staff = []
    for r in top_rows:
        raw_name = r.display_name or r.raw_op or "未知"
        top_staff.append({
            "name": _strip_qw_prefix(raw_name) or raw_name or "未知",
            "department": r.department or "未设置",
            "group": r.group_name or "未分组",
            "count": int(r.cnt or 0),
            "device_count": int(r.dcnt or 0),
        })

    # ---------- 新增: 今日投诉统计 (口径=三表 UNION: service_tickets + external_complaints + card_complaints) ----------
    # 左2: 投诉子类饼图 (从 service_item 归一化, 仅统计今日投诉工单)
    # 同时拿到每个子类的来源类型(内部/外部) 方便前端分组着色与拆分展示
    comp_sub_rows = db.execute(
        select(_complaint_subitem_case(),
               _complaint_source_type_case(),
               func.count().label("cnt"))
        .where(tk_between, _ticket_category_case() == "投诉")
        .group_by("complaint_sub", "complaint_source_type")
        .order_by(text("cnt DESC"))
    ).all()
    complaint_subs = [
        {"name": r[0] or "未标注",
         "source_type": r[1] or "内部投诉",
         "value": int(r[2] or 0)}
        for r in comp_sub_rows
    ]
    # 叠加 external_complaints 今日 (按 complaint_category 聚合,未分类则归入 "外部投诉(监督局/315等)")
    ec_between = and_(ExternalComplaint.complaint_date.isnot(None),
                      ExternalComplaint.complaint_date >= ts, ExternalComplaint.complaint_date <= te)
    ec_rows = db.execute(
        select(func.coalesce(ExternalComplaint.complaint_category, "外部投诉(监督局/315等)"),
               func.count().label("cnt"))
        .where(ec_between)
        .group_by(func.coalesce(ExternalComplaint.complaint_category, "外部投诉(监督局/315等)"))
        .order_by(text("cnt DESC"))
    ).all()
    for r in ec_rows:
        complaint_subs.append({
            "name": str(r[0] or "外部投诉(监督局/315等)"),
            "source_type": "外部投诉",
            "value": int(r[1] or 0),
        })
    # 叠加 card_complaints 今日 (按 problem_category 聚合, 未分类归入 "卡商群投诉")
    cc_between = and_(CardComplaint.complaint_date.isnot(None),
                      CardComplaint.complaint_date >= ts, CardComplaint.complaint_date <= te)
    cc_rows = db.execute(
        select(func.coalesce(CardComplaint.problem_category, "卡商群投诉"),
               func.count().label("cnt"))
        .where(cc_between)
        .group_by(func.coalesce(CardComplaint.problem_category, "卡商群投诉"))
        .order_by(text("cnt DESC"))
    ).all()
    for r in cc_rows:
        complaint_subs.append({
            "name": str(r[0] or "卡商群投诉"),
            "source_type": "内部投诉",
            "value": int(r[1] or 0),
        })
    if complaint_subs:
        complaint_subs.sort(key=lambda x: -x["value"])
    # 今日投诉总数 (便于前端展示 "今日共XX条投诉") = 三张独立投诉表今日之和
    st_cnt = int(sum((x["value"] for x in complaint_subs if x["name"] not in ("外部投诉(监督局/315等)", "卡商群投诉") or (x["source_type"] == "外部投诉" and x["name"].startswith("外部投诉"))), 0))
    # 为避免重复计算,直接重算 total = 三表独立相加
    _cnt_st = db.execute(select(func.count()).where(tk_between, _ticket_category_case() == "投诉")).scalar() or 0
    _cnt_ec = db.execute(select(func.count()).where(ec_between)).scalar() or 0
    _cnt_cc = db.execute(select(func.count()).where(cc_between)).scalar() or 0
    complaint_total = int(_cnt_st) + int(_cnt_ec) + int(_cnt_cc)
    # 投诉来源类型聚合: 外部投诉 vs 内部投诉 各自数量与占比(分析维度独立核算)
    comp_src_rows = db.execute(
        select(_complaint_source_type_case(), func.count().label("cnt"))
        .where(tk_between, _ticket_category_case() == "投诉")
        .group_by("complaint_source_type")
        .order_by(text("cnt DESC"))
    ).all()
    complaint_by_source = [
        {"name": r[0] or "内部投诉", "value": int(r[1] or 0)}
        for r in comp_src_rows
    ]
    # 外部投诉平台(ExternalComplaint + CardComplaint) 归入外部/内部 来源桶
    if _cnt_ec > 0:
        # 所有 external_complaints 统一算 "外部投诉"
        found_outer = False
        for x in complaint_by_source:
            if x["name"] == "外部投诉":
                x["value"] += int(_cnt_ec); found_outer = True; break
        if not found_outer:
            complaint_by_source.append({"name": "外部投诉", "value": int(_cnt_ec)})
    if _cnt_cc > 0:
        # 卡商群投诉 = 内部投诉
        found_inner = False
        for x in complaint_by_source:
            if x["name"] == "内部投诉":
                x["value"] += int(_cnt_cc); found_inner = True; break
        if not found_inner:
            complaint_by_source.append({"name": "内部投诉", "value": int(_cnt_cc)})
    # 补齐"内部投诉""外部投诉"两个桶,缺失补0 (前端颜色固定)
    _src_present = {x["name"] for x in complaint_by_source}
    for nm in ("外部投诉", "内部投诉"):
        if nm not in _src_present:
            complaint_by_source.append({"name": nm, "value": 0})
    complaint_by_source.sort(key=lambda x: -x["value"])

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
              -- 投诉工单: 与 _ticket_category_case 口径严格一致
              AND (
                st.service_category IS NULL
                OR st.service_category ILIKE '%投诉%'
                OR st.service_item ILIKE ANY (ARRAY[
                    '%12315%', '%工信部%', '%消协%', '%公共服务平台%', '%消费者协会%',
                    '%监督局%', '%市场监管%', '%市监%', '%315平台%', '%12345%', '%政务平台%',
                    '%商品投诉%', '%服务投诉%', '%微信投诉%'
                ])
              )
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
        "date": d_iso,
        "date_end": d_end_iso,
        # 原接待分类
        "categories": sorted(categories, key=lambda x: -x["value"]),
        "top_staff": top_staff,
        # 新增: 今日投诉
        "complaint_total": complaint_total,
        "complaint_subs": complaint_subs,     # 投诉子类 (饼图: 商品投诉/服务投诉/微信投诉/12315/工信部/公共服务平台, 每项带 source_type)
        "complaint_by_source": complaint_by_source,  # 投诉来源(内部/外部) 两个桶独立占比
        "complaint_roots": complaint_roots,   # 投诉根因 (柱图: 6 大类)
        "complaint_unmatched": complaint_unmatched,  # 未匹配到接待记录的投诉数
    }


def _derive_service_item_for_refund(
    remark: str | None,
    audit_remark: str | None,
    package_name: str | None,
    refund_reason: str | None,
) -> tuple[str, str | None]:
    """根据退款单 remark/audit_remark/package 猜 service_category + service_item

    返回: (service_category, service_item). 命中明确关键词优先:
      「超期」→ 退款 / 退款-超期纠纷
      「充值」+ 退款 → 退款 / 充值错误
      网慢/网卡/无网络 → 投诉 / 网络问题
      不想用 / 要退 → 退款 / 不想用了
      设备坏 → 投诉 / 设备故障
    兜底 scat=退款 sitm=退款管理-未分类
    """
    blob = " ".join(x or "" for x in [remark, audit_remark, package_name, refund_reason])
    b = blob.lower()
    if any(k in b for k in ["超期", "超期订单不允许"]):
        return "退款", "退款-超期纠纷"
    if any(k in blob for k in ["充值错误", "充错", "充错了", "冲错"]):
        return "退款", "充值错误"
    if any(k in b for k in ["网慢", "网卡", "无网络", "没网", "断网", "信号差", "网速"]):
        return "投诉", "网络问题"
    if any(k in b for k in ["不想用", "不要了", "用不上", "不想续费", "不需要了", "不用了", "退订"]):
        return "退款", "不想用了"
    if any(k in b for k in ["设备坏", "坏了", "设备故障", "硬件", "无法开机", "坏机"]):
        return "投诉", "设备故障"
    # WiFi钱包充值
    if any(k in (package_name or "") for k in ["WiFi钱包", "钱包"]):
        return "退款", "充值错误"
    return "退款", "退款管理-未分类"


def _ensure_service_tickets_for_unmatched_refunds(
    db: Session,
    range_start: _dt.datetime,
    range_end: _dt.datetime,
    match_win_sec: int = 86400 * 45,
) -> dict[str, int]:
    """F2: 审计兜底 — 针对 [range_start, range_end) 内所有路线A退款(线上直退),
    若找不到 ±match_win_sec 内同 SN 的 service_ticket, 自动补建一条 service_ticket,
    用 refund.operator → registrant, refund.auditor 或 operator → handler,
    用 remark/audit_remark/package_name 关键字推 service_category/service_item.

    为保证幂等: 新增的工单号 prefix = 'GDREF_'+refund_no (和正常 GDC/GD prefix 完全区分);
    若已存在同 ticket_no 的工单 → 跳过 (INSERT ON CONFLICT DO NOTHING 语义, 用 merge instead)

    返回: {"inserted": N, "skipped": M} 供调用方参考
    """
    # 1. 扫所有日期内路线A退款
    in_range = and_(
        Refund.created_at_raw.isnot(None),
        Refund.created_at_raw >= range_start,
        Refund.created_at_raw <= range_end,
        *_route_a_online_refund_filter(),
    )
    refunds_rows = db.execute(select(
        Refund.refund_no, Refund.device_sn, Refund.operator, Refund.auditor,
        Refund.remark, Refund.audit_remark, Refund.package_name, Refund.refund_reason,
        Refund.refund_amount, Refund.actual_amount, Refund.created_at_raw, Refund.updated_at_raw,
        Refund.platform_order_no, Refund.status,
    ).where(in_range)).all()
    if not refunds_rows:
        return {"inserted": 0, "skipped": 0}

    # 1.5 姓名归一查维表: 把 operator/auditor → Person.display_name 一次性先查好 (避免 for 里 N+1)
    all_names = sorted({
        x for r in refunds_rows for x in [r.operator or "", r.auditor or ""] if x
    })
    name_map: dict[str, str] = {}
    if all_names:
        op_prs = _operator_to_person_cte()
        alias_rows = db.execute(select(op_prs.c.operator, op_prs.c.display_name
        ).where(op_prs.c.operator.in_(all_names))).all()
        for a, dn in alias_rows:
            if dn and a:
                name_map[a] = dn
    def _dn(x: str | None) -> str | None:
        if not x: return None
        return name_map.get(x, x)  # 没alias登记则保留原值

    # 2. 批量判断每条退款是否有"有效"工单 (1 次 SQL 直接跑存在性判断)
    # v3.2 P0修复: 只有匹配到 handler 非空(已指派/已处理) 的工单才算"有工单";
    #   如果匹配到的是 handler=NULL 的用户自助工单(H5/微信投诉), 视为"仍然无有效接待人",
    #   需要继续补建 GDREF_ 兜底工单, 避免 handler 空值落入 [未分配] 桶.
    #   同时增加 service_category IS NOT NULL 条件以改善原因榜的覆盖率(与P0-1排序规则对齐)
    have_ticket: set[str] = set()
    # 简单策略: 直接按 SN 列表过滤 + 时间窗
    sns = sorted({r.device_sn for r in refunds_rows if r.device_sn})
    if sns:
        sec = match_win_sec
        pairs = db.execute(select(Refund.refund_no, ServiceTicket.ticket_no
        ).select_from(Refund).join(ServiceTicket, and_(
            ServiceTicket.device_sn == Refund.device_sn,
            ServiceTicket.device_sn.in_(sns),
            Refund.device_sn.in_(sns),
            ServiceTicket.register_time.isnot(None),
            Refund.created_at_raw.isnot(None),
            func.abs(func.extract("epoch", ServiceTicket.register_time - Refund.created_at_raw)) < sec,
            # v3.2 P0新增: 必须handler非空, 才算"已被服务工单覆盖"
            ServiceTicket.handler.isnot(None),
            ServiceTicket.handler != "",
            # 同时工单必须有明确分类信息(避免"空分类工单"阻止补建, 导致原因榜也失真)
            ServiceTicket.service_category.isnot(None),
            ServiceTicket.service_category != "",
        )).where(in_range)).all()
        for rno, _tno in pairs:
            have_ticket.add(rno)

    # 3. 对没工单的退款 建 service_ticket
    inserted = 0
    skipped = 0
    for r in refunds_rows:
        if r.refund_no in have_ticket:
            continue
        # 幂等: 工单号 = GDREF_{refund_no}  (64 字符限制 OK, refund_no ~ 22 字符)
        ticket_no = "GDREF_" + r.refund_no
        # 查重(已有同 ticket_no 已建过)
        ex = db.execute(select(ServiceTicket.ticket_no).where(ServiceTicket.ticket_no == ticket_no)).first()
        if ex:
            skipped += 1
            have_ticket.add(r.refund_no)
            continue
        scat, sitm = _derive_service_item_for_refund(r.remark, r.audit_remark, r.package_name, r.refund_reason)
        registrant_raw = r.operator or "系统自动补建"
        handler_raw = (r.auditor or r.operator or None)
        # 写入前姓名归一 (维表 PersonAlias→Person.display_name), 保持与展示侧 by_staff 口径一致, 避免同一人在不同表里用不同字符串
        registrant = _dn(registrant_raw) or registrant_raw
        handler = _dn(handler_raw) if handler_raw else "未指派"
        # 备注信息: 合并退款 remark/audit_remark/refund_reason + 来源 refund_no
        rmk_parts = []
        if r.remark:
            rmk_parts.append("退款备注:" + r.remark)
        if r.audit_remark:
            rmk_parts.append("审核:" + r.audit_remark)
        if r.refund_reason and r.refund_reason != "用户退款":
            rmk_parts.append("原退款原因:" + r.refund_reason)
        rmk_parts.append("来源:退款单" + r.refund_no)
        if r.package_name:
            rmk_parts.append("商品:" + r.package_name)
        # 登记时间用退款的 created_at_raw
        reg_time = r.created_at_raw or (range_start + (range_end - range_start) / 2)
        # finish_time 用 updated_at_raw (如 status 非空且已完成)
        finish_time = None
        st_status = "处理中"
        if r.status and ("退款" in r.status or "完成" in r.status or "成功" in r.status):
            st_status = "已完成"
            finish_time = r.updated_at_raw or reg_time
        # 处理人如果是中文非空, 把 st_status 直接置已完成(默认已经操作完了), 避免人工重算
        if handler and handler != "未指派":
            st_status = "已完成"
            finish_time = r.updated_at_raw or reg_time
        new_t = ServiceTicket(
            ticket_no=ticket_no,
            device_sn=r.device_sn,
            phone=None,
            service_category=scat,
            service_item=sitm,
            priority=2,
            status=st_status,
            remark="; ".join(rmk_parts),
            register_time=reg_time,
            active_time=reg_time,
            dispatch_time=reg_time if (handler and handler != "未指派") else None,
            finish_time=finish_time,
            handle_duration=None,
            registrant=registrant,
            channel="系统自动补建(退款兜底)",
            handle_node="系统自动补建",
            handler=handler,
            handle_opinion=(
                "经营-实际对账兜底: 该退款发生前/后 " + str(match_win_sec // 86400) +
                " 天内无对应服务工单; 系统依据退款单字段自动补建以便统计分析。operator=" +
                (r.operator or "") + ", auditor=" + (r.auditor or "")
            ),
            is_appeal="否",
            order_no=r.platform_order_no,
            submit_params=None,
            refund_amount=float(r.actual_amount or r.refund_amount or 0.0),
            estimated_amount=float(r.refund_amount or 0.0),
        )
        db.add(new_t)
        inserted += 1
        have_ticket.add(r.refund_no)
    # Flush 以便 immediately following 的 best_match CTE 能看到; 外部事务统一 commit
    if inserted:
        db.flush()
    return {"inserted": inserted, "skipped": skipped}


def today_refund_breakdown(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块4: 今日退款分项 = 退款细分原因Top10 + 退款接待人Top10 【统一口径: 路线A refund_method='线上'】

    v3.1 升级(2026-08-02):
      1. 时间窗过窄修复: |register_time − created_at_raw| < ±30天 → ±45天 (覆盖老套餐月末冷静期退款)
      2. 未关联工单兜底: 进入分析前调用_ensure_service_tickets_for_unmatched_refunds
         自动按退款单补建 service_ticket(ticket_no=GDREF_*),
         handler = auditor或operator, service_item 由 remark/package 关键词推导
         → 彻底消除 "[未分配]" 桶绩效失真
      3. 原因榜/接待人榜仍用同一张 best_match CTE
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None
    between = and_(Refund.created_at_raw.isnot(None),
                   Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
                   *_route_a_online_refund_filter())
    _WIN_45D = 86400 * 45  # v3.1: ±30d → ±45d,修复 8/1 样本里 31.28d 这种工单登记稍早被错漏的情况

    # ── F2. 自动补建工单: 日期内 ±45d 未匹配到工单的退款, 自动建一条 service_ticket, 保证 接待人/原因 绩效不漏 ──
    _ensure_service_tickets_for_unmatched_refunds(db, ts, te, match_win_sec=_WIN_45D)

    # ── CTE 共享: 每个 refund 最佳匹配工单 ──
    # v3.2 排序升级 (P0修复):
    #   1) handler 非空优先 (避免"时差最小但handler=NULL的用户自助工单"覆盖掉"时差稍大但有真实处理人"的工单)
    #   2) scat/sitm 非空优先 (同理, 原因榜不要选上空分类的工单)
    #   3) 时间差最小 (原有逻辑, 作为次级排序)
    # 同时带出 Refund.auditor/operator 作为最终兜底: 工单hdl仍空时直接回退到退款单审核人/操作人
    _hdl_ok = case(
        (and_(ServiceTicket.handler.isnot(None), ServiceTicket.handler != ""), 1),
        else_=0,
    )
    _cat_ok = case(
        (and_(
            ServiceTicket.service_category.isnot(None), ServiceTicket.service_category != "",
            ServiceTicket.service_item.isnot(None), ServiceTicket.service_item != "",
        ), 2),
        (and_(
            (ServiceTicket.service_category.isnot(None)) & (ServiceTicket.service_category != ""),
        ), 1),
        else_=0,
    )
    best_stmt = select(
        Refund.refund_no.label("rid"),
        Refund.refund_amount.label("amt"),
        Refund.package_name.label("pkg"),
        Refund.refund_reason.label("rrsn"),
        Refund.auditor.label("raud"),   # 新增: 退款单审核人 (P2回退)
        Refund.operator.label("rop"),   # 新增: 退款单操作人 (P2回退)
        # 工单字段 (左连接, 可能 NULL)
        ServiceTicket.service_category.label("scat"),
        ServiceTicket.service_item.label("sitm"),
        ServiceTicket.handler.label("hdl"),
        func.abs(func.extract("epoch",
            ServiceTicket.register_time - Refund.created_at_raw
        )).label("diff_sec"),
    ).select_from(Refund).outerjoin(ServiceTicket, and_(
        ServiceTicket.device_sn == Refund.device_sn,
        Refund.device_sn.isnot(None), Refund.device_sn != "",
        ServiceTicket.register_time.isnot(None),
        func.abs(func.extract("epoch",
            ServiceTicket.register_time - Refund.created_at_raw
        )) < _WIN_45D,
    )).where(between)
    best_cte = best_stmt.distinct(text("rid")).order_by(
        text("rid"),
        _hdl_ok.desc(),          # P0: handler 非空优先 (1 > 0)
        _cat_ok.desc(),          # 同步: 分类非空优先
        text("diff_sec NULLS LAST"),
    ).cte("best_match")

    # ── ① 左: 细分原因 Top10 (按金额倒序) ──
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

    # ── ② 右: 退款接待人 Top10 ──
    # v3.2 两级兜底策略 (P2修复):
    #   Tier1: 工单handler → 经 PersonAlias 归一化后取 display_name
    #   Tier2: 工单handler仍空时 → 用退款单自身的 auditor (优先) / operator (次选) 回退计名
    #          与 _ensure_service_tickets_for_unmatched_refunds 补建工单时取handler逻辑完全一致
    #   Tier3: auditor/operator 都空 → 才进 "[未分配]" 桶
    #
    # 姓名归一化: 一次性从 best_cte 取出 hdl/raud/rop 三列的去重 raw 值集合,
    # 用 1 次 _operator_to_person_cte 反查构建 name_map (避免 SQL 里多CTE同名冲突 & N+1),
    # 然后在 SQL 里按优先级算出 raw_handler, 最后 Python 端统一做 display_name 替换 + 聚合.
    # Step B1: 查所有候选name的归一结果 (1次SQL取三列, Python端合并去重构建name_map, 避免Union/CTE复杂)
    name_rows = db.execute(select(
        best_cte.c.hdl, best_cte.c.raud, best_cte.c.rop,
    ).select_from(best_cte)).all()
    all_raw: set[str] = set()
    for r in name_rows:
        if r.hdl: all_raw.add(r.hdl)
        if r.raud: all_raw.add(r.raud)
        if r.rop: all_raw.add(r.rop)
    name_map: dict[str, str] = {}
    if all_raw:
        op_prs = _operator_to_person_cte()
        nm_rows = db.execute(select(
            op_prs.c.operator, op_prs.c.display_name,
        ).where(op_prs.c.operator.in_(sorted(all_raw)))).all()
        for _op, dn in nm_rows:
            if dn and _op:
                name_map[_op] = dn

    def _dn(x: str | None) -> str | None:
        if not x: return None
        return name_map.get(x, x)

    # Step B2: SQL 聚合只按 raw_handler (三级回退优先级) 聚合, 不做维表JOIN
    _raw_hdl = case(
        (best_cte.c.hdl.isnot(None) & (best_cte.c.hdl != ""), best_cte.c.hdl),
        (best_cte.c.raud.isnot(None) & (best_cte.c.raud != ""), best_cte.c.raud),
        (best_cte.c.rop.isnot(None) & (best_cte.c.rop != ""), best_cte.c.rop),
        else_=None,
    )
    staff_raw = db.execute(select(
        _raw_hdl.label("raw"),
        func.count().label("cnt"),
        func.coalesce(func.sum(best_cte.c.amt), 0).label("amt"),
    ).select_from(best_cte
    ).group_by(text("raw"))).all()

    # Step B3: Python 端 display_name 归一 → 合并 → 排序取 Top
    merged: dict[str, dict] = {}
    for r in staff_raw:
        raw = r.raw
        if not raw:
            nm = "[未分配] 未关联工单或未指派处理人"
        else:
            nm = _dn(raw) or raw or "[未分配] 未关联工单或未指派处理人"
        if nm not in merged:
            merged[nm] = {"name": nm, "count": 0, "amount": 0.0}
        merged[nm]["count"] += int(r.cnt or 0)
        merged[nm]["amount"] += float(r.amt or 0)
    by_staff = sorted(merged.values(), key=lambda x: -x["amount"])[:15]
    for s in by_staff:
        s["amount"] = round(s["amount"], 2)

    # ── ③ 补充字段: 退款商品分布 Top10 (独立于原因维度, 用于业务分析但不混原因榜) ──
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

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "reasons": reasons,                 # 左: 退款原因榜 (工单分类 + 未登记统一桶)
        "packages": packages,               # 独立榜: 退款商品分布 Top10 (与原因榜解耦)
        "by_staff": by_staff,               # 右: 退款接待人 Top10 (工单处理人 handler)
        "methods": [],                      # 向后兼容: 原"退款方式分布" 只路线A一桶无意义, 移除以防误导
        "_spec": {
            "scope": "路线A: 退费单 refund_method='线上'",
            "reason_strategy": (
                "v3.2: 1)best_match DISTINCT ON排序: 先分类完整性(scat&sitm > scat > 空) → 再时差最小; "
                "2)自动补建GDREF_工单的判定条件升级为: 只有[handler非空 & 分类非空]的工单才算'已覆盖' → "
                "否则继续补建GDREF_以纠正原因榜; 3)补建后仍无分类 → '[原因未登记] 未关联到工单' 统一桶"
            ),
            "staff_strategy": (
                "v3.2 P0+P2三级兜底: 1)best_match DISTINCT ON 先选handler非空的工单(>handler空时差最小); "
                "2)工单handler仍空 → 回退用退款单auditor → operator (与补建工单取handler口径一致, 自动PersonAlias归一); "
                "3)auditor/operator全空 → 才进'[未分配] 未关联工单或未指派处理人'桶; "
                "4)补建工单判定升级为: 仅handler非空工单算已覆盖, 否则仍补建GDREF_填handler"
            ),
            "time_window_seconds": _WIN_45D,
            "packages_strategy": "独立字段 packages: 按 package_name 商品 Top10 (原因榜与商品榜彻底解耦)",
        },
    }


def today_queue(db: Session, date: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """模块5: 今日处理队列 = 今日截止待办 + 进行中专项 + 今日异常事件.
    每项最多10条, 点击可跳转详情页.
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None
    today_start = ts
    today_end = te
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
        "date": d_iso,
        "date_end": d_end_iso,
        "todos": todos,
        "tasks": tasks,
        "incidents": incidents,
    }


def today_staff_efficiency(
    db: Session,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    topn: int = 30,
    qw_only: bool = False,
    aggregate_by: str = "person",   # "person" 按人明细 / "specialty" 按专项 / "group" 按分组 / "all" 全员汇总
    department: str | None = None,  # 按部门筛选
    group_id: int | None = None,    # 按分组ID筛选
    specialty_id: int | None = None, # 按专项ID筛选
) -> dict[str, Any]:
    """模块7: 今日人均效率表 (按operator聚合, 关联Person拿display_name/group_name).

    两个独立合成指标 (用户 2026-08-02 新算法, 2026-08-02 v2 二次修订, 2026-08-09 v3 转接系数调整):
      a. 工作量分 (会话次数维度, 反映忙闲强度):
         = Σ (专项会话数) × 专项难度系数 × 本职系数(本职=1.0 / 非本职转接=固定0.2, 不再叠加专项难度)
      b. 效率分   (接待个数维度, SN去重, 反映服务覆盖面)
         —— 2026-08-02 修订: 跨表SN合并去重
         智能助手(cs_record)接待SN ∪ 服务工单(ServiceTicket)处理SN, 按当天汇总后去重
         同一SN在两个表都出现只算1个, 真实反映当天服务覆盖的独立设备/客户数.
         系统自动产生的记录排除: cs_record 中 sys/system/系统自动 等 operator,
         以及 description/execution_result/solution 中明确是自动触发的关键词.

    本职判定:
      - HR 预设专项 与 计算专项 同一大类 → 本职, 全系数
      - 岗位=主管/组长, 或HR专项属于管理层/投诉 → 全部本职, 豁免转接惩罚
      - HR未登记的新人 → 默认全部本职 (避免新人刚进来全部被打折)

    非本职 = 跨专项转接, 固定折算 TRANSFER_PENALTY (0.2), 不再 × 专项难度系数.
    系统/用户自助 虚拟账户 → 不进真人员工排名, 补在末尾 rank=NULL.

    新增 2026-08-09 筛选 & 聚合:
      qw_only = True  → 仅显示 Person 维表中 qw_user_id 非空 (已绑定企微账号) 的人员
      aggregate_by:
        "person"      → 原口径: 每人一行 (默认)
        "specialty"   → 按 hr_category (咨询接待/套餐专项/退款挽回/投诉专员) 聚合
        "group"       → 按 group_name (行政小组) 聚合
        "all"         → 所有真人一行总汇总 (显示"全员合计")
    """
    ts, te, _, _, is_realtime, disp_range = _resolve_windows(date=date, start=start, end=end)
    if disp_range:
        d_iso = disp_range[0][:10]
        d_end_iso = disp_range[1][:10]
    else:
        d_iso = (_dt.datetime.now(TZ).date() if date is None else _parse_date(date)).isoformat()
        d_end_iso = None
    op_person = _operator_to_person_cte(department=department, group_id=group_id, specialty_id=specialty_id)

    # ─────────────────────────────────────────────────────────────────────
    # 系统/自动记录排除规则 (同时用于 cs_record 与 退款/工单)
    #   1. operator 精确黑名单: sys/system/SYS/System 大小写变体 + 用户自助/客户自助
    #   2. 业务字段关键词: description/execution_result/solution 中包含"系统自动""自动发送"
    # ─────────────────────────────────────────────────────────────────────
    SYS_OP_BLACKLIST = ["sys", "system", "SYS", "System", "用户自助", "客户自助", "企微用户自助"]
    def _sys_auto_filter():
        """返回 SQL 条件 排除 cs_record 中系统自动产生的记录"""
        kw = ("系统自动", "自动发送", "自动回复", "自动推送", "自动补发", "自动触发", "自动生成")
        kw_like = [CsRecord.description.ilike(f"%{k}%") for k in kw] \
                + [CsRecord.execution_result.ilike(f"%{k}%") for k in kw] \
                + [CsRecord.solution.ilike(f"%{k}%") for k in kw]
        return and_(
            CsRecord.operator.isnot(None),
            CsRecord.operator.notin_(SYS_OP_BLACKLIST),
            ~or_(*kw_like),  # 反向: 不包含任何自动关键词
        )

    # 1. 会话接待量 + 接待人数(去重SN) 总量 per operator
    #    v2: 排除系统自动记录
    cs_q = (select(CsRecord.operator.label("op"),
                   func.count().label("c"),
                   func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("dc"))
            .where(CsRecord.operation_time.isnot(None),
                   CsRecord.operation_time >= ts, CsRecord.operation_time <= te,
                   _sys_auto_filter())
            .group_by(CsRecord.operator)).cte("cs_q")
    # 2. 会话接待 按 6 个 category_bucket 分桶 per operator: 会话数 + 接待数(SN去重) 两套
    #    v2: 同样排除系统自动记录
    cs_cat_q = (select(
        CsRecord.operator.label("op"),
        _category_case().label("bucket"),
        func.count().label("scnt"),
        func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("pcnt"),
    ).where(
        CsRecord.operation_time.isnot(None),
        CsRecord.operation_time >= ts, CsRecord.operation_time <= te,
        _sys_auto_filter(),
    ).group_by(CsRecord.operator, "bucket")).cte("cs_cat_q")
    # 3. 退款挽回实际执行: 笔数 + 去重设备个数
    #    已有 sys/system 排除, 保留
    rf_q = (select(Refund.operator.label("op"),
                   func.count().label("sc"),
                   func.count(func.distinct(func.nullif(func.trim(Refund.device_sn), ""))).label("pc"))
            .where(Refund.created_at_raw.isnot(None),
                   Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
                   Refund.operator.isnot(None),
                   Refund.operator.notin_(["sys", "system", "SYS", "System"]),
                   *_route_a_online_refund_filter())
            .group_by(Refund.operator)).cte("rf_q")
    # 4. 投诉类工单 处理人完工: 笔数 + 去重设备个数
    cm_q = (select(ServiceTicket.handler.label("op"),
                   func.count().label("sc"),
                   func.count(func.distinct(func.nullif(func.trim(ServiceTicket.device_sn), ""))).label("pc"))
            .where(ServiceTicket.finish_time.isnot(None),
                   ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te,
                   ServiceTicket.handler.isnot(None),
                   ServiceTicket.handler.notin_(SYS_OP_BLACKLIST),
                   _ticket_category_case() == "投诉")
            .group_by(ServiceTicket.handler)).cte("cm_q")

    # ─────────────────────────────────────────────────────────────────────
    # v2 新增: 5. 服务工单 全类处理人完工(不只是投诉) — 用于 SN 合并去重
    #   当天任何完工的服务工单, 其 device_sn 都纳入该处理人"当天工作覆盖SN"
    #   因为: 咨询/套餐/退款/设备故障类工单也是实际工作, 不能只靠 cs_record
    # ─────────────────────────────────────────────────────────────────────
    tk_all_q = (select(ServiceTicket.handler.label("op"),
                       func.count().label("sc"),
                       func.count(func.distinct(func.nullif(func.trim(ServiceTicket.device_sn), ""))).label("pc"))
                .where(ServiceTicket.finish_time.isnot(None),
                       ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te,
                       ServiceTicket.handler.isnot(None),
                       ServiceTicket.handler.notin_(SYS_OP_BLACKLIST))
                .group_by(ServiceTicket.handler)).cte("tk_all_q")

    # 并集: 做过接待 或 退款 或 投诉处理 或 任何工单完工 的人员
    ops_union = union(
        select(cs_q.c.op), select(rf_q.c.op), select(cm_q.c.op), select(tk_all_q.c.op)
    ).cte("ops_u")

    rows = db.execute(
        select(
            ops_union.c.op,
            func.coalesce(op_person.c.display_name, ops_union.c.op).label("name"),
            op_person.c.department.label("dept"),
            op_person.c.group_name.label("grp"),
            op_person.c.position.label("pos"),
            op_person.c.specialty.label("sp"),
            op_person.c.qw_user_id.label("qw_user_id"),
            func.coalesce(cs_q.c.c, 0).label("rc"),
            func.coalesce(cs_q.c.dc, 0).label("rdc"),
        )
        .select_from(ops_union)
        .outerjoin(cs_q, ops_union.c.op == cs_q.c.op)
        .outerjoin(op_person, ops_union.c.op == op_person.c.operator)
    ).all()

    # 会话/接待两套 6 桶 按 op 聚合
    cs_cat_by_op: dict[str, dict[str, tuple[int, int]]] = {}
    for r in db.execute(select(cs_cat_q)).all():
        cs_cat_by_op.setdefault(r.op, {})[r.bucket] = (int(r.scnt or 0), int(r.pcnt or 0))
    rf_by_op: dict[str, tuple[int, int]] = {r.op: (int(r.sc or 0), int(r.pc or 0)) for r in db.execute(select(rf_q)).all()}
    cm_by_op: dict[str, tuple[int, int]] = {r.op: (int(r.sc or 0), int(r.pc or 0)) for r in db.execute(select(cm_q)).all()}
    tk_all_by_op: dict[str, tuple[int, int]] = {r.op: (int(r.sc or 0), int(r.pc or 0)) for r in db.execute(select(tk_all_q)).all()}

    # ─────────────────────────────────────────────────────────────────────
    # v2 新增: 跨表 SN 合并去重 —— 每个人员 × 专项 的 SN 集合 union 后再计数
    #   - 咨询:  cs_record[咨询/其他/综合] SN
    #   - 套餐:  cs_record[套餐问题] SN  +  工单中 service_category='套餐疑问'/'套餐' SN
    #   - 退款:  cs_record[退款] SN     +  refund SN
    #   - 投诉:  cs_record[投诉] SN     +  投诉类工单 SN
    #   - 另外: 工单中其他非投诉 SN 并入"咨询"桶 (如设备故障/网络故障等按咨询算)
    # 用 SQL 一次性按 op × 专项 聚合 DISTINCT SN, 避免 Python 层内存中拼 SN 集合.
    # ─────────────────────────────────────────────────────────────────────
    merged_sn_sql = text(
        """
        -- 子查询1: cs_record 按 6 类 bucket × SN 输出 (过滤系统自动)
        WITH cs_sns AS (
            SELECT
                c.operator AS op,
                CASE
                    WHEN COALESCE(c.problem_category,'') ILIKE '%投诉%' THEN '投诉'
                    WHEN COALESCE(c.problem_category,'') ILIKE '%退款%' THEN '退款'
                    WHEN COALESCE(c.problem_category,'') IN ('套餐','套餐问题') THEN '套餐'
                    WHEN COALESCE(c.problem_category,'') ILIKE '%咨询%'
                      OR COALESCE(c.problem_category,'') ILIKE '%网络问题%'
                      OR COALESCE(c.problem_category,'') ILIKE '%综合%'
                      OR COALESCE(c.problem_category,'') ILIKE '%其他%'
                      OR COALESCE(c.problem_category,'') = '' THEN '咨询'
                    ELSE '咨询'
                END AS spec,
                NULLIF(TRIM(c.device_sn),'') AS sn
            FROM cs_records c
            WHERE c.operation_time IS NOT NULL
              AND c.operation_time >= :ts AND c.operation_time <= :te
              AND c.operator IS NOT NULL
              AND c.operator NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助')
              AND NOT (
                  COALESCE(c.description,'') ILIKE ANY(ARRAY['%系统自动%','%自动发送%','%自动回复%','%自动推送%','%自动补发%','%自动触发%','%自动生成%'])
                  OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY['%系统自动%','%自动发送%','%自动回复%','%自动推送%','%自动补发%','%自动触发%','%自动生成%'])
                  OR COALESCE(c.solution,'') ILIKE ANY(ARRAY['%系统自动%','%自动发送%','%自动回复%','%自动推送%','%自动补发%','%自动触发%','%自动生成%'])
              )
        ),
        -- 子查询2: refund SN → "退款" 专项
        rf_sns AS (
            SELECT r.operator AS op, '退款' AS spec,
                   NULLIF(TRIM(r.device_sn),'') AS sn
            FROM refunds r
            WHERE r.created_at_raw IS NOT NULL
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te
              AND r.operator IS NOT NULL
              AND r.operator NOT IN ('sys','system','SYS','System')
              AND r.refund_method = '线上'
        ),
        -- 子查询3: 投诉工单 SN → "投诉" 专项
        cm_sns AS (
            SELECT st.handler AS op, '投诉' AS spec,
                   NULLIF(TRIM(st.device_sn),'') AS sn
            FROM service_tickets st
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te
              AND st.handler IS NOT NULL
              AND st.handler NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助')
              AND (st.service_category IS NULL OR st.service_category = '投诉')
        ),
        -- 子查询4: 非投诉工单 SN → 按 service_category 分桶 (退款→退款, 套餐疑问/套餐→套餐, 其他→咨询)
        tk_sns AS (
            SELECT st.handler AS op,
                   CASE
                       WHEN COALESCE(st.service_category,'') = '退款' THEN '退款'
                       WHEN COALESCE(st.service_category,'') IN ('套餐疑问','套餐') THEN '套餐'
                       WHEN COALESCE(st.service_category,'') IN ('换货','设备故障','网络故障') THEN '咨询'
                       ELSE '咨询'
                   END AS spec,
                   NULLIF(TRIM(st.device_sn),'') AS sn
            FROM service_tickets st
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te
              AND st.handler IS NOT NULL
              AND st.handler NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助')
        ),
        -- 合并 4 个子查询, 按 op × spec × DISTINCT SN 去重后 聚合: 该人员该专项的真实独立设备数
        merged AS (
            SELECT op, spec, COUNT(DISTINCT sn) AS sn_cnt
            FROM (
                SELECT op, spec, sn FROM cs_sns WHERE sn IS NOT NULL
                UNION ALL SELECT op, spec, sn FROM rf_sns WHERE sn IS NOT NULL
                UNION ALL SELECT op, spec, sn FROM cm_sns WHERE sn IS NOT NULL
                UNION ALL SELECT op, spec, sn FROM tk_sns WHERE sn IS NOT NULL
            ) all_sn
            GROUP BY op, spec
        )
        SELECT op, spec, sn_cnt FROM merged;
        """
    )
    merged_sn_rows = db.execute(merged_sn_sql, {"ts": ts, "te": te}).fetchall()
    # 构造成: merged_pp[op][spec] = 跨表去重后的 SN 接待个数
    merged_pp: dict[str, dict[str, int]] = {}
    for r in merged_sn_rows:
        merged_pp.setdefault(r.op, {})[r.spec or "咨询"] = int(r.sn_cnt or 0)

    # ─────────────────────────────────────────────────────────────────────
    # 继续计算各专项的会话次数 (按原逻辑, 非SN维度, 不需要去重合并)
    # 但需要把 工单的会话次数(=工单数) 也并入对应专项的会话总数:
    #   - 投诉专项会话 = cs_record[投诉]条数 + 投诉工单数
    #   - 退款专项会话 = cs_record[退款]条数 + 退款条数
    #   - 套餐专项会话 = cs_record[套餐]条数 + 套餐疑问工单数
    #   - 咨询专项会话 = cs_record[咨询/其他/综合]条数 + 其他类型工单数
    # 先查"套餐疑问工单数"和"其他类型工单数"按 handler 分桶
    # ─────────────────────────────────────────────────────────────────────
    tk_spec_count_sql = text(
        """
        SELECT st.handler AS op,
               CASE
                   WHEN (st.service_category IS NULL OR st.service_category = '投诉') THEN '投诉'
                   WHEN COALESCE(st.service_category,'') = '退款' THEN '退款'
                   WHEN COALESCE(st.service_category,'') IN ('套餐疑问','套餐') THEN '套餐'
                   ELSE '咨询'
               END AS spec,
               COUNT(*) AS cnt
        FROM service_tickets st
        WHERE st.finish_time IS NOT NULL
          AND st.finish_time >= :ts AND st.finish_time <= :te
          AND st.handler IS NOT NULL
          AND st.handler NOT IN ('sys','system','SYS','System','用户自助','客户自助','企微用户自助')
        GROUP BY 1, 2
        """
    )
    tk_spec_rows = db.execute(tk_spec_count_sql, {"ts": ts, "te": te}).fetchall()
    tk_spec_by_op: dict[str, dict[str, int]] = {}
    for r in tk_spec_rows:
        tk_spec_by_op.setdefault(r.op or "", {})[r.spec or "咨询"] = int(r.cnt or 0)

    # 2026-08-10 专项细分调整: 6个专项, 难度系数
    #   咨询接待×1.0 | 套餐专项×1.8 | 退款处理×2.0 | 退款挽回×2.2 | 内部投诉×3.0 | 外部投诉×5.0
    # 工单大类(4桶): 咨询/套餐/退款/投诉
    #   退款大类 包含: 退款处理、退款挽回 两个专项
    #   投诉大类 包含: 内部投诉、外部投诉 两个专项
    WEIGHT = {
        "咨询接待": 1.0,
        "套餐专项": 1.8,
        "退款处理": 2.0,
        "退款挽回": 2.2,
        "内部投诉": 3.0,
        "外部投诉": 5.0,
    }
    # 工单大类 → 管理岗/无主项人员使用的默认系数
    CAT_DEFAULT_WEIGHT = {
        "咨询接待": 1.0,    # 咨询
        "套餐专项": 1.8,    # 套餐
        "退款处理": 2.0,    # 退款(默认退款处理系数)
        "投诉接待": 3.0,    # 投诉(默认内部投诉系数)
    }
    # 工单大类key → (桶key, 大类名称, 包含的专项集合)
    CAT_BUCKETS = [
        ("zixun", "咨询接待", {"咨询接待"}),
        ("taocan", "套餐专项", {"套餐专项"}),
        ("tuikuan", "退款处理", {"退款处理", "退款挽回"}),
        ("tousu", "投诉接待", {"内部投诉", "外部投诉"}),
    ]
    CATS = [c[1] for c in CAT_BUCKETS]  # ["咨询接待", "套餐专项", "退款处理", "投诉接待"]
    TRANSFER_PENALTY = 0.2
    # 2026-08-09 用户新要求: 转接接待工作量/效率折算 = 固定 TRANSFER_PENALTY (0.2)
    #   不再叠加 专项难度系数 WEIGHT[c], 即:
    #   原 咨询 1.0×0.2=0.2 / 套餐 1.8×0.2=0.36 / 退款 2.2×0.2=0.44 / 投诉 3.0×0.2=0.6
    #   新 全部 → 0.2 (理由: 转接接待本身负荷相似且不高, 不按原专项难度打折上折)
    # 计算口径:
    #   本职 → coef = WEIGHT[hr_cat] (本人主专项系数)
    #   转接 → coef = TRANSFER_PENALTY (固定, 不再乘以 WEIGHT[c])
    #   管理岗 → coef = CAT_DEFAULT_WEIGHT[cat] (大类默认系数, 跨类豁免全系数)
    SYSTEM_VIRTUAL_EXACT = {"用户自助", "系统自动", "客户自助", "企微用户自助", "企微系统自动"}
    # 未登记到 Person 维表 的 第三方/系统/设备/公司 账号关键字 (关键字匹配 直接判非真人)
    VIRTUAL_HINT_WORDS = ("wifi","管家","科技","公司","设备","云客猫","闪猫","sys_","sys-","hqq","ykm-","畅享","商米","猫科","科联","运营")
    def _is_virtual_name(name: str, dept: str | None, grp: str | None, pos: str | None) -> bool:
        """非真人虚拟账户判定:
        ① 名字精确匹配 SYSTEM_VIRTUAL_EXACT → True
        ② 未登记 Person (部门=未设置 AND 组=未分组 AND 岗位空) 且 名字不像人名
           (≥5字 / 关键字命中 / ≥4字且含数字 / 含横杠下划线 公司/设备名特征 → True
        """
        if not name: return True
        if name in SYSTEM_VIRTUAL_EXACT: return True
        unregistered = (not dept or dept in ("未设置",)) and (not grp or grp in ("未分组", "用户自助")) and not pos
        if not unregistered: return False
        if len(name) >= 5: return True
        low = name.lower()
        if any(k.lower() in low for k in VIRTUAL_HINT_WORDS): return True
        if any(c.isdigit() for c in name) and len(name) >= 4: return True
        if ("-" in name) or ("_" in name): return True
        return False

    def _hr_map(hr_sp: str | None, pos: str | None) -> str | None:
        """人员主专项归一化映射 (2026-08-10 细分后)
        返回: 专项名称 | "管理层" | None
        """
        # 管理岗（主管/组长）→ 跨类全豁免
        if (pos or "") in ("主管", "组长", "副主管"):
            return "管理层"
        if not hr_sp:
            return None
        # 精确匹配6个标准专项名称
        s = (hr_sp or "").strip()
        if s in WEIGHT:
            return s
        # 兼容旧数据的模糊匹配
        if "外部投诉" in s: return "外部投诉"
        if "内部投诉" in s or "投诉" in s: return "内部投诉"
        if "退款挽回" in s: return "退款挽回"
        if "退款处理" in s or "退款" in s: return "退款处理"
        if "套餐" in s: return "套餐专项"
        if any(k in s for k in ("咨询", "接待", "综合问题", "其他问题")):
            return "咨询接待"
        return None

    BUCK_FZ = ("咨询", "其他问题", "综合问题")

    out: list[dict[str, Any]] = []; virtual_rows: list[dict[str, Any]] = []
    for r in rows:
        op = r.op
        rc = int(r.rc or 0)
        rdc = int(getattr(r, "rdc", 0) or 0)
        buckets = cs_cat_by_op.get(op, {})
        # 会话次数 (sessions):  cs_record条数 + 对应专项工单完工条数
        def _sess_sum(names: tuple[str, ...]) -> int:
            return sum((buckets.get(n, (0,0))[0] for n in names), 0)
        z_sess_cs = _sess_sum(BUCK_FZ)
        t_sess_cs = buckets.get("套餐问题", (0,0))[0]
        tu_sess_cs = buckets.get("退款", (0,0))[0]
        to_sess_cs = buckets.get("投诉", (0,0))[0]
        # 加上工单按专项分类的条数
        tk_sess = tk_spec_by_op.get(op or "", {})
        z_sess = z_sess_cs + tk_sess.get("咨询", 0)
        t_sess = t_sess_cs + tk_sess.get("套餐", 0)
        tu_sess = tu_sess_cs + rf_by_op.get(op, (0,0))[0] + tk_sess.get("退款", 0)
        to_sess = to_sess_cs + tk_sess.get("投诉", 0)
        rf_op_count = rf_by_op.get(op, (0,0))[0]

        # 接待个数 (people, SN去重跨表合并): 使用 SQL 侧预先算出的 merged_pp
        sn_pp = merged_pp.get(op or "", {})
        z_peop = sn_pp.get("咨询", 0)
        t_peop = sn_pp.get("套餐", 0)
        tu_peop = sn_pp.get("退款", 0)
        to_peop = sn_pp.get("投诉", 0)

        # 4 个工单大类桶 (key → sessions, people)
        cat_data = {
            "zixun": (z_sess, z_peop),
            "taocan": (t_sess, t_peop),
            "tuikuan": (tu_sess, tu_peop),
            "tousu": (to_sess, to_peop),
        }
        # 总接待设备数 = 4 桶 SN 并集个数 (SQL 侧没有算并集, 这里近似用桶求和; 未来如果需要精确并集再补)
        total_devices = z_peop + t_peop + tu_peop + to_peop
        # 若 total_devices 比 cs_record 侧 rdc 大, 用 total_devices 更真实; 否则保留 rdc 保持旧口径
        reception_device_count = max(rdc, total_devices)
        # 总会话数 = 工单并入后的 4 桶之和 (反映当天真实工作量条次)
        reception_count = z_sess + t_sess + tu_sess + to_sess
        # rc 是原 cs_record 侧算出来的会话总条数; 并入工单数后可能更大, 取 max 避免回退
        reception_count = max(rc, reception_count)

        position = r.pos or ""
        hr_cat = _hr_map(r.sp, position)
        # 管理岗判定: 主管/组长/副主管 → 跨类全系数豁免
        mgmt = (position in ("主管", "组长", "副主管")) or hr_cat == "管理层"

        work_score = 0.0; eff_score = 0.0
        bd: dict[str, dict[str, Any]] = {}
        for (bucket_key, cat_name, sp_set) in CAT_BUCKETS:
            sv, pv = cat_data[bucket_key]
            if sv == 0 and pv == 0:
                bd[cat_name] = {"sessions": 0, "people": 0, "weight": 0, "coef": 0, "is_own": False, "work_contrib": 0, "eff_contrib": 0}
                continue
            # 判断本职/转接
            if mgmt:
                # 管理岗: 跨类豁免, 使用大类默认系数
                own = True
                coef = CAT_DEFAULT_WEIGHT[cat_name]
            elif hr_cat is not None and hr_cat in sp_set:
                # 本人主专项属于该大类 → 本职, 使用本人专项系数
                own = True
                coef = WEIGHT[hr_cat]
            else:
                # 转接 → 固定 0.2
                own = False
                coef = TRANSFER_PENALTY
            wp = sv * coef; ep = pv * coef
            work_score += wp; eff_score += ep
            bd[cat_name] = {
                "sessions": sv, "people": pv,
                "weight": coef, "coef": round(coef, 3), "is_own": own,
                "work_contrib": round(wp, 1), "eff_contrib": round(ep, 1),
            }

        raw_name = r.name or op or "未知"
        norm_name = _strip_qw_prefix(raw_name) or raw_name
        _qw = getattr(r, "qw_user_id", None)
        record: dict[str, Any] = {
            "operator": op, "name": norm_name,
            "department": r.dept or "未设置", "group": r.grp or "未分组",
            "position": r.pos, "current_specialty": r.sp,
            "hr_category": hr_cat, "is_management": mgmt,
            "qw_user_id": _qw,
            "reception_count": reception_count,
            "reception_device_count": reception_device_count,
            "refund_op_count": rf_op_count,
            "sp_zixun": z_sess, "sp_taocan": t_sess, "sp_tuikuan": tu_sess, "sp_tousu": to_sess,
            "pp_zixun": z_peop, "pp_taocan": t_peop, "pp_tuikuan": tu_peop, "pp_tousu": to_peop,
            "weights": dict(WEIGHT), "transfer_penalty": TRANSFER_PENALTY,
            "breakdown": bd,
            "work_score": round(work_score, 2), "eff_score": round(eff_score, 2),
            "rank": None,              # rank 先写默认 None, 排序后真人员工再写 1..N
            "is_virtual": False,       # 显式 False, 避免 undefined 误判成 null 非真人 Tag 全亮
            # v2 口径说明供前端 tooltip 渲染
            "_calc_note": {
                "sessions_merge": "会话次数(次)=cs_record条数 + 对应专项工单完工条数(退款+全类工单并入)",
                "sn_merge_rule": "接待个数(个)=智能助手SN ∪ 工单SN 跨表去重, 按人员×专项独立核算; 排除sys/system/系统自动产生的记录",
                "weight_rule": "本职: 主专项系数(外诉×5.0/内诉×3.0/退款挽回×2.2/退款处理×2.0/套餐×1.8/咨询×1.0); 转接: 固定×0.2; 管理岗: 大类默认系数",
            },
        }
        # 非真人判定 = 精确虚拟匹配 OR 未登记+名字特征匹配
        if _is_virtual_name(norm_name, r.dept, r.grp, r.pos):
            record["is_virtual"] = True
            virtual_rows.append(record)
        else:
            out.append(record)

    # ═══════════════════════════════════════════════════════════════════
    # 2026-08-09 新增: 企微筛选 + 聚合视图
    # ═══════════════════════════════════════════════════════════════════
    def _filter_qw_only(recs: list[dict]) -> list[dict]:
        """qw_only: 仅保留 Person 维表已绑定企微 qw_user_id 的人员"""
        return [r for r in recs if (r.get("qw_user_id") or "").strip() != ""]

    def _aggregate_records(recs: list[dict], mode: str) -> list[dict]:
        """按 mode 聚合真人员工记录.
        mode: person (不聚合) / specialty (按hr_category) / group (按组) / all (全员一行)
        """
        if mode == "person":
            return recs
        if not recs:
            return recs
        SUM_KEYS = [
            "reception_count", "reception_device_count", "refund_op_count",
            "sp_zixun", "sp_taocan", "sp_tuikuan", "sp_tousu",
            "pp_zixun", "pp_taocan", "pp_tuikuan", "pp_tousu",
            "work_score", "eff_score",
        ]
        buckets: dict[str, dict] = {}
        for r in recs:
            if mode == "specialty":
                key = r.get("hr_category") or "未登记专项"
                disp_name = key
            elif mode == "group":
                key = r.get("group") or "未分组"
                disp_name = key
            elif mode == "all":
                key = "__ALL__"
                disp_name = "全员合计"
            else:  # fallback
                key = r.get("name") or ""
                disp_name = key
            if key not in buckets:
                buckets[key] = {
                    "aggregate_key": key,
                    "operator": None,
                    "name": disp_name,
                    "department": _merge_mode_label(r.get("department"), mode),
                    "group": disp_name if mode == "group" else "—",
                    "position": _merge_mode_label(r.get("position"), mode),
                    "current_specialty": disp_name if mode == "specialty" else "—",
                    "hr_category": disp_name if mode == "specialty" else "—",
                    "person_count": 0,
                    "is_management": False,
                    "qw_user_id": None,
                    "weights": dict(WEIGHT),
                    "transfer_penalty": TRANSFER_PENALTY,
                    "breakdown": {c: {
                        "sessions": 0, "people": 0,
                        "weight": WEIGHT.get(c, 1.0),
                        "coef": WEIGHT.get(c, 1.0),
                        "is_own": True,
                        "work_contrib": 0.0, "eff_contrib": 0.0,
                    } for c in CATS},
                    "is_virtual": False,
                    "_calc_note": {
                        "aggregate_mode": mode,
                        "sessions_merge": "聚合视图: 各人员 Σ 工作量分 / Σ 效率分",
                    },
                    **{k: 0 for k in SUM_KEYS},
                }
            b = buckets[key]
            b["person_count"] += 1
            for k in SUM_KEYS:
                b[k] = b[k] + (r.get(k) or 0)
            for c in CATS:
                rb = (r.get("breakdown") or {}).get(c) or {}
                bb = b["breakdown"][c]
                for k in ("sessions", "people", "work_contrib", "eff_contrib"):
                    bb[k] = bb[k] + (rb.get(k) or 0)
        # 后处理: round, rank 后续再打
        out_list = list(buckets.values())
        for b in out_list:
            b["work_score"] = round(float(b["work_score"] or 0), 2)
            b["eff_score"] = round(float(b["eff_score"] or 0), 2)
            for c in CATS:
                bb = b["breakdown"][c]
                bb["work_contrib"] = round(float(bb["work_contrib"]), 1)
                bb["eff_contrib"] = round(float(bb["eff_contrib"]), 1)
        return out_list

    def _merge_mode_label(val: str | None, mode: str) -> str:
        if mode == "all":
            return "—"
        return val or "—"

    # 先做企微筛选 (真人员工 + 虚拟 都筛一遍; 虚拟账号一般 qw_user_id 为空就被筛掉)
    if qw_only:
        out = _filter_qw_only(out)
        virtual_rows = _filter_qw_only(virtual_rows)

    # 真人员工 按 mode 聚合 (virtual_rows 不聚合, 原样保留在末尾)
    aggregate_mode = (aggregate_by or "person").lower()
    if aggregate_mode not in ("person", "specialty", "group", "all"):
        aggregate_mode = "person"
    out = _aggregate_records(out, aggregate_mode)

    out.sort(key=lambda x: -x["work_score"])
    out = out[: max(1, min(200, topn or 30))]
    for i, rec in enumerate(out, 1):
        rec["rank"] = i
    # 聚合模式（按专项/分组/全员）不追加虚拟账号行，只显示聚合后的真实数据汇总
    if aggregate_mode == "person":
        for rec in virtual_rows:
            rec["rank"] = None
            out.append(rec)
    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "qw_only": qw_only,
        "aggregate_by": aggregate_mode,
        "data": out,
    }


# ==============================================================================
# 退款挽回专项分析 (独立视图, 不冲抵接待效率)
#   业务逻辑:
#     1. 分母 = 退款专项组人员, 在周期内通过 cs_records 接待的退款类设备SN去重集合
#     2. 挽留失败 = 分母SN中, 出现在 refunds(线上退款单) 或 service_tickets(退款类工单) 中的SN
#     3. 挽留成功 = 分母SN - 挽留失败SN
#     4. 成功率 = 挽留成功 / 分母
#     5. 原因分布: 基于挽留失败SN对应的 refund_reason / service_item 聚合
#     6. 周/日趋势: 按周/日 拦截订单 + 成功率 走势
# ==============================================================================

# 注意: 口径原则 —— 分组(group_name)=行政/管理维度, 专项(specialty/hr_cat)=业务内容维度.
# 退款挽回专项只按"专项内容"判定, 不看管理分组 (避免同一管理组内不同专项被误圈).
# 之前的 REFUND_RECOVERY_GROUPS (基于组名) 已废弃, 统一只看 REFUND_RECOVERY_HR_CATS.
REFUND_RECOVERY_HR_CATS = ("退款挽回",)             # 判定条件: HR专项(=Person.specialty 映射)为"退款挽回"
SYS_OP_BLACKLIST_RR = ("sys", "system", "SYS", "System", "用户自助", "客户自助", "企微用户自助")

# 退款原因归一化映射 (兼容 refund_reason 文本 和 service_item 文本)
_REASON_NORMALIZE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("不想用", "不想要", "没用了", "不需要", "退订", "取消"), "不想用了"),
    (("充错", "充值错", "充多", "误充", "错充"), "充值错误"),
    (("设备", "坏了", "故障", "损坏", "不能用", "用不了", "死机", "卡机"), "设备故障"),
    (("网卡", "卡慢", "网速", "网络慢", "网慢", "没网", "无网", "没信号", "信号差", "掉线"), "网卡/卡无网络"),
    (("套餐", "流量", "升级", "涨价", "资费", "太贵"), "套餐问题(流量虚、涨价、升级)"),
    (("卡商", "渠道", "代理", "上游"), "卡商问题"),
]


def _normalize_refund_reason(raw: str | None) -> str:
    """把 refund_reason / service_item 原始文本 归一化到 7 大标准原因桶."""
    if not raw:
        return "其他(填写备注)"
    s = raw.strip()
    if not s:
        return "其他(填写备注)"
    low = s.lower()
    for kws, bucket in _REASON_NORMALIZE_RULES:
        if any(k.lower() in low for k in kws):
            return bucket
    return "其他(填写备注)"


def _is_refund_staff(grp: str | None, hr_cat: str | None) -> bool:
    """判定是否属于退款挽回专项组人员.

    口径原则: 分组(group_name)=行政/管理维度, 专项(hr_cat/specialty)=业务内容维度.
    只按"专项内容"判定, 不看管理分组 (避免同一管理组内不同专项被误圈).
    同时保留"套餐专项"优先排除, 防止其它分支误判.
    """
    if hr_cat == "套餐专项":
        return False
    if hr_cat and hr_cat in REFUND_RECOVERY_HR_CATS:
        return True
    return False


def refund_recovery_analysis(
    db: Session,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """退款挽回专项分析: 总体KPI + 原因分布 + 按人员明细 + 周趋势.

    返回结构:
      {
        date, date_end,
        kpi: { total_sn, success_sn, failed_sn, success_rate,
               abandon_cnt, fail_cnt, success_cnt, recharge_cnt,
               abandon_amount, fail_amount, success_amount, recharge_amount,
               actual_refund_amount, fee_ratio },
        reason_dist: [{ reason, order_cnt, ratio, success_rate, amount }],
        staff_detail: [{ operator, name, group, total_sn, success_sn, failed_sn, success_rate }],
        weekly_trend: [{ week_label, abandon_cnt, fail_cnt, success_cnt, total_cnt, success_rate }],
        daily_reason: [{ date, reason1_cnt, reason1_ratio, ... }],
      }
    """
    # ---- 1. 统一时间窗口 ----
    cur_s, cur_e, _, _, is_realtime, disp = _resolve_windows(date, start, end)
    ts = cur_s; te = cur_e
    d_iso = ts.astimezone(TZ).date().isoformat()
    d_end_iso = te.astimezone(TZ).date().isoformat()

    # 系统自动过滤 (和 today_staff_efficiency 保持一致)
    SYS_KW = ["系统自动", "自动发送", "自动回复", "自动推送", "自动补发", "自动触发", "自动生成"]
    def _desc_auto_filter(col):
        return ~and_(*[func.coalesce(col, "").ilike(f"%{k}%") for k in SYS_KW])

    # ---- HR 专项 映射 (2026-08-10 细分后版本, 与 today_staff_efficiency 保持一致) ----
    def _hr_map(hr_sp: str | None, pos: str | None) -> str | None:
        """人员主专项归一化映射
        返回: 专项名称 | "管理层" | None
        """
        if (pos or "") in ("主管", "组长", "副主管"):
            return "管理层"
        if not hr_sp:
            return None
        s = (hr_sp or "").strip()
        # 6个标准专项
        if s in ("咨询接待", "套餐专项", "退款处理", "退款挽回", "内部投诉", "外部投诉"):
            return s
        # 兼容旧数据模糊匹配
        if "外部投诉" in s: return "外部投诉"
        if "内部投诉" in s or "投诉" in s: return "内部投诉"
        if "退款挽回" in s: return "退款挽回"
        if "退款处理" in s or "退款" in s: return "退款处理"
        if "套餐" in s: return "套餐专项"
        if any(k in s for k in ("咨询", "接待", "综合问题", "其他问题")):
            return "咨询接待"
        return None

    # ---- 2. 先找 Person 维表: 哪些人 属于 退款专项组 ----
    #   注意: PersonAlias.alias = 业务表的 operator (如 "企微-叶珍凤"), 通过 person_id 关联到 Person (统一实体)
    from app.models.person import Person as P, PersonAlias as PA
    person_rows = db.execute(
        select(
            PA.alias.label("operator"),
            func.coalesce(P.display_name, PA.alias).label("display_name"),
            P.group_name, P.specialty, P.position, P.department,
        )
        .select_from(PA)
        .outerjoin(P, PA.person_id == P.id)
        .where(PA.is_system == False)  # 排除 sys/system 等系统自动别名
    ).all()
    refund_ops: dict[str, dict] = {}  # operator (alias) -> {name, group, specialty, position, dept}
    for pr in person_rows:
        op = pr.operator or ""
        if not op:
            continue
        hr_cat = _hr_map(pr.specialty, pr.position)
        if _is_refund_staff(pr.group_name, hr_cat):
            # 同一人员(同 person) 可能有多个 alias (企微-xxx, 店铺-xxx), 均加入 refund_ops 以便 cs_records 匹配
            refund_ops.setdefault(op, {
                "name": pr.display_name or op,
                "group": pr.group_name or "未分组",
                "specialty": pr.specialty,
                "position": pr.position,
                "dept": pr.department or "未设置",
            })

    if not refund_ops:
        # 兜底: 如果维表没配, 从实际数据里推 (做过退款类工单/退款单的人)
        guess_ops = set()
        for r in db.execute(select(Refund.operator).where(
            Refund.created_at_raw.isnot(None),
            Refund.created_at_raw >= ts, Refund.created_at_raw <= te,
            Refund.operator.isnot(None), Refund.operator.notin_(SYS_OP_BLACKLIST_RR),
        )).all():
            guess_ops.add(r.operator or "")
        for r in db.execute(select(ServiceTicket.handler).where(
            ServiceTicket.finish_time.isnot(None),
            ServiceTicket.finish_time >= ts, ServiceTicket.finish_time <= te,
            ServiceTicket.handler.isnot(None), ServiceTicket.handler.notin_(SYS_OP_BLACKLIST_RR),
            func.coalesce(ServiceTicket.service_category, "") == "退款",
        )).all():
            guess_ops.add(r.handler or "")
        for op in guess_ops:
            if not op or op in SYS_OP_BLACKLIST_RR:
                continue
            refund_ops.setdefault(op, {"name": op, "group": "未分组", "specialty": None, "position": None, "dept": "未设置"})

    # op_list = 退款挽回成员 (按用户最新口径:移除组长/主管强制失败规则,只有 属于退款专项组的人 才算"进入退款专员接待工作量")
    op_list = list(set(refund_ops.keys()))
    all_ops_meta: dict[str, dict] = {**refund_ops}
    if not op_list:
        return {
            "date": d_iso, "date_end": d_end_iso,
            "kpi": _empty_kpi(),
            "reason_dist": [],
            "staff_detail": [],
            "weekly_trend": [],
            "daily_reason": [],
            "daily_trend": [],
            "hourly_today": [],
            "_note": "当前时间窗口未识别到退款专项组人员, 请检查 Person 维表的 分组/专项 配置.",
        }

    # ---- 3. 分母: 退款挽回成员 在周期内 cs_records 接待的【真退款类】 SN 集合 (按 op 分组) ----
    #    新口径(2026-08-04 用户确认):
    #      - problem_category = '退款' (大类)
    #      - AND problem_type IN ('不想用了','充值错误','设备故障','服务原因') → 这4类=真的要退款,排除 37% 错标「网络问题」和 7464 条「其他」
    #      - AND operator 属于 退款专项组人员 (op_list) → 才叫"进入退款专员接待工作量"
    cs_refund_sn_sql = text(
        """
        SELECT c.operator AS op, NULLIF(TRIM(c.device_sn),'') AS sn,
               c.operation_time AS op_time
        FROM cs_records c
        WHERE c.operation_time IS NOT NULL
          AND c.operation_time >= :ts AND c.operation_time <= :te
          AND c.operator = ANY(:op_list)
          AND NOT (c.operator = ANY(:sys_bl))
          AND c.problem_category = '退款'
          AND COALESCE(c.problem_type,'') IN ('不想用了','充值错误','设备故障','服务原因')
          AND NOT (
              COALESCE(c.description,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
           OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
           OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
          )
        """
    )
    kw_params = {f"k{i+1}": f"%{k}%" for i, k in enumerate(SYS_KW)}
    cs_sn_rows = db.execute(
        cs_refund_sn_sql,
        {"ts": ts, "te": te, "op_list": op_list, "sys_bl": list(SYS_OP_BLACKLIST_RR), **kw_params},
    ).fetchall()

    # op -> set(sn)   (退款专项组成员 分母SN)
    op_host_sns: dict[str, set[str]] = {op: set() for op in op_list}
    for r in cs_sn_rows:
        if r.sn:
            op_host_sns.setdefault(r.op or "", set()).add(r.sn)

    # 全局分母SN (全部合并)
    global_host_sns: set[str] = set()
    for s in op_host_sns.values():
        global_host_sns.update(s)

    # ---- 4. 分子: 挽留失败 SN 集合 = 分母SN ∩ (refunds ∪ 退款工单) ----
    #   时间: 退款/工单 发生时间 在 [start, end + 7天] 窗口 (T+7 宽限, 用户2026-08-04确认)
    #   说明: 允许专员接待后最长7天走到退款审批(到账/工单结案)才判定为失败, 避免"假成功"
    #   注意: PostgreSQL ::text[] 类型转换 会与 SQLAlchemy :param 占位符冲突, 必须用 CAST
    import datetime as _dt2
    te7 = te + _dt2.timedelta(days=7)
    failed_sn_sql = text(
        """
        WITH host_sns AS (SELECT unnest(CAST(:sn_arr AS text[])) AS sn),
        refund_sn AS (
            SELECT DISTINCT NULLIF(TRIM(r.device_sn),'') AS sn,
                   'refund' AS src,
                   r.refund_reason AS reason,
                   COALESCE(r.actual_amount, r.refund_amount, 0) AS amount,
                   r.status AS status,
                   r.refund_method AS method
            FROM refunds r
            WHERE r.created_at_raw IS NOT NULL
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
              AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
              AND r.operator IS NOT NULL AND NOT (r.operator = ANY(:sys_bl))
              AND r.refund_method = '线上'
        ),
        ticket_sn AS (
            SELECT DISTINCT NULLIF(TRIM(st.device_sn),'') AS sn,
                   'ticket' AS src,
                   st.service_item AS reason,
                   COALESCE(st.refund_amount, st.estimated_amount, 0) AS amount,
                   st.status AS status,
                   st.service_category AS method
            FROM service_tickets st
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te7
              AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
              AND st.handler IS NOT NULL AND NOT (st.handler = ANY(:sys_bl))
              AND COALESCE(st.service_category,'') = '退款'
        ),
        all_fail AS (
            SELECT sn, src, reason, amount, status, method FROM refund_sn
            UNION ALL
            SELECT sn, src, reason, amount, status, method FROM ticket_sn
        )
        SELECT af.sn, af.src, af.reason, af.amount, af.status, af.method
        FROM all_fail af
        JOIN host_sns hs ON af.sn = hs.sn
        """
    )
    fail_rows = db.execute(
        failed_sn_sql,
        {"sn_arr": list(global_host_sns), "ts": ts, "te7": te7, "sys_bl": list(SYS_OP_BLACKLIST_RR)},
    ).fetchall()

    # 全局失败SN集合 + 明细 (按 SN 取金额最大那一条 作为该SN的归因)
    global_fail_sns: set[str] = set()
    sn_failed_meta: dict[str, dict] = {}
    for r in fail_rows:
        sn = r.sn or ""
        if not sn:
            continue
        global_fail_sns.add(sn)
        amt = float(r.amount or 0)
        prev = sn_failed_meta.get(sn)
        if prev is None or amt > float(prev.get("amount") or 0):
            sn_failed_meta[sn] = {
                "src": r.src, "reason": r.reason, "amount": amt,
                "status": r.status, "method": r.method,
            }

    # ---- 4.6 退款操作执行人统计 (同SN多人都算工作量, 不算入成功/失败分子) ----
    #   统计每个 operator 作为 refunds.operator / service_tickets.handler 处理的失败SN数
    #   注意: 这只是 "退款操作工作量" 独立字段, 不影响成功率计算的分子分母
    refund_op_sns: dict[str, set[str]] = {}   # operator alias -> set of 退款操作 SN
    refund_op_amt: dict[str, float] = {}     # operator alias -> 操作金额合计
    # 从 refunds 拉 operator
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(r.device_sn),'') AS sn,
               r.operator AS op,
               COALESCE(r.actual_amount, r.refund_amount, 0) AS amt
        FROM refunds r
        WHERE r.created_at_raw IS NOT NULL
          AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
          AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
          AND r.operator IS NOT NULL AND NOT (r.operator = ANY(:sys_bl))
          AND r.refund_method = '线上'
        """
    ), {"ts": ts, "te7": te7, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
        if not r.sn or not r.op:
            continue
        refund_op_sns.setdefault(r.op, set()).add(r.sn)
        refund_op_amt[r.op] = refund_op_amt.get(r.op, 0.0) + float(r.amt or 0)
    # 从 service_tickets 拉 handler
    for r in db.execute(text(
        """
        SELECT NULLIF(TRIM(st.device_sn),'') AS sn,
               st.handler AS op,
               COALESCE(st.refund_amount, st.estimated_amount, 0) AS amt
        FROM service_tickets st
        WHERE st.finish_time IS NOT NULL
          AND st.finish_time >= :ts AND st.finish_time <= :te7
          AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
          AND st.handler IS NOT NULL AND NOT (st.handler = ANY(:sys_bl))
          AND COALESCE(st.service_category,'') = '退款'
        """
    ), {"ts": ts, "te7": te7, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall():
        if not r.sn or not r.op:
            continue
        refund_op_sns.setdefault(r.op, set()).add(r.sn)
        refund_op_amt[r.op] = refund_op_amt.get(r.op, 0.0) + float(r.amt or 0)

    # ---- 5. 按 op 拆分 成功/失败 + 退款操作工作量 (多人同SN各算各的) ----
    #   范围: refund_ops(退款专项组成员) + 所有 refund_op_sns 中出现的执行人
    staff_operators: dict[str, dict] = dict(all_ops_meta)  # 先加入 CS 接待侧的人员 meta
    for op in refund_op_sns.keys():
        if op not in staff_operators and op not in SYS_OP_BLACKLIST_RR:
            # 纯退款执行人 (没出现在 CS 接待侧) → 从维表补 meta
            staff_operators[op] = {"name": op, "group": "退款执行", "specialty": None, "position": "退款执行人", "dept": "售后"}
    # 再精确补一次维表信息(有可能维表里有但之前没圈进来)
    for pr in person_rows:
        op = pr.operator or ""
        if op and op in staff_operators:
            info = staff_operators[op]
            if not info.get("group") or info.get("group") == "退款执行":
                info["group"] = pr.group_name or info.get("group") or "未分组"
            if not info.get("position") or info.get("position") == "退款执行人":
                info["position"] = pr.position or info.get("position")
            info["dept"] = pr.department or info.get("dept") or "未设置"
            info["name"] = pr.display_name or op

    staff_detail = []
    for op, info in staff_operators.items():
        # CS 接待工作量: 接待的真退款类SN
        cs_host = op_host_sns.get(op, set())
        cs_failed = cs_host & global_fail_sns
        cs_success = cs_host - global_fail_sns
        total_cs = len(cs_host)
        fail_cs = len(cs_failed)
        succ_cs = len(cs_success)

        # 退款操作工作量: 独立字段, 单独统计 (不重复计入 success/failed)
        ro_sns = refund_op_sns.get(op, set())
        ro_cnt = len(ro_sns)
        ro_amt = refund_op_amt.get(op, 0.0)

        # 成功率口径: 仅基于 CS 接待侧 (退款专项组人员 的接待成功/接待失败)
        # 如果纯退款执行人(无 CS 接待), 成功率记为 None(空) 避免误导
        total_for_rate = total_cs
        rate = (succ_cs / total_for_rate * 100) if total_for_rate else None

        staff_detail.append({
            "operator": op,
            "name": info.get("name") or op,
            "group": info.get("group") or "未分组",
            "department": info.get("dept") or "未设置",
            "position": info.get("position") or "",
            "total_sn": total_cs,           # CS 接待SN数 (新口径:真退款类问题 + 退款专员接待)
            "success_sn": succ_cs,          # 接待成功SN数
            "failed_sn": fail_cs,           # 接待失败SN数
            "success_rate": round(rate, 2) if rate is not None else None,
            "refund_op_cnt": ro_cnt,        # 退款操作SN数: 作为 refunds.operator 或 service_tickets.handler 处理过的 SN
            "refund_op_amount": round(ro_amt, 2),  # 退款操作金额合计
            "_is_refund_staff": (op in refund_ops),
        })
    staff_detail.sort(key=lambda x: (
        -(x.get("total_sn") or 0),            # 主排序: CS 接待SN数降序
        -(x.get("refund_op_cnt") or 0),       # 次排序: 退款操作SN数降序
        -(x.get("success_rate") or -1),       # 再次: 成功率降序
    ))

    # ---- 6. 总体 KPI ----
    total_host = len(global_host_sns)
    total_fail = len(global_fail_sns)
    total_success = total_host - total_fail
    overall_rate = (total_success / total_host * 100) if total_host else 0.0

    # 金额拆分: 参考用户 Excel 分类
    #   失败(拦截失败): status 已退款 / 线上 退款成功 → fail_amount
    #   放弃(系统清退): status 申请中 / 无需退款 / sys操作 → abandon_amount
    #   退充(正常退充): remark/原因含"退充" → recharge_amount
    #   成功(拦截成功): 成功SN对应的估算金额 = 对应失败SN的平均金额 * 成功数 (粗略估算)
    abandon_cnt = 0; abandon_amount = 0.0
    fail_cnt = 0; fail_amount = 0.0
    recharge_cnt = 0; recharge_amount = 0.0
    for sn, meta in sn_failed_meta.items():
        status = (meta.get("status") or "").strip()
        reason = (meta.get("reason") or "").lower()
        amt = float(meta.get("amount") or 0)
        is_recharge = ("退充" in (meta.get("reason") or "")) or ("recharge" in reason)
        is_abandon = (meta.get("src") == "ticket" and status in ("待处理",)) or (status in ("无需退款", "申请中")) or (meta.get("method") == "线下")
        if is_recharge:
            recharge_cnt += 1; recharge_amount += amt
        elif is_abandon:
            abandon_cnt += 1; abandon_amount += amt
        else:
            fail_cnt += 1; fail_amount += amt

    success_cnt = max(0, total_success)  # 拦截成功单数 = 成功SN数
    # 成功金额 = 0 (业务口径: 挽回成功的设备没有进入退款流程, 无实际退款工单可对应金额, 暂不做估算)
    success_amount = 0.0

    actual_refund = fail_amount + recharge_amount  # 实际退款 = 失败金额 + 退充金额
    # 费占比改为单量口径: 成功单量 / (成功单量 + 实际退款单量(fail+recharge))
    # 金额口径的成功金额无法准确核算, 故用单量近似表达拦截比例
    actual_refund_cnt = fail_cnt + recharge_cnt
    denom_fee_cnt = success_cnt + actual_refund_cnt
    fee_ratio = (success_cnt / denom_fee_cnt * 100) if denom_fee_cnt else 0.0

    # 先占位: reason_order 将在第7部分原因分布生成后回填
    reason_order_placeholder: list[str] = []

    kpi = {
        # SN维度 (核心分母/分子)
        "total_sn": total_host,
        "success_sn": total_success,
        "failed_sn": total_fail,
        "success_rate": round(overall_rate, 2),
        # 单量维度 (对齐截图 放弃/失败/成功/退充 4类)
        "abandon_cnt": abandon_cnt,
        "fail_cnt": fail_cnt,
        "success_cnt": success_cnt,
        "recharge_cnt": recharge_cnt,
        "target_total_cnt": abandon_cnt + fail_cnt + success_cnt + recharge_cnt,
        # 金额维度 (单位: 元)
        # 注意: success_amount 恒为 0 (挽回成功无退款工单, 金额无法准确核算)
        "abandon_amount": round(abandon_amount, 2),
        "fail_amount": round(fail_amount, 2),
        "success_amount": round(success_amount, 2),
        "recharge_amount": round(recharge_amount, 2),
        "target_total_amount": round(abandon_amount + fail_amount + recharge_amount, 2),  # 不含 success_amount (为0)
        "actual_refund_amount": round(actual_refund, 2),
        "fee_ratio": round(fee_ratio, 2),  # 单量口径拦截比例, 非金额口径
        "_calc_note": {
            "denominator": "退款专项组成员(Person.specialty=退款挽回/叶珍凤组/朱海洋组) 接待的 problem_category='退款' AND problem_type IN (不想用了/充值错误/设备故障/服务原因) → 真要退款的SN去重总数 (2026-08-04 新口径, 排除 37% 错标的「网络问题」和 「其他」 7464条)",
            "failed_rule": "分母SN 中, 在 [start, end+7天] T+7 窗口内 出现在 refunds(线上退款单) 或 退款工单(service_category=退款) 中 → 挽留失败. (T+7 宽限避免接待后才走完退款审批的SN被误判为成功)",
            "success_rule": "「分母SN」 − 「失败SN」 → 挽留成功 (即退款专员接待过但最终没走到退款流程, 被沟通拦截下来)",
            "leader_rule_removed": "已移除: 原规则「组长/主管接待SN强制失败」(用户2026-08-04确认移除,仅按SN是否真正进入退款工单判定成败)",
            "multi_user_rule": "工作负荷/绩效层面: 同个SN 允许 A(退款接待专员) + B(退款操作执行人) 各算一次. 接待侧计入 total_sn/success_sn/failed_sn; 退款操作侧(即 refunds.operator/service_tickets.handler) 单独计入 staff_detail.refund_op_cnt / refund_op_amount, 不重复计入成功率",
            "success_amount_rule": "成功金额暂不核算 (挽留成功的设备未进入退款流程, 无实际退款工单金额可对应)",
            "fee_ratio_rule": "费占比(单量口径) = 成功单数 / (成功单数 + 实际退款单数(失败+退充)) · 参考目标 37%+",
            "staff_scope": f"退款专项 (Person.specialty∈{REFUND_RECOVERY_HR_CATS} OR 组名匹配 叶珍凤组/朱海洋组) + 所有退款操作执行人; 同alias多身份时优先按退款专项成员计; 已排除系统别名(system/机器人等)",
            "reason_order": reason_order_placeholder,
        },
    }

    # ---- 7. 退款原因分布 (挽留失败SN → 归一化原因) ----
    reason_bucket: dict[str, dict] = {}
    for sn in global_fail_sns:
        raw = sn_failed_meta.get(sn, {}).get("reason")
        bucket = _normalize_refund_reason(raw)
        amt = float(sn_failed_meta.get(sn, {}).get("amount") or 0)
        b = reason_bucket.setdefault(bucket, {"order_cnt": 0, "amount": 0.0})
        b["order_cnt"] += 1
        b["amount"] += amt

    reason_dist = []
    for reason, info in reason_bucket.items():
        cnt = info["order_cnt"]
        ratio = (cnt / total_fail * 100) if total_fail else 0.0
        reason_dist.append({
            "reason": reason,
            "order_cnt": cnt,
            "ratio": round(ratio, 2),
            "amount": round(info["amount"], 2),
        })
    reason_dist.sort(key=lambda x: -x["order_cnt"])

    # 回填 kpi._calc_note.reason_order (按 单量降序 的原因列表, 供 日期×原因 矩阵前端使用)
    kpi["_calc_note"]["reason_order"] = [r["reason"] for r in reason_dist]

    # ---- 8. 周趋势: 最近 8 周 拦截订单汇总 (新口径+T+7) ----
    now_sh = _dt.datetime.now(TZ)
    weekly_trend = []
    for wi in range(7, -1, -1):
        wk_end = (now_sh - _dt.timedelta(weeks=wi, days=now_sh.weekday())).replace(hour=23, minute=59, second=59, microsecond=0)
        wk_start = wk_end - _dt.timedelta(days=6, hours=23, minutes=59, seconds=59)
        wk_start_utc = wk_start.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        wk_end_utc = wk_end.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        wk_end7_utc = wk_end_utc + _dt.timedelta(days=7)  # T+7
        week_label = f"{(now_sh.isocalendar()[0])}-W{(now_sh - _dt.timedelta(weeks=wi)).isocalendar()[1]}"

        # 当周 退款专员 cs_record 接待【真退款类】 SN (新口径)
        wk_cs = db.execute(text(
            """
            SELECT DISTINCT NULLIF(TRIM(c.device_sn),'') AS sn
            FROM cs_records c
            WHERE c.operation_time IS NOT NULL
              AND c.operation_time >= :ts AND c.operation_time <= :te
              AND c.operator = ANY(:op_list)
              AND NOT (c.operator = ANY(:sys_bl))
              AND c.problem_category = '退款'
              AND COALESCE(c.problem_type,'') IN ('不想用了','充值错误','设备故障','服务原因')
              AND NOT (
                  COALESCE(c.description,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
               OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
               OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
              )
            """
        ), {"ts": wk_start_utc, "te": wk_end_utc, "op_list": op_list, "sys_bl": list(SYS_OP_BLACKLIST_RR), **kw_params}).fetchall()
        wk_host: set[str] = set(r.sn for r in wk_cs if r.sn)

        # 当周 失败SN (T+7 窗口)
        wk_fail_rows = db.execute(text(
            """
            SELECT DISTINCT NULLIF(TRIM(r.device_sn),'') AS sn,
                   'refund' AS src, r.refund_reason AS reason,
                   COALESCE(r.actual_amount, r.refund_amount, 0) AS amount,
                   r.status AS status, r.refund_method AS method
            FROM refunds r
            WHERE r.created_at_raw IS NOT NULL
              AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
              AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
              AND r.operator IS NOT NULL AND NOT (r.operator = ANY(:sys_bl))
              AND r.refund_method = '线上'
            UNION ALL
            SELECT DISTINCT NULLIF(TRIM(st.device_sn),'') AS sn,
                   'ticket' AS src, st.service_item AS reason,
                   COALESCE(st.refund_amount, st.estimated_amount, 0) AS amount,
                   st.status AS status, st.service_category AS method
            FROM service_tickets st
            WHERE st.finish_time IS NOT NULL
              AND st.finish_time >= :ts AND st.finish_time <= :te7
              AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
              AND st.handler IS NOT NULL AND NOT (st.handler = ANY(:sys_bl))
              AND COALESCE(st.service_category,'') = '退款'
            """
        ), {"ts": wk_start_utc, "te7": wk_end7_utc, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall()
        wk_fail_map: dict[str, dict] = {}
        for r in wk_fail_rows:
            sn = r.sn or ""
            if not sn: continue
            amt = float(r.amount or 0)
            prev = wk_fail_map.get(sn)
            if prev is None or amt > float(prev.get("amount") or 0):
                wk_fail_map[sn] = {"reason": r.reason, "amount": amt, "status": r.status, "method": r.method, "src": r.src}
        wk_fail_sns = set(wk_fail_map.keys()) & wk_host

        wk_total = len(wk_host)
        wk_fail_n = len(wk_fail_sns)
        wk_succ_n = wk_total - wk_fail_n
        wk_rate = (wk_succ_n / wk_total * 100) if wk_total else 0.0
        wk_ab = 0; wk_fl = 0; wk_rc = 0
        for sn in wk_fail_sns:
            m = wk_fail_map[sn]
            st = (m.get("status") or "").strip()
            is_rc = "退充" in (m.get("reason") or "")
            is_ab = (m.get("src") == "ticket" and st == "待处理") or (st in ("无需退款", "申请中")) or (m.get("method") == "线下")
            if is_rc: wk_rc += 1
            elif is_ab: wk_ab += 1
            else: wk_fl += 1

        weekly_trend.append({
            "week_label": week_label,
            "date_range": [wk_start.strftime("%m/%d"), wk_end.strftime("%m/%d")],
            "abandon_cnt": wk_ab,
            "fail_cnt": wk_fl,
            "success_cnt": wk_succ_n,
            "recharge_cnt": wk_rc,
            "total_cnt": wk_ab + wk_fl + wk_succ_n + wk_rc,
            "success_rate": round(wk_rate, 2),
        })

    # ---- 9. 日×原因 明细 (当前时间窗口内, 按天 × 原因 交叉) (新口径) ----
    daily_reason = []
    if global_fail_sns:
        sn_first_day_sql = text(
            """
            SELECT sn, MIN(day) AS first_day
            FROM (
                SELECT NULLIF(TRIM(r.device_sn),'') AS sn,
                       DATE(r.created_at_raw AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai') AS day
                FROM refunds r
                WHERE r.created_at_raw IS NOT NULL
                  AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
                  AND NULLIF(TRIM(r.device_sn),'') = ANY(:sn_arr)
                  AND r.refund_method = '线上'
                UNION ALL
                SELECT NULLIF(TRIM(st.device_sn),'') AS sn,
                       DATE(st.finish_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai') AS day
                FROM service_tickets st
                WHERE st.finish_time IS NOT NULL
                  AND st.finish_time >= :ts AND st.finish_time <= :te7
                  AND NULLIF(TRIM(st.device_sn),'') = ANY(:sn_arr)
                  AND COALESCE(st.service_category,'') = '退款'
            ) x
            WHERE sn IS NOT NULL
            GROUP BY sn
            """
        )
        day_rows = db.execute(
            sn_first_day_sql,
            {"ts": ts, "te7": te7, "sn_arr": list(global_fail_sns)},
        ).fetchall()
        sn_day: dict[str, str] = {r.sn: str(r.first_day) for r in day_rows if r.sn and r.first_day}

        # 日 -> 原因 -> cnt
        day_reason_bucket: dict[str, dict[str, int]] = {}
        all_reasons_seen: set[str] = set()
        for sn in global_fail_sns:
            d = sn_day.get(sn)
            if not d:
                continue
            raw = sn_failed_meta.get(sn, {}).get("reason")
            b = _normalize_refund_reason(raw)
            all_reasons_seen.add(b)
            inner = day_reason_bucket.setdefault(d, {})
            inner[b] = inner.get(b, 0) + 1

        ordered_reasons = [r["reason"] for r in reason_dist]
        for extra in sorted(all_reasons_seen - set(ordered_reasons)):
            ordered_reasons.append(extra)
        ordered_days = sorted(day_reason_bucket.keys())

        daily_reason = []
        for d in ordered_days:
            total_d = sum(day_reason_bucket.get(d, {}).values())
            row = {"date": d, "_total": total_d}
            for reason in ordered_reasons:
                c = day_reason_bucket.get(d, {}).get(reason, 0)
                row[f"{reason}_cnt"] = c
                row[f"{reason}_ratio"] = round(c / total_d * 100, 2) if total_d else 0.0
            daily_reason.append(row)
        kpi["_calc_note"]["reason_order"] = ordered_reasons

    # ---- 10. 按日 挽回趋势 (近30天 或 当前时间窗口 范围内 日粒度) (新口径 新增) ----
    daily_trend: list[dict] = []
    try:
        # 生成每日日期序列
        start_day = ts.astimezone(TZ).date() if hasattr(ts, 'astimezone') else ts.date()
        end_day = te.astimezone(TZ).date() if hasattr(te, 'astimezone') else te.date()
        # 强制至少近30天用于 今日视图观察
        min_start = _dt.date.today() - _dt.timedelta(days=29)
        plot_start = min(start_day, min_start)
        dates_list: list[_dt.date] = []
        cur_d = plot_start
        while cur_d <= end_day:
            dates_list.append(cur_d)
            cur_d += _dt.timedelta(days=1)
        if dates_list:
            ts0 = _dt.datetime.combine(plot_start, _dt.time.min).replace(tzinfo=TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None)
            te0 = _dt.datetime.combine(end_day, _dt.time.max).replace(tzinfo=TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None)
            te0_7 = te0 + _dt.timedelta(days=7)
            # 日粒度 分母SN (按天)
            day_host_rows = db.execute(text(
                """
                SELECT DATE(c.operation_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai') AS d,
                       NULLIF(TRIM(c.device_sn),'') AS sn
                FROM cs_records c
                WHERE c.operation_time IS NOT NULL
                  AND c.operation_time >= :ts AND c.operation_time <= :te
                  AND c.operator = ANY(:op_list)
                  AND NOT (c.operator = ANY(:sys_bl))
                  AND c.problem_category = '退款'
                  AND COALESCE(c.problem_type,'') IN ('不想用了','充值错误','设备故障','服务原因')
                  AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
                  AND NOT (
                      COALESCE(c.description,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
                   OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
                   OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
                  )
                """
            ), {"ts": ts0, "te": te0, "op_list": op_list, "sys_bl": list(SYS_OP_BLACKLIST_RR), **kw_params}).fetchall()
            day_to_host: dict[str, set[str]] = {}
            for r in day_host_rows:
                if r.d is None or not r.sn: continue
                key = str(r.d)
                day_to_host.setdefault(key, set()).add(r.sn)
            # 日粒度 失败SN (T+7)
            day_fail_rows = db.execute(text(
                """
                SELECT NULLIF(TRIM(r.device_sn),'') AS sn
                FROM refunds r
                WHERE r.created_at_raw IS NOT NULL
                  AND r.created_at_raw >= :ts AND r.created_at_raw <= :te7
                  AND r.operator IS NOT NULL AND NOT (r.operator = ANY(:sys_bl))
                  AND r.refund_method = '线上'
                  AND NULLIF(TRIM(r.device_sn),'') IS NOT NULL
                UNION
                SELECT NULLIF(TRIM(st.device_sn),'') AS sn
                FROM service_tickets st
                WHERE st.finish_time IS NOT NULL
                  AND st.finish_time >= :ts AND st.finish_time <= :te7
                  AND st.handler IS NOT NULL AND NOT (st.handler = ANY(:sys_bl))
                  AND COALESCE(st.service_category,'') = '退款'
                  AND NULLIF(TRIM(st.device_sn),'') IS NOT NULL
                """
            ), {"ts": ts0, "te7": te0_7, "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall()
            fail_sn_set = set(r.sn for r in day_fail_rows if r.sn)
            daily_trend = []
            for d_obj in dates_list:
                key = d_obj.isoformat()
                h = day_to_host.get(key, set())
                f = h & fail_sn_set
                total = len(h)
                fail_n = len(f)
                succ_n = total - fail_n
                rate = round((succ_n / total * 100), 2) if total else 0.0
                daily_trend.append({
                    "date": key,
                    "total_sn": total,
                    "success_sn": succ_n,
                    "failed_sn": fail_n,
                    "success_rate": rate,
                })
    except Exception as _daily_e:
        logger.warning(f"退款挽回 按日趋势 计算异常: {_daily_e}")
        daily_trend = []

    # ---- 11. 今日 按小时 挽回趋势 (00:00~当前) (新口径 新增) ----
    hourly_today: list[dict] = []
    try:
        today_sh = _dt.datetime.now(TZ).date()
        ts_today = _dt.datetime.combine(today_sh, _dt.time.min).replace(tzinfo=TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None)
        te_today = _dt.datetime.now(TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None)
        # 今日 分母SN (按小时)
        hr_rows = db.execute(text(
            """
            SELECT EXTRACT(HOUR FROM c.operation_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai') AS hh,
                   NULLIF(TRIM(c.device_sn),'') AS sn
            FROM cs_records c
            WHERE c.operation_time IS NOT NULL
              AND c.operation_time >= :ts AND c.operation_time <= :te
              AND c.operator = ANY(:op_list)
              AND NOT (c.operator = ANY(:sys_bl))
              AND c.problem_category = '退款'
              AND COALESCE(c.problem_type,'') IN ('不想用了','充值错误','设备故障','服务原因')
              AND NULLIF(TRIM(c.device_sn),'') IS NOT NULL
              AND NOT (
                  COALESCE(c.description,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
               OR COALESCE(c.execution_result,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
               OR COALESCE(c.solution,'') ILIKE ANY(ARRAY[:k1,:k2,:k3,:k4,:k5,:k6,:k7])
              )
            """
        ), {"ts": ts_today, "te": te_today, "op_list": op_list, "sys_bl": list(SYS_OP_BLACKLIST_RR), **kw_params}).fetchall()
        hr_to_host: dict[int, set[str]] = {}
        for r in hr_rows:
            if r.hh is None or not r.sn: continue
            h = int(float(r.hh))
            hr_to_host.setdefault(h, set()).add(r.sn)
        # 今日 失败SN (所有今日接待的分母SN中, 有任何退款记录 = 今日判断失败, T+7宽限会导致今日数据部分还没出来, 但对图表走势无影响)
        today_all_sns = set()
        for s in hr_to_host.values():
            today_all_sns.update(s)
        today_fail_sns: set[str] = set()
        if today_all_sns:
            tfr = db.execute(text(
                """
                WITH arr AS (SELECT unnest(CAST(:sn_arr AS text[])) AS sn)
                SELECT DISTINCT NULLIF(TRIM(r.device_sn),'') AS sn FROM refunds r JOIN arr a ON NULLIF(TRIM(r.device_sn),'') = a.sn
                 WHERE r.refund_method = '线上' AND r.operator IS NOT NULL AND NOT (r.operator = ANY(:sys_bl))
                UNION
                SELECT DISTINCT NULLIF(TRIM(st.device_sn),'') AS sn FROM service_tickets st JOIN arr a ON NULLIF(TRIM(st.device_sn),'') = a.sn
                 WHERE COALESCE(st.service_category,'') = '退款' AND st.handler IS NOT NULL AND NOT (st.handler = ANY(:sys_bl))
                """
            ), {"sn_arr": list(today_all_sns), "sys_bl": list(SYS_OP_BLACKLIST_RR)}).fetchall()
            today_fail_sns = set(r.sn for r in tfr if r.sn)
        max_hh = _dt.datetime.now(TZ).hour
        hourly_today = []
        for h in range(0, max_hh + 1):
            hh_set = hr_to_host.get(h, set())
            h_fail = hh_set & today_fail_sns
            total = len(hh_set)
            fail_n = len(h_fail)
            hourly_today.append({
                "hour": f"{h:02d}",
                "total_sn": total,
                "success_sn": total - fail_n,
                "failed_sn": fail_n,
            })
    except Exception as _hourly_e:
        logger.warning(f"退款挽回 今日按小时趋势 计算异常: {_hourly_e}")
        hourly_today = []

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        "kpi": kpi,
        "reason_dist": reason_dist,
        "staff_detail": staff_detail,
        "weekly_trend": weekly_trend,
        "daily_reason": daily_reason,
        "daily_trend": daily_trend,
        "hourly_today": hourly_today,
        # 2026-08-09 追加: 数据核查调试信息 (前端不展示, 便于有疑问时在接口响应中直接核对)
        "_debug": {
            "refund_op_count": len(refund_ops),
            "refund_op_list_sample": list(refund_ops.keys())[:20],
            "cs_sn_row_count": len(cs_sn_rows),
            "global_host_sn_count": len(global_host_sns),
            "global_fail_sn_count": len(global_fail_sns),
            "fail_refund_sn_cnt": sum(1 for v in sn_failed_meta.values() if v.get("src") == "refund"),
            "fail_ticket_sn_cnt": sum(1 for v in sn_failed_meta.values() if v.get("src") == "ticket"),
            "sys_keywords_used": SYS_KW,
            "sys_blacklist_ops": list(SYS_OP_BLACKLIST_RR),
            "problem_type_filter": ['不想用了', '充值错误', '设备故障', '服务原因'],
        },
    }


def _empty_kpi() -> dict:
    return {
        "total_sn": 0, "success_sn": 0, "failed_sn": 0, "success_rate": 0.0,
        "abandon_cnt": 0, "fail_cnt": 0, "success_cnt": 0, "recharge_cnt": 0, "target_total_cnt": 0,
        "abandon_amount": 0.0, "fail_amount": 0.0, "success_amount": 0.0, "recharge_amount": 0.0,
        "target_total_amount": 0.0, "actual_refund_amount": 0.0, "fee_ratio": 0.0,
        "_calc_note": {},
    }


# ==============================================================
# 退款挽回 5 个 AnalysisView HANDLERS (对应 analysis_engine.py 分派机制)
# 与 urge_analysis.HANDLERS 完全相同的注册模式:view.code 命中则调用自定义实现
# ==============================================================
from typing import Callable as _Callable

_PARAMS_T = dict | None
_RET_T = dict


def _rr_parse_params(params: _PARAMS_T) -> tuple[str | None, str | None]:
    """从视图执行参数解析 start/end (兼容 AnalysisView 约定)"""
    if not params:
        return None, None
    return params.get("start") or params.get("date_start"), params.get("end") or params.get("date_end")


def _view_rr_kpi(db: Session, params: _PARAMS_T) -> _RET_T:
    """退款挽回 KPI: 核心4指标 + 费占比 (table 格式,供 ViewItemCard 展示)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    k = data.get("kpi") or _empty_kpi()
    # 行=指标, 列=名称/数值/说明
    rows = [
        {"指标": "总接待SN (分母)", "数值": int(k.get("total_sn") or 0), "单位": "个",
         "说明": "退款专员接待 + 真退款类型 (4类) 的去重SN"},
        {"指标": "挽留成功SN", "数值": int(k.get("success_sn") or 0), "单位": "个",
         "说明": "分母SN 中 最终未进入退款工单/退款单 (被拦截)"},
        {"指标": "挽留失败SN", "数值": int(k.get("failed_sn") or 0), "单位": "个",
         "说明": "分母SN 中 T+7 窗口内 最终进入了退款流程"},
        {"指标": "挽回成功率", "数值": f"{round(float(k.get('success_rate') or 0), 1)}%", "单位": "%",
         "说明": "成功SN / 分母SN"},
        {"指标": "拦截费占比", "数值": f"{round(float(k.get('fee_ratio') or 0), 1)}%", "单位": "%",
         "说明": "成功单量/(成功单量+实际退款单量) 参考目标 37%+"},
        {"指标": "实际退款金额", "数值": round(float(k.get("actual_refund_amount") or 0), 2), "单位": "元",
         "说明": "失败金额 + 退充金额"},
    ]
    return {
        "chart_type": "table",
        "data": {
            "columns": [
                {"field": "指标", "title": "指标"},
                {"field": "数值", "title": "数值"},
                {"field": "单位", "title": "单位"},
                {"field": "说明", "title": "口径说明"},
            ],
            "rows": rows,
            "meta": {"_source": "refund_recovery", "date": data.get("date"), "date_end": data.get("date_end")},
        },
    }


def _view_rr_daily_trend(db: Session, params: _PARAMS_T) -> _RET_T:
    """近30天 挽回成功率 (线) + 接待量 (柱) """
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    tr = data.get("daily_trend") or []
    x_axis = [r["date"][5:] for r in tr]  # MM-DD
    series = [
        {"name": "总接待SN", "type": "bar", "data": [r.get("total_sn") or 0 for r in tr],
         "itemStyle": {"color": "#93c5fd"}, "barMaxWidth": 14},
        {"name": "挽回成功SN", "type": "bar", "stack": None, "data": [r.get("success_sn") or 0 for r in tr],
         "itemStyle": {"color": "#4ade80"}, "barMaxWidth": 14},
        {"name": "挽回失败SN", "type": "bar", "stack": None, "data": [r.get("failed_sn") or 0 for r in tr],
         "itemStyle": {"color": "#f87171"}, "barMaxWidth": 14},
        {"name": "挽回成功率", "type": "line", "yAxisIndex": 1, "smooth": True, "symbolSize": 6,
         "data": [r.get("success_rate") or 0 for r in tr],
         "itemStyle": {"color": "#059669"}, "lineStyle": {"width": 3, "color": "#059669"}},
    ]
    return {
        "chart_type": "line",
        "data": {
            "title": "退款挽回 按日趋势 (近30天)",
            "x_axis": x_axis,
            "series": series,
            "legend": {"data": [s["name"] for s in series]},
            "yAxis": [
                {"type": "value", "name": "SN个数"},
                {"type": "value", "name": "成功率%", "min": 0, "max": 100, "axisLabel": {"formatter": "{value}%"}},
            ],
        },
    }


def _view_rr_hourly_today(db: Session, params: _PARAMS_T) -> _RET_T:
    """今日 按小时 接待SN (柱) + 成功率(线)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    hl = data.get("hourly_today") or []
    x_axis = [r["hour"] for r in hl]
    total = [r.get("total_sn") or 0 for r in hl]
    succ = [r.get("success_sn") or 0 for r in hl]
    fail = [r.get("failed_sn") or 0 for r in hl]
    rates = []
    for r in hl:
        t = r.get("total_sn") or 0
        rates.append(round((r.get("success_sn") or 0) / t * 100, 1) if t else 0)
    series = [
        {"name": "总接待SN", "type": "bar", "data": total, "itemStyle": {"color": "#bfdbfe"}, "barMaxWidth": 18},
        {"name": "挽留成功SN", "type": "bar", "data": succ, "itemStyle": {"color": "#34d399"}, "barMaxWidth": 18},
        {"name": "挽留失败SN", "type": "bar", "data": fail, "itemStyle": {"color": "#fca5a5"}, "barMaxWidth": 18},
        {"name": "成功率%", "type": "line", "yAxisIndex": 1, "smooth": True, "symbolSize": 7,
         "data": rates, "itemStyle": {"color": "#047857"}, "lineStyle": {"width": 3, "color": "#047857"}},
    ]
    return {
        "chart_type": "bar",
        "data": {
            "title": "今日 按小时 退款挽回趋势 (00:00 ~ 当前)",
            "x_axis": x_axis,
            "series": series,
            "legend": {"data": [s["name"] for s in series]},
            "yAxis": [
                {"type": "value", "name": "SN个数"},
                {"type": "value", "name": "成功率%", "min": 0, "max": 100, "axisLabel": {"formatter": "{value}%"}},
            ],
        },
    }


def _view_rr_operator_rank(db: Session, params: _PARAMS_T) -> _RET_T:
    """退款挽回 按人员明细排名 (表格)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    sd = data.get("staff_detail") or []
    rows = []
    for i, r in enumerate(sd):
        rate = r.get("success_rate")
        rate_txt = "—" if rate is None else (f"{round(float(rate), 1)}%")
        rows.append({
            "排名": i + 1,
            "姓名": r.get("name") or r.get("operator") or "-",
            "组别": r.get("group") or "未分组",
            "岗位": r.get("position") or "-",
            "接待SN": int(r.get("total_sn") or 0),
            "成功SN": int(r.get("success_sn") or 0),
            "失败SN": int(r.get("failed_sn") or 0),
            "成功率": rate_txt,
            "退款操作SN": int(r.get("refund_op_cnt") or 0),
            "退款操作金额": round(float(r.get("refund_op_amount") or 0), 2),
        })
    cols = [
        {"field": "排名", "title": "排名", "width": 60},
        {"field": "姓名", "title": "姓名", "width": 120},
        {"field": "组别", "title": "组别", "width": 120},
        {"field": "岗位", "title": "岗位", "width": 100},
        {"field": "接待SN", "title": "接待SN(分母)"},
        {"field": "成功SN", "title": "成功SN"},
        {"field": "失败SN", "title": "失败SN"},
        {"field": "成功率", "title": "成功率"},
        {"field": "退款操作SN", "title": "退款操作SN"},
        {"field": "退款操作金额", "title": "退款操作金额(元)"},
    ]
    return {
        "chart_type": "table",
        "data": {"columns": cols, "rows": rows,
                 "meta": {"_source": "refund_recovery_staff"}},
    }


def _view_rr_reason_dist(db: Session, params: _PARAMS_T) -> _RET_T:
    """挽回失败SN 原因分布 (柱+占比)"""
    s, e = _rr_parse_params(params)
    data = refund_recovery_analysis(db, start=s, end=e)
    rd = data.get("reason_dist") or []
    x_axis = [r.get("reason") or "未知" for r in rd]
    pal = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#6366f1", "#ec4899", "#0ea5e9"]
    bars_data = []
    for idx in range(len(rd)):
        bars_data.append({
            "value": int(rd[idx].get("order_cnt") or 0),
            "itemStyle": {"color": pal[idx % len(pal)], "borderRadius": [4, 4, 0, 0]},
        })
    series = [
        {"name": "失败SN数", "type": "bar", "data": bars_data,
         "barMaxWidth": 36,
         "label": {"show": True, "position": "top"}},
        {"name": "占比%", "type": "line", "yAxisIndex": 1, "smooth": True, "symbolSize": 7,
         "data": [round(float(r.get("ratio") or 0), 1) for r in rd],
         "itemStyle": {"color": "#7c3aed"}, "lineStyle": {"width": 3, "color": "#7c3aed"}},
    ]
    return {
        "chart_type": "bar",
        "data": {
            "title": "挽回失败 SN 原因分布 (归一化 7 大类)",
            "x_axis": x_axis,
            "series": series,
            "legend": {"data": ["失败SN数", "占比%"]},
            "yAxis": [
                {"type": "value", "name": "SN数"},
                {"type": "value", "name": "占比%", "min": 0, "max": 100, "axisLabel": {"formatter": "{value}%"}},
            ],
        },
    }


# ==============================================================
# 服务请求管理 / 今日看板专项 6 个 AnalysisView HANDLERS
# (对应 P1-T3.1, P1-T3.2, P2-T6.1~T6.4 6 个待实现视图)
# 所有函数签名: (Session, params:{start/end/date}) -> dict[chart_type, data]
# ==============================================================

def _sh_parse_params(params: _PARAMS_T) -> tuple[_dt.datetime, _dt.datetime]:
    """解析视图参数 → 返回北京时间当日窗口 (ts naive-UTC-start, te naive-UTC-end inclusive)

    优先级:
      - params.date (单日模式): 该自然日 00:00~23:59:59.999999 北京时间
      - params.start / params.end: 直接用 (字符串 YYYY-MM-DD 或 datetime)
      - 缺省: 今天自然日 北京时间
    """
    import datetime as __dt
    from zoneinfo import ZoneInfo as _ZI
    _TZ = _ZI("Asia/Shanghai")

    def _from_iso(s: str) -> __dt.date | None:
        try:
            # 先尝试 YYYY-MM-DD
            return __dt.date.fromisoformat(s[:10])
        except Exception:
            return None
    # 1) date 单日模式
    if params and params.get("date"):
        d = _from_iso(str(params["date"])) or __dt.datetime.now(_TZ).date()
    elif params and (params.get("start") or params.get("end")):
        d = _from_iso(str(params.get("start"))) if params.get("start") else __dt.datetime.now(_TZ).date()
    else:
        d = __dt.datetime.now(_TZ).date()
    s_local = __dt.datetime.combine(d, __dt.time(0, 0, 0), tzinfo=_TZ)
    e_local = __dt.datetime.combine(d, __dt.time(23, 59, 59, 999999), tzinfo=_TZ)
    return (
        s_local.astimezone(__dt.timezone.utc).replace(tzinfo=None),
        e_local.astimezone(__dt.timezone.utc).replace(tzinfo=None),
    )


def _view_ticket_work_top20(db: Session, params: _PARAMS_T) -> _RET_T:
    """P1-T3.1: 工单工作内容 Top20 (服务类别 × 服务项)

    列: 服务类别 / 服务项 / 近7天工单量 / 同比±% / 在途积压 / 平均处理时长min / 涉及SN去重 / 涉及手机号去重
    """
    ts, te = _sh_parse_params(params)
    # 近7天窗口
    ts_7 = te - timedelta(days=7) + timedelta(microseconds=1)
    # 同比7天 (前7-14天)
    ys_7 = ts_7 - timedelta(days=7)
    ye_7 = ts_7 - timedelta(microseconds=1)

    # Base CTE: 服务类别归一化 + 手机号清洗
    cat = _ticket_category_case()  # 归一化后的 service_category
    phone_clean = _clean_col_expr(ServiceTicket.phone)

    # 近7天聚合
    base_sel = (
        select(
            cat.label("cat_norm"),
            func.coalesce(ServiceTicket.service_item, literal("未标注")).label("item_norm"),
            func.count().label("cnt_cur"),
            func.count(func.distinct(func.nullif(func.trim(ServiceTicket.device_sn), ""))).label("sn_cnt"),
            func.count(func.distinct(phone_clean)).label("phone_cnt"),
            func.coalesce(func.avg(case(
                (and_(ServiceTicket.handle_duration != None, ServiceTicket.handle_duration > 0),
                 ServiceTicket.handle_duration / 60.0),
                else_=func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time) / 60.0
            )), 0.0).label("avg_dur_min"),
            func.sum(case(
                (ServiceTicket.status.notin_(["已完成", "无需处理", "已取消"]), 1),
                else_=0
            )).label("open_cnt"),
        )
        .select_from(ServiceTicket)
        .where(ServiceTicket.register_time.isnot(None),
               ServiceTicket.register_time >= ts_7, ServiceTicket.register_time <= te)
        .group_by("cat_norm", "item_norm")
        .cte("cur_agg")
    )
    # 同比7天聚合 (键相同的才会JOIN)
    yoy_sel = (
        select(
            cat.label("y_cat"),
            func.coalesce(ServiceTicket.service_item, literal("未标注")).label("y_item"),
            func.count().label("cnt_yoy")
        )
        .select_from(ServiceTicket)
        .where(ServiceTicket.register_time.isnot(None),
               ServiceTicket.register_time >= ys_7, ServiceTicket.register_time <= ye_7)
        .group_by("y_cat", "y_item")
        .cte("yoy_agg")
    )
    stmt = (
        select(
            base_sel.c.cat_norm, base_sel.c.item_norm,
            base_sel.c.cnt_cur, yoy_sel.c.cnt_yoy,
            base_sel.c.open_cnt, base_sel.c.avg_dur_min, base_sel.c.sn_cnt, base_sel.c.phone_cnt,
        )
        .select_from(base_sel)
        .outerjoin(yoy_sel, and_(yoy_sel.c.y_cat == base_sel.c.cat_norm, yoy_sel.c.y_item == base_sel.c.item_norm))
        .order_by(base_sel.c.cnt_cur.desc())
        .limit(20)
    )
    rows_q = db.execute(stmt).all()
    rows = []
    for r in rows_q:
        cur = int(r.cnt_cur or 0)
        yoy = int(r.cnt_yoy or 0)
        diff = cur - yoy
        ratio = round(diff / yoy * 100, 1) if yoy else None
        rows.append({
            "服务类别": (r.cat_norm or "未分类") if r.cat_norm != "投诉" else "投诉",
            "服务项": (r.item_norm or "未标注")[:30],
            "近7天工单量": cur,
            "同比工单量": yoy,
            "同比Δ": (f"+{diff}" if diff >= 0 else f"{diff}"),
            "同比%": (f"{ratio:+.1f}%" if ratio is not None else "新"),
            "在途积压": int(r.open_cnt or 0),
            "平均处理时长(min)": round(float(r.avg_dur_min or 0), 1),
            "涉及SN(去重)": int(r.sn_cnt or 0),
            "涉及手机号(去重)": int(r.phone_cnt or 0),
        })
    cols = [
        "服务类别", "服务项", "近7天工单量", "同比工单量", "同比Δ", "同比%",
        "在途积压", "平均处理时长(min)", "涉及SN(去重)", "涉及手机号(去重)",
    ]
    return {
        "chart_type": "table",
        "data": {
            "title": "工单工作内容 Top20 (按 服务类别×服务项, 近7天工单量排序)",
            "columns": cols,
            "rows": rows,
        },
    }


def _view_complaint_root_heatmap(db: Session, params: _PARAMS_T) -> _RET_T:
    """P1-T3.2: 投诉子类 Top10 × 六大类根因 热力矩阵

    口径与投诉根因柱图 (today_complaint_root_cause) 保持一致:
      - 投诉工单 (三表 UNION) JOIN cs_record (SN ± 24h 窗口关联)
      - 根因: 网络质量类 / 套餐争议类 / 退款协商失败类 / 降成争议类 / 其它注销发票退换 / 未关联SN
    """
    ts, te = _sh_parse_params(params)
    # 三表 UNION ALL: sn, phone, complaint_sub, register_time
    union_subq = union(
        select(
            ServiceTicket.device_sn.label("sn"),
            ServiceTicket.phone.label("phone"),
            _complaint_subitem_case().label("sub"),
            ServiceTicket.register_time.label("rt"),
        ).where(
            ServiceTicket.register_time >= ts - timedelta(days=1),
            ServiceTicket.register_time <= te + timedelta(days=1),
            _ticket_category_case() == "投诉",
        ).cte("_tp", recursive=False),
        select(
            ExternalComplaint.device_sn.label("sn"),
            ExternalComplaint.complainant_phone.label("phone"),
            func.coalesce(ExternalComplaint.complaint_category, literal("外部投诉")).label("sub"),
            ExternalComplaint.complaint_date.label("rt"),
        ).where(
            ExternalComplaint.complaint_date >= ts - timedelta(days=1),
            ExternalComplaint.complaint_date <= te + timedelta(days=1),
        ).cte("_ep", recursive=False),
        select(
            CardComplaint.device_sn.label("sn"),
            CardComplaint.complainant_phone.label("phone"),
            func.coalesce(CardComplaint.problem_category, literal("卡商投诉")).label("sub"),
            CardComplaint.complaint_date.label("rt"),
        ).where(
            CardComplaint.complaint_date >= ts - timedelta(days=1),
            CardComplaint.complaint_date <= te + timedelta(days=1),
        ).cte("_cp", recursive=False),
    )
    # 再建一级 CTE 便于 JOIN
    u = select(union_subq.c.sn, union_subq.c.phone, union_subq.c.sub, union_subq.c.rt).cte("u")

    # ±24h 窗口 JOIN cs_record, 拿根因分类
    from app.models.cs_record import CsRecord as _CsR
    stmt = (
        select(
            u.c.sub.label("投诉子类"),
            _complaint_root_case(_CsR.problem_category, _CsR.problem_type).label("根因"),
            func.count().label("c"),
        )
        .select_from(u)
        .outerjoin(
            _CsR,
            and_(
                func.nullif(func.trim(_CsR.device_sn), "").isnot(None),
                u.c.sn == func.nullif(func.trim(_CsR.device_sn), ""),
                _CsR.operation_time >= u.c.rt - text("INTERVAL '24 hours'"),
                _CsR.operation_time <= u.c.rt + text("INTERVAL '24 hours'"),
            ),
        )
        # 过滤窗口内登记的 (避免多 JOIN 后多算, 我们按 rt 再过滤)
        .where(u.c.rt >= ts, u.c.rt <= te)
        .group_by("投诉子类", "根因")
        .order_by(func.count().desc())
    )
    rows_q = db.execute(stmt).all()
    # 先拿到 Top10 投诉子类 + 6 根因桶
    sub_totals: dict[str, int] = {}
    matrix: dict[tuple[str, str], int] = {}
    ROOT_BUCKETS = [
        "网络质量类", "套餐争议类", "退款协商失败类", "降成争议类",
        "其它(注销/发票/退换)", "未关联SN",
    ]
    for r in rows_q:
        sub = (r["投诉子类"] or "未标注")[:14]
        rc = r["根因"] or "未关联SN"
        # 没关联上的 → 未关联SN 桶
        if rc not in ROOT_BUCKETS:
            # _complaint_root_case 已经归一化, 没命中的 else 值按规范归入最后兜底桶
            rc = "其它(注销/发票/退换)" if rc and "未关联" not in rc else "未关联SN"
        c = int(r.c or 0)
        sub_totals[sub] = sub_totals.get(sub, 0) + c
        matrix[(sub, rc)] = matrix.get((sub, rc), 0) + c
    # Top10 投诉子类
    top10_subs = [s for s, _ in sorted(sub_totals.items(), key=lambda kv: -kv[1])[:10]]
    if not top10_subs:
        return {"chart_type": "table", "data": {"columns": ["投诉子类"] + ROOT_BUCKETS + ["合计"], "rows": []}}
    # 行: 投诉子类 Top10; 列: 6 根因桶 + 合计; 值=该 cell 投诉量; 额外加一列占比% (cell / 该子类总量)
    rows = []
    for sub in top10_subs:
        total = sub_totals[sub]
        row = {"投诉子类": f"{sub}  (n={total})", "合计": total}
        for rc in ROOT_BUCKETS:
            cell = matrix.get((sub, rc), 0)
            pct = round(cell / total * 100, 1) if total else 0.0
            row[rc] = cell
            row[f"{rc}%"] = pct
        rows.append(row)
    cols = ["投诉子类"] + [x for pair in zip(ROOT_BUCKETS, [f"{b}%" for b in ROOT_BUCKETS]) for x in pair] + ["合计"]
    return {
        "chart_type": "table",
        "data": {
            "title": "投诉子类 Top10 × 六大类根因 热力矩阵 (SN±24h JOIN cs_record 归并根因)",
            "columns": cols,
            "rows": rows,
            "_matrix_cfg": {
                "top10_subs": top10_subs,
                "root_buckets": ROOT_BUCKETS,
                "matrix": {f"{k[0]}||{k[1]}": v for k, v in matrix.items()},
            },
        },
    }


def _view_today_sn_top(db: Session, params: _PARAMS_T) -> _RET_T:
    """P2-T6.1: 今日 SN TOP 覆盖 (按 接待工单量 排序, Top20)

    列: SN / 今日工单数 / 其中投诉 / 去重手机号 / 首次进线 / 最近进线 / 关联服务项摘要
    """
    ts, te = _sh_parse_params(params)
    stmt = (
        select(
            func.nullif(func.trim(ServiceTicket.device_sn), "").label("sn"),
            func.count().label("ticket_cnt"),
            func.sum(case((_ticket_category_case() == "投诉", 1), else_=0)).label("complaint_cnt"),
            func.count(func.distinct(_clean_col_expr(ServiceTicket.phone))).label("phone_cnt"),
            func.min(ServiceTicket.register_time).label("first_rt"),
            func.max(ServiceTicket.register_time).label("last_rt"),
            func.array_agg(func.distinct(func.coalesce(ServiceTicket.service_item, literal("未标注")))).label("items"),
        )
        .select_from(ServiceTicket)
        .where(
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te,
            func.nullif(func.trim(ServiceTicket.device_sn), "").isnot(None),
        )
        .group_by(text("sn"))
        .order_by(func.count().desc())
        .limit(20)
    )
    rows_q = db.execute(stmt).all()
    rows = []
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Shanghai")
    for r in rows_q:
        def _fmt(t):
            if not t: return ""
            try:
                return (t.replace(tzinfo=_dt.timezone.utc) if t.tzinfo is None else t).astimezone(TZ).strftime("%H:%M")
            except Exception:
                return ""
        items_list = list(r.items or [])[:3]
        rows.append({
            "SN": str(r.sn),
            "今日工单数": int(r.ticket_cnt or 0),
            "其中投诉": int(r.complaint_cnt or 0),
            "去重手机号": int(r.phone_cnt or 0),
            "首次进线": _fmt(r.first_rt),
            "最近进线": _fmt(r.last_rt),
            "服务项摘要": " / ".join([str(x)[:12] for x in items_list]),
        })
    cols = ["SN", "今日工单数", "其中投诉", "去重手机号", "首次进线", "最近进线", "服务项摘要"]
    return {
        "chart_type": "table",
        "data": {"title": "今日 SN TOP 覆盖 Top20 (按 今日工单数 排序)", "columns": cols, "rows": rows},
    }


def _view_today_phone_dup(db: Session, params: _PARAMS_T) -> _RET_T:
    """P2-T6.2: 今日手机号 TOP 重复投诉 (当日同一手机号 ≥ 2 次投诉, 三表 UNION)

    列: 脱敏手机号 / 今日投诉次数 (投诉三表内重复) / 今日总进线次数 (全部工单) / 关联SN列表 / 投诉子类摘要
    """
    ts, te = _sh_parse_params(params)
    # 投诉三表 UNION: 手机号, 投诉子类, SN
    comp_st = select(
        _clean_col_expr(ServiceTicket.phone).label("phone_c"),
        _complaint_subitem_case().label("sub"),
        ServiceTicket.device_sn.label("sn_c"),
        literal("ST").label("src"),
    ).where(
        ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te,
        _ticket_category_case() == "投诉",
    )
    comp_ec = select(
        _clean_col_expr(ExternalComplaint.complainant_phone).label("phone_c"),
        func.coalesce(ExternalComplaint.complaint_category, literal("外部投诉")).label("sub"),
        ExternalComplaint.device_sn.label("sn_c"),
        literal("EC").label("src"),
    ).where(
        ExternalComplaint.complaint_date >= ts, ExternalComplaint.complaint_date <= te,
    )
    comp_cc = select(
        _clean_col_expr(CardComplaint.complainant_phone).label("phone_c"),
        func.coalesce(CardComplaint.problem_category, literal("卡商投诉")).label("sub"),
        CardComplaint.device_sn.label("sn_c"),
        literal("CC").label("src"),
    ).where(
        CardComplaint.complaint_date >= ts, CardComplaint.complaint_date <= te,
    )
    comp_u = union(comp_st, comp_ec, comp_cc).cte("u")
    # 全部工单 (非投诉也统计) 用于总进线次数
    total_st = (
        select(
            _clean_col_expr(ServiceTicket.phone).label("phone_t"),
            func.count().label("total_cnt"),
            func.array_agg(func.distinct(func.nullif(func.trim(ServiceTicket.device_sn), ""))).label("sn_list"),
        )
        .where(ServiceTicket.register_time >= ts, ServiceTicket.register_time <= te)
        .group_by("phone_t")
        .cte("ts_agg")
    )
    # 投诉聚合
    comp_agg = (
        select(
            comp_u.c.phone_c.label("phone"),
            func.count().label("comp_cnt"),
            func.array_agg(func.distinct(comp_u.c.sub)).label("sub_list"),
            func.array_agg(func.distinct(func.nullif(func.trim(comp_u.c.sn_c), ""))).label("sn_list"),
        )
        .where(comp_u.c.phone_c.isnot(None))
        .group_by("phone")
        .having(func.count() >= 2)
        .order_by(func.count().desc())
        .limit(20)
        .cte("cagg")
    )
    stmt = (
        select(
            comp_agg.c.phone, comp_agg.c.comp_cnt, comp_agg.c.sub_list,
            func.coalesce(total_st.c.total_cnt, comp_agg.c.comp_cnt).label("total_cnt"),
            func.coalesce(total_st.c.sn_list, comp_agg.c.sn_list).label("sn_list"),
        )
        .select_from(comp_agg)
        .outerjoin(total_st, total_st.c.phone_t == comp_agg.c.phone)
        .order_by(comp_agg.c.comp_cnt.desc())
    )
    rows_q = db.execute(stmt).all()
    rows = []
    for r in rows_q:
        # 脱敏手机号: 138****1234
        p = str(r.phone or "")
        masked = p[:3] + "****" + p[7:] if len(p) == 11 else p
        subs = list(r.sub_list or [])[:3]
        sns = list({x for x in (r.sn_list or []) if x})[:5]
        rows.append({
            "手机号(脱敏)": masked,
            "今日投诉次数": int(r.comp_cnt or 0),
            "今日总进线次数": int(r.total_cnt or 0),
            "投诉子类摘要": " / ".join([str(s)[:12] for s in subs]),
            "关联SN(去重)": " / ".join(sns),
        })
    cols = ["手机号(脱敏)", "今日投诉次数", "今日总进线次数", "投诉子类摘要", "关联SN(去重)"]
    return {
        "chart_type": "table",
        "data": {"title": "今日手机号 TOP 重复投诉 (当日 ≥ 2 次投诉, Top20)", "columns": cols, "rows": rows},
    }


def _view_today_handler_sn_rank(db: Session, params: _PARAMS_T) -> _RET_T:
    """P2-T6.3: 今日处理人 覆盖 SN 排行 (按 白名单真实完工 + 推算完工时间 落今日 → 去重 SN 数 排序)

    更准确地反映"谁今天真正帮了最多设备的用户解决问题", 而不是纯结单数量。
    """
    ts, te = _sh_parse_params(params)
    SYS_USERS = {"sys", "system", "SYS", "System", "客户", None}

    # Step 1: 近 30 天真实完成样本, handler 平均耗时 (复用 _ticket_effective_finish_time_where 的 Step 1 逻辑)
    hist_since = ts - timedelta(days=30)
    real_done_cond = and_(
        ServiceTicket.status == "已完成",
        ServiceTicket.handler.isnot(None),
        ~ServiceTicket.handler.in_(list(SYS_USERS) + ["客户"]),
        ServiceTicket.finish_time.isnot(None),
        ServiceTicket.register_time.isnot(None),
        ServiceTicket.finish_time >= ServiceTicket.register_time,
        ServiceTicket.finish_time <= ServiceTicket.register_time + text("INTERVAL '12 hours'"),
    )
    h_avg_rows = db.execute(
        select(
            ServiceTicket.handler,
            func.avg(case(
                (and_(ServiceTicket.handle_duration != None, ServiceTicket.handle_duration > 0),
                 ServiceTicket.handle_duration),
                else_=func.extract("epoch", ServiceTicket.finish_time - ServiceTicket.register_time)
            )).label("avg_dur"),
            func.count().label("n"),
        )
        .where(real_done_cond, ServiceTicket.register_time >= hist_since)
        .group_by(ServiceTicket.handler)
    ).all()
    handler_avg: dict[str, float] = {}
    w_t, w_c = 0.0, 0
    for r in h_avg_rows:
        d = float(r._mapping["avg_dur"] or 0.0)
        n = int(r._mapping["n"] or 0)
        if n > 0 and d > 0:
            handler_avg[str(r.handler).strip()] = d
            w_t += d * n
            w_c += n
    global_avg = w_t / w_c if w_c else (28.0 * 60)

    # Step 2: 拉 status='已完成' + register 合理范围的工单, 算 effective_finish_time
    rows = db.execute(
        select(
            ServiceTicket.register_time, ServiceTicket.finish_time,
            ServiceTicket.handle_duration, ServiceTicket.handler,
            ServiceTicket.device_sn, ServiceTicket.service_category, ServiceTicket.service_item,
        )
        .where(
            ServiceTicket.status == "已完成",
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= hist_since,
            ServiceTicket.register_time <= te + timedelta(hours=13),
        )
    ).all()

    import datetime as __dt
    def _u(t):
        if t is None: return None
        return t.replace(tzinfo=__dt.timezone.utc) if t.tzinfo is None else t.astimezone(__dt.timezone.utc)
    _s, _e = _u(ts), _u(te)

    # {handler: set(sns)} 今日有效完工
    out: dict[str, set[str]] = {}
    ticket_cnt: dict[str, int] = {}
    for r in rows:
        reg_a = _u(r.register_time)
        if not reg_a: continue
        fin_a = _u(r.finish_time)
        is_real = bool(
            fin_a and r.handler and r.handler not in SYS_USERS
            and fin_a >= reg_a and fin_a <= reg_a + timedelta(hours=12)
        )
        if is_real:
            eff = fin_a
        else:
            if isinstance(r.handle_duration, (int, float)) and r.handle_duration > 0:
                eff = reg_a + timedelta(seconds=float(r.handle_duration))
            else:
                avg = handler_avg.get(str(r.handler).strip()) if r.handler else None
                eff = reg_a + timedelta(seconds=avg) if avg else reg_a + timedelta(seconds=global_avg)
        if not (_s <= eff <= _e): continue
        # 计数
        h = str(r.handler or "未分派").strip() or "未分派"
        if h not in out:
            out[h] = set()
            ticket_cnt[h] = 0
        ticket_cnt[h] += 1
        sn = (r.device_sn or "").strip()
        if sn:
            out[h].add(sn)

    # 排序
    rows_out = []
    for h, sns in sorted(out.items(), key=lambda kv: (-len(kv[1]), -ticket_cnt.get(kv[0], 0))):
        rows_out.append({
            "处理人": h,
            "今日覆盖SN数": len(sns),
            "今日真实结单量": ticket_cnt.get(h, 0),
        })
    cols = ["处理人", "今日覆盖SN数", "今日真实结单量"]
    return {
        "chart_type": "table",
        "data": {
            "title": "今日处理人 覆盖 SN 排行 (按 今日真实覆盖去重SN数 排序, 更能体现客服工作覆盖)",
            "columns": cols,
            "rows": rows_out,
        },
    }


def _view_today_cold_tickets(db: Session, params: _PARAMS_T) -> _RET_T:
    """P2-T6.4: 今日在途冷工单清单 (当前仍未结单 + 距登记 >24h + 最后更新人/最后更新时间)

    冷工单定义 (规范一致):
      - status 不属于 ['已完成','无需处理','已取消'] 三桶 (仍在途)
      - 当前时间 - register_time > 24 小时 (属于「压单」)
    按 "距登记时长(小时)" 倒序, 主管一眼看到压单最久的 50 条先处理。
    """
    # 冷工单不依赖"今日窗口",而是"当前所有在途"。
    # 不过 params.date 如果指定了日期, 就按「当日登记的在途工单 >24h」展示 (用于历史回溯)
    ts_utc, te_utc = _sh_parse_params(params)
    now_utc = datetime.now() if True else _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    # now 转 naive UTC (因为 ServiceTicket.register_time 取出时若 tz-aware, 会兼容比较)
    import datetime as __dt
    now = __dt.datetime.now(__dt.timezone.utc).replace(tzinfo=None)

    cond_in_progress = ServiceTicket.status.notin_(["已完成", "无需处理", "已取消"])
    # 距登记 > 24h
    cold_cond = now - ServiceTicket.register_time >= text("INTERVAL '24 hours'")
    # 如果是"非今日日期"模式 (params.date 不等于今天) → 就限定 register_time 落在 [ts_utc,te_utc] (当日视角)
    d_today = __dt.date.today()  # system date (we'll compare to sh today to decide)
    from zoneinfo import ZoneInfo as _ZI
    _TODAY_SH = __dt.datetime.now(_ZI("Asia/Shanghai")).date()
    # 用 te_utc 反推 display 日期
    def _to_sh_date(naive_utc):
        return naive_utc.replace(tzinfo=__dt.timezone.utc).astimezone(_ZI("Asia/Shanghai")).date()
    display_date = _to_sh_date(te_utc)
    scope = and_(ServiceTicket.register_time >= (ts_utc - timedelta(days=14)),
                 ServiceTicket.register_time <= te_utc) if display_date != _TODAY_SH else and_(
                     ServiceTicket.register_time <= te_utc,
                     now - ServiceTicket.register_time <= text("INTERVAL '60 days'"),
                 )

    stmt = (
        select(
            ServiceTicket.ticket_no, ServiceTicket.register_time, ServiceTicket.status,
            ServiceTicket.service_category, ServiceTicket.service_item,
            ServiceTicket.handler, ServiceTicket.dispatcher,
            ServiceTicket.last_dispatch_time, ServiceTicket.last_urge_time,
            ServiceTicket.device_sn, _clean_col_expr(ServiceTicket.phone).label("phone"),
            ServiceTicket.urge_count,
        )
        .where(cond_in_progress, cold_cond, ServiceTicket.register_time.isnot(None), scope)
        .order_by((now - ServiceTicket.register_time).desc())
        .limit(50)
    )
    rows_q = db.execute(stmt).all()
    TZ = _ZI("Asia/Shanghai")
    rows = []
    for r in rows_q:
        rt = r.register_time
        if not rt: continue
        rt_aware = rt.replace(tzinfo=__dt.timezone.utc) if rt.tzinfo is None else rt
        hrs = (now.replace(tzinfo=__dt.timezone.utc) - rt_aware).total_seconds() / 3600.0
        # 最后活动时间: 最后派单时间 / 最后催单时间 / 登记时间 取最大
        times = [rt_aware]
        for t in (r.last_dispatch_time, r.last_urge_time):
            if t is None: continue
            t_aware = t.replace(tzinfo=__dt.timezone.utc) if t.tzinfo is None else t
            times.append(t_aware)
        last_activity = max(times)
        rows.append({
            "工单号": r.ticket_no,
            "距登记(h)": round(hrs, 1),
            "状态": (r.status or "未标注")[:8],
            "服务类别": (r.service_category or "未分类")[:8],
            "服务项": (r.service_item or "未标注")[:18],
            "处理人": r.handler or "—",
            "派发人": r.dispatcher or "—",
            "最后活动": last_activity.astimezone(TZ).strftime("%m-%d %H:%M"),
            "催单次数": int(r.urge_count or 0),
            "SN": (r.device_sn or "")[:16],
            "手机号(脱敏)": (lambda p: p[:3] + "****" + p[7:] if p and len(p) == 11 else (p or ""))(r.phone),
            "登记时间": rt_aware.astimezone(TZ).strftime("%m-%d %H:%M"),
        })
    cols = [
        "工单号", "距登记(h)", "状态", "服务类别", "服务项", "处理人", "派发人",
        "最后活动", "催单次数", "SN", "手机号(脱敏)", "登记时间",
    ]
    return {
        "chart_type": "table",
        "data": {
            "title": f"在途冷工单 Top50 (仍未结 + 距登记 > 24h, 按压单时长倒序, 主管优先处理)",
            "columns": cols,
            "rows": rows,
            "_scope": {"display_date": display_date.isoformat(), "now": __dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M")},
        },
    }


HANDLERS: dict[str, _Callable[[Session, _PARAMS_T], _RET_T]] = {
    "preset_refund_recovery_kpi":            _view_rr_kpi,
    "preset_refund_recovery_daily_trend":    _view_rr_daily_trend,
    "preset_refund_recovery_hourly_today":   _view_rr_hourly_today,
    "preset_refund_recovery_operator_rank":  _view_rr_operator_rank,
    "preset_refund_recovery_reason_dist":    _view_rr_reason_dist,
    # ---- 今日看板 / 服务请求专项 (本次新增 6 个 handlers) ----
    "preset_ticket_work_content_top20":      _view_ticket_work_top20,
    "preset_complaint_subtype_rootcause_heatmap": _view_complaint_root_heatmap,
    "preset_today_sn_top_coverage":          _view_today_sn_top,
    "preset_today_phone_dup_complaint":      _view_today_phone_dup,
    "preset_today_handler_sn_rank":          _view_today_handler_sn_rank,
    "preset_today_cold_tickets":             _view_today_cold_tickets,
}


