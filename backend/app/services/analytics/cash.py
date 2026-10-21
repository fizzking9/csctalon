"""经营流水/钱包: 操作统计 + A/B/C/D/E 路由 + 现金流出/钱包/商户汇总。"""

import datetime as _dt
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, column, func, select, table, text
from sqlalchemy.orm import Session

# #338: 退款五路统计统一读 std_* / std_*_all, 禁止直读 ods。
from app.models.refund_standard import (
    StdRefundOnline,
    StdRefundRecord,
    StdWalletFlow,
)

# std_refund_online_all 全量镜像(全状态口径: 含申请中/驳回等被采纳规则排除的行)
_REFUND_ONLINE_ALL = table(
    "std_refund_online_all",
    column("status"), column("refund_amount"), column("refund_method"),
)

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    D,
    _date_range,
    _route_a_online_refund_filter,
    _route_b_withdraw_filter,
    _route_e_cashflow_filter,
)


# ---------- 看板1:运营分析 ----------
def operation_trend(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """退款趋势(按日):退款金额、笔数 【统一口径: A 路线上直退, 取数 std_refund_online 采纳行(#332)】"""
    start, end = _date_range(start, end)
    rows = db.execute(
        select(
            func.to_char(func.timezone("Asia/Shanghai", StdRefundOnline.created_at_raw), "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(StdRefundOnline.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(StdRefundOnline.created_at_raw.between(start, end), *_route_a_online_refund_filter())
        .group_by("date")
        .order_by("date")
    ).all()
    return [{"date": r.date, "value": float(r.amount or 0), "count": r.count} for r in rows]

def operation_status_dist(db: Session) -> list[dict]:
    """退费状态分布 【A 路: 线上退费单各状态占比(含申请中/无需退款/驳回, 全状态口径)】

    #338: 全状态诊断读 std_refund_online_all 镜像视图(含被采纳规则排除的行);
    std_refund_online 只含已退款采纳行, 看不到申请中/驳回等状态。
    """
    rows = db.execute(
        select(_REFUND_ONLINE_ALL.c.status,
               func.coalesce(func.sum(_REFUND_ONLINE_ALL.c.refund_amount), 0),
               func.count())
        .where(_REFUND_ONLINE_ALL.c.refund_method == "线上")
        .group_by(_REFUND_ONLINE_ALL.c.status)
    ).all()
    total = sum(r[2] for r in rows) or 1
    return [
        {"name": r[0] or "(空)", "value": float(r[1] or 0), "count": r[2], "ratio": round(r[2] / total, 4)}
        for r in rows
    ]

def operation_reason_top(db: Session, limit: int = 10) -> list[dict]:
    """退费原因 TOP 【统一口径: A 路线上直退, 取数 std_refund_online 采纳行(#332)】"""
    rows = db.execute(
        select(StdRefundOnline.refund_reason, func.coalesce(func.sum(StdRefundOnline.refund_amount), 0), func.count())
        .where(StdRefundOnline.refund_reason.isnot(None), *_route_a_online_refund_filter())
        .group_by(StdRefundOnline.refund_reason)
        .order_by(func.sum(StdRefundOnline.refund_amount).desc())
        .limit(limit)
    ).all()
    return [{"name": r[0], "value": float(r[1] or 0), "count": r[2]} for r in rows]

def operation_method_dist(db: Session) -> list[dict]:
    """退款方式占比 【#332: 取数 std_refund_online, 已采纳退款的方式分布(线上/空方式捞回)】"""
    rows = db.execute(
        select(StdRefundOnline.refund_method, func.coalesce(func.sum(StdRefundOnline.refund_amount), 0), func.count())
        .group_by(StdRefundOnline.refund_method)
    ).all()
    return [{"name": r[0] or "(空)", "value": float(r[1] or 0), "count": r[2]} for r in rows]


# ---------- 路线C 闪猫退款 (独立业务表) ----------
# 真实表名: shanmao_refunds (alembic: b8c9d0e1f2a3_create_shanmao_refunds)
# 字段映射关系(2026-08-03 实际 Excel 样例):
#   金额: actual_amount(实际退款金额)  而非 refund_amount(申请退款金额)
#   时间: audit_time(审核通过时间 ≈ 退款完成时间)  Excel 无独立 refund_time 列
#   状态列: status  成功值: '已退款'(中文), 其他状态(申请中/已驳回/退款失败等)不计入经营出款
# #322 方案A → #332: 读路径切到标准表 std_refund_shanmao(采纳作业固化 status='已退款'),
# 原 status='已退款' WHERE 与质量排除片段删除。
_C_SHANMAO_TABLE = "std_refund_shanmao"

_C_SHANMAO_AMOUNT_COL = "actual_amount"

_C_SHANMAO_TIME_COL = "audit_time"

# std 表层已固化已退款口径 → 状态条件置 None, SQL 模板条件式自动去子句
_C_SHANMAO_STATUS_COL = None

_C_SHANMAO_STATUS_OK = "已退款"


# 表存在性缓存: {table_name: (exists, checked_at_monotonic)}
# 避免反复查询不存在的表(如尚未建物理表的数据源)导致 PG 日志刷屏 ERROR。
# to_regclass() 查不到返回 NULL,不会产生 ERROR 日志;缓存 10 分钟,建表后自动生效。
_table_exists_cache: dict[str, tuple[bool, float]] = {}

_TABLE_EXISTS_RECHECK_SEC = 600.0

def _table_exists(db: Session, table_name: str) -> bool:
    """检查物理表是否存在(带缓存,用 to_regclass 避免 PG ERROR 日志刷屏)"""
    import time as _t
    now = _t.monotonic()
    cached = _table_exists_cache.get(table_name)
    if cached:
        exists, checked_at = cached
        # 已确认存在 → 永久缓存; 不存在 → 10 分钟内跳过重查
        if exists or (now - checked_at) < _TABLE_EXISTS_RECHECK_SEC:
            return exists
    try:
        with db.begin_nested():
            oid = db.execute(text("SELECT to_regclass(:t)"), {"t": table_name}).scalar()
        exists = oid is not None
    except Exception:
        exists = False
    _table_exists_cache[table_name] = (exists, now)
    return exists

def _route_c_shanmao_agg(db: Session, start, end) -> tuple[int, float]:
    """【路线C】闪猫退款独立表聚合 (表不存在则返回 0,0,不报错).

    PostgreSQL 中 SQL 失败会使整个事务处于 InFailedSqlTransaction 状态，
    即使 Python 层 except 吞异常，后续 SQL 也会被 PG 拒绝。
    必须用 savepoint (begin_nested) 隔离，失败只回滚子事务。
    """
    if not _table_exists(db, _C_SHANMAO_TABLE):
        return 0, Decimal("0")
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
        return int(r[0] or 0), D(r[1])
    except Exception:
        return 0, Decimal("0")


# ---------- 路线D 手动退款 (独立业务表) ----------
# 真实表名: manual_refunds (alembic: f0e1d2c3b4a5_create_manual_refunds)
# 数据源: 商户号手机退款处理登记.xlsx (客服在商户号后台手动操作退款台账)
#   金额: refund_amount(退款金额); 状态: is_refunded='是' 计入经营出款, '否' = 登记蓄水(水位)
#   时间: op_time(实际退款操作时间) 为主, 缺失回退 register_time(登记日期)
# #322 方案A → #332: 读路径切到标准表 std_refund_manual(采纳作业固化 is_refunded='是'
#   且排除 dup_residual/sn_unresolvable), 原 is_refunded WHERE 与质量排除机制删除。
_D_OTHER_TABLE = "std_refund_manual"

_D_OTHER_AMOUNT_COL = "refund_amount"

_D_OTHER_TIME_COL = "COALESCE(op_time, register_time)"

# std 表层已固化 is_refunded='是' → 状态条件置 None, SQL 模板条件式自动去子句
_D_OTHER_STATUS_COL = None

_D_OTHER_STATUS_OK = "是"

def _route_d_other_agg(db: Session, start, end) -> tuple[int, float]:
    """【路线D】手动退款聚合 (取数 std_refund_manual 采纳行; 表不存在则返回 0,0,不报错).

    同 _route_c_shanmao_agg: 必须用 savepoint 隔离 PG 事务失败状态。
    #332: 采纳作业已固化 is_refunded='是' + 质量排除, 查询不再散落 WHERE。
    """
    if not _table_exists(db, _D_OTHER_TABLE):
        return 0, Decimal("0")
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
        return int(r[0] or 0), D(r[1])
    except Exception:
        return 0, Decimal("0")


# ---------- 路线C/D 按时间粒度分组聚合 (小时/日) ----------
def _route_c_shanmao_trend(db: Session, start, end, grain: str, slot_idx_fn) -> tuple[dict[int, int], dict[int, float]]:
    """【路线C】闪猫退款按时间分组聚合 (取数 std_refund_shanmao; 表不存在则返回空map)."""
    if not _table_exists(db, _C_SHANMAO_TABLE):
        return {}, {}
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
    amt_map: dict[int, Decimal] = {}
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
            amt_map[idx] = D(r[2])
    except Exception:
        pass
    return cnt_map, amt_map

def _route_d_other_trend(db: Session, start, end, grain: str, slot_idx_fn) -> tuple[dict[int, int], dict[int, float]]:
    """【路线D】手动退款按时间分组聚合 (取数 std_refund_manual 采纳行; 表不存在则返回空map)."""
    if not _table_exists(db, _D_OTHER_TABLE):
        return {}, {}
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
    amt_map: dict[int, Decimal] = {}
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
            amt_map[idx] = D(r[2])
    except Exception:
        pass
    return cnt_map, amt_map

def _cash_routes_total(db: Session,
                       col_a: Any = StdRefundOnline.created_at_raw,
                       col_b: Any = StdWalletFlow.op_time,
                       col_e: Any = StdRefundRecord.refund_time,
                       start: _dt.datetime | None = None,
                       end: _dt.datetime | None = None) -> dict[str, Any]:
    """【统一核算聚合器 v3】四路经营A/B/C/D + 实际现金流E + 对账偏差 (#332 取数 std 五表).

    Args:
        col_a: 路线A 时间列 (默认 std_refund_online.created_at_raw)
        col_b: 路线B 时间列 (默认 std_wallet_flow.op_time)
        col_e: 实际现金流 E 时间列 (默认 std_refund_record.refund_time)
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

    # ── A 退费单线上直退 (std_refund_online 采纳行) ──
    a_row = db.execute(select(
        func.count(),
        func.coalesce(func.sum(StdRefundOnline.refund_amount), 0),
    ).where(
        *_route_a_online_refund_filter(),
        _between(col_a, start, end),
    )).first() or (0, 0)
    a_cnt, a_amt = int(a_row[0] or 0), D(a_row[1])

    # ── B 钱包用户取现 (std_wallet_flow 成功提现) ──
    b_row = db.execute(select(
        func.count(),
        func.coalesce(func.sum(StdWalletFlow.op_amount), 0),
    ).where(
        *_route_b_withdraw_filter(),
        _between(col_b, start, end),
    )).first() or (0, 0)
    b_cnt, b_amt = int(b_row[0] or 0), D(b_row[1])

    # ── C 闪猫退款独立表 (std_refund_shanmao, 采纳行) ──
    c_cnt, c_amt = _route_c_shanmao_agg(db, start, end)
    # ── D 手动退款独立表 (std_refund_manual, 采纳行) ──
    d_cnt, d_amt = _route_d_other_agg(db, start, end)

    # ── E 实际现金流: std_refund_record 退款成功合计 (对账基准) ──
    e_row = db.execute(select(
        func.count(),
        func.coalesce(func.sum(StdRefundRecord.refund_amount), 0),
    ).where(
        *_route_e_cashflow_filter(),
        _between(col_e, start, end),
    )).first() or (0, 0)
    e_cnt, e_amt = int(e_row[0] or 0), D(e_row[1])

    # ── 合计: 经营估算 A+B+C+D / 实际 E / 对账偏差 经营−实际 ──
    op_cnt = a_cnt + b_cnt + c_cnt + d_cnt
    op_amt = a_amt + b_amt + c_amt + d_amt
    fc_cnt = e_cnt
    fc_amt = e_amt

    # #222 出参边界: 金额在出参点统一 quantize(2) 后转 float(JSON number),
    # 下游(API/JSONB 落库/既有测试断言)语义与改造前完全一致; 计算层已是 Decimal。
    def _out(x: Decimal) -> float:
        return float(round(x, 2))

    return {
        "route_a": {"count": a_cnt, "amount": _out(a_amt)},   # A.退费单线上直退
        "route_b": {"count": b_cnt, "amount": _out(b_amt)},   # B.钱包-用户取现
        "route_c": {"count": c_cnt, "amount": _out(c_amt)},   # C.闪猫退款(独立表,暂0)
        "route_d": {"count": d_cnt, "amount": _out(d_amt)},   # D.手动退款(manual_refunds)
        "actual_cashflow":  # E.商户号实际现金流 退款记录报表退款成功合计 (对账基准,不分C/D桶)
            {"count": e_cnt, "amount": _out(e_amt)},
        "operational":     # 经营侧四路总登记出款估算 = A+B+C+D
            {"count": op_cnt, "amount": _out(op_amt)},
        "financial":       # 向后兼容键: 财务实际 = actual_cashflow = E
            {"count": fc_cnt, "amount": _out(fc_amt)},
        "diff":            # 对账偏差 = 经营估算 − 实际现金流
            {"count": op_cnt - fc_cnt, "amount": _out(op_amt - fc_amt)},
    }

def _real_cash_out_total(db: Session) -> tuple[float, float]:
    """兼容旧名: 真实现金流(A+B) = 直接退款A + 用户提现B,返回 (A, B)（出参边界 float, 见 _cash_routes_total 注释）"""
    r = _cash_routes_total(db)
    return r["route_a"]["amount"], r["route_b"]["amount"]

def cash_out_trend(db: Session, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """真实现金流出趋势(按日) 【全域统一核算 v3】

    四路经营出款 (A/B/C/D) 单独按日堆叠 = 经营估算 total;
    实际现金流 E (refund_record_reports 退款成功,不分桶) 作为独立曲线 overlay;
    对账偏差 diff = 经营(total) − 实际(E);
    每 day 返回 direct(A) withdraw(B) shanmao(C) other_refund(D), total(=A+B+C+D),
    merchant(=E 实际现金流), diff(=total−merchant), 及各自 count.
    D 手动退款(manual_refunds) 已建表自动生效.
    """
    start, end = _date_range(start, end)
    s, e = start, end
    # ── A 直接退款按日 (路线A refunds线上直退) ──
    # 日分桶统一按北京日 (AT TIME ZONE 'Asia/Shanghai'), 与 C/D/E 路及决策概览口径一致 (2026-09-05 修正 UTC 截日 bug)
    direct_rows = db.execute(
        select(
            func.to_char(func.timezone("Asia/Shanghai", StdRefundOnline.created_at_raw), "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(StdRefundOnline.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(*_route_a_online_refund_filter(), StdRefundOnline.created_at_raw.between(s, e))
        .group_by("date")
    ).all()
    direct_map = {r.date: {"amount": D(r.amount), "count": r.count} for r in direct_rows}
    # ── B 用户提现按日 (路线B std_wallet_flow 成功提现) ──
    withdraw_rows = db.execute(
        select(
            func.to_char(func.timezone("Asia/Shanghai", StdWalletFlow.op_time), "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(StdWalletFlow.op_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(StdWalletFlow.op_time.between(s, e), *_route_b_withdraw_filter())
        .group_by("date")
    ).all()
    withdraw_map = {r.date: {"amount": D(r.amount), "count": r.count} for r in withdraw_rows}
    # ── C 闪猫退款按日 (独立表 shenmao_refund 占位, 表不存在→空map) ──
    shanmao_map: dict[str, dict] = {}
    if _table_exists(db, _C_SHANMAO_TABLE):
        try:
            with db.begin_nested():
                sm_rows = db.execute(text(f"""
                SELECT to_char(timezone('Asia/Shanghai', {_C_SHANMAO_TIME_COL}), 'YYYY-MM-DD') AS date,
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
            shanmao_map = {r[0]: {"amount": D(r[2]), "count": int(r[1] or 0)} for r in sm_rows}
        except Exception:
            pass   # 表未建 → 空 (savepoint 已隔离 PG 事务失败状态)
    # ── D 手动退款按日 (std_refund_manual 采纳行) ──
    other_map: dict[str, dict] = {}
    if _table_exists(db, _D_OTHER_TABLE):
        try:
            with db.begin_nested():
                ot_rows = db.execute(text(f"""
                SELECT to_char(timezone('Asia/Shanghai', {_D_OTHER_TIME_COL}), 'YYYY-MM-DD') AS date,
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
            other_map = {r[0]: {"amount": D(r[2]), "count": int(r[1] or 0)} for r in ot_rows}
        except Exception:
            pass
    # ── E 实际现金流按日 (std_refund_record 退款成功合计, 对账基准) ──
    # 日分桶统一按北京日 (与决策概览 _day AT TIME ZONE 口径一致)
    actual_rows = db.execute(
        select(
            func.to_char(func.timezone("Asia/Shanghai", StdRefundRecord.refund_time), "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(StdRefundRecord.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(StdRefundRecord.refund_time.between(s, e), *_route_e_cashflow_filter())
        .group_by("date")
    ).all()
    actual_map = {r.date: {"amount": D(r.amount), "count": r.count} for r in actual_rows}

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
            # D 手动退款 (manual_refunds)
            "other_refund":        _d(other_map, d, "amount"),
            "other_refund_count":  _d(other_map, d, "count"),
            # 经营估算 = A+B+C+D (四路经营出款)
            "total": (_d(direct_map, d, "amount") + _d(withdraw_map, d, "amount")
                      + _d(shanmao_map, d, "amount") + _d(other_map, d, "amount")),
            "total_count": (_d(direct_map, d, "count") + _d(withdraw_map, d, "count")
                            + _d(shanmao_map, d, "count") + _d(other_map, d, "count")),
            # 实际现金流 E = refund_record_reports 退款成功 合计 (对账基准)
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
    # 退回钱包: std_wallet_flow 进水 (flow_dir='inflow' 即 op_type='订单退费转入', #332)
    wallet_in = db.execute(
        select(
            func.to_char(func.timezone("Asia/Shanghai", StdWalletFlow.op_time), "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(StdWalletFlow.op_amount), 0).label("amount"),
        )
        .where(StdWalletFlow.op_time.between(start, end), StdWalletFlow.flow_dir == "inflow")
        .group_by("date")
    ).all()
    wallet_in_map = {r.date: D(r.amount) for r in wallet_in}
    # 实际提现: std_wallet_flow 成功提现 (失败/冻结已在表层排除, #332)
    withdraw = db.execute(
        select(
            func.to_char(func.timezone("Asia/Shanghai", StdWalletFlow.op_time), "YYYY-MM-DD").label("date"),
            func.coalesce(func.sum(StdWalletFlow.op_amount), 0).label("amount"),
        )
        .where(StdWalletFlow.op_time.between(start, end), *_route_b_withdraw_filter())
        .group_by("date")
    ).all()
    withdraw_map = {r.date: D(r.amount) for r in withdraw}
    dates = sorted(set(wallet_in_map) | set(withdraw_map))
    return [
        {"date": d, "wallet_in": wallet_in_map.get(d, 0), "withdraw": withdraw_map.get(d, 0)}
        for d in dates
    ]

def cash_out_breakdown(db: Session) -> dict[str, Any]:
    """真实现金流构成明细 【全域统一核算 v3】

    四路经营出款: A 退费单线上直退 / B 钱包用户取现 / C 闪猫退款 / D 手动退款(manual_refunds)
    经营估算 = A+B+C+D (四路登记出款合计)
    实际现金流 = E (refund_record_reports 退款成功合计, 对账基准, 不分C/D桶)
    对账偏差 = 经营估算 − 实际现金流
    """
    routes = _cash_routes_total(db)
    a = routes["route_a"]
    b = routes["route_b"]
    c = routes["route_c"]
    d = routes["route_d"]
    op = routes["operational"]        # 经营估算 = A+B+C+D
    e = routes["actual_cashflow"]     # 实际现金流 = E = 退款记录报表退款成功合计
    fc = routes["financial"]          # 向后兼容: financial = actual_cashflow = E
    diff = routes["diff"]             # 对账偏差 = 经营 − 实际
    return {
        # ── 四路线拆分 ──
        "route_a": a,  # A.退费单-线上直退 (refund_method='线上')
        "route_b": b,  # B.钱包-用户取现 (op_type='用户提现')
        "route_c": c,  # C.闪猫退款 (独立业务表 shanmao_refunds)
        "route_d": d,  # D.手动退款 (独立业务表 manual_refunds)
        # ── 双视角合计 ──
        "direct_refund": a["amount"],       # 兼容旧键: 直接退款 = A
        "user_withdraw": b["amount"],       # 兼容旧键: 用户提现 = B
        "total": op["amount"],              # 兼容旧键: 经营估算 = A+B+C+D
        "total_count": op["count"],         # 经营侧估算笔数
        "actual_cashflow": e["amount"],     # (新) 实际现金流 E = 退款记录报表退款成功合计
        "actual_cashflow_count": e["count"],# (新) 实际现金流笔数
        "merchant_cash": fc["amount"],      # 兼容旧键: 商户号现金流 = E = actual_cashflow (财务实际)
        "merchant_cash_count": fc["count"], # 兼容旧键: 财务侧实际笔数
        "diff": diff["amount"],             # 兼容旧键: 对账偏差 = 经营估算 − 实际现金流
        "diff_count": diff["count"],        # 对账偏差笔数
        "_spec": {
            "total": "经营估算 = 四路经营出款 A+B+C+D (A线上直退+B钱包取现+C闪猫退款+D手动退款)",
            "merchant_cash": "实际现金流(对账基准) = refund_record_reports 退款成功合计, 不分ORIGINAL/BALANCE桶",
            "diff": "对账偏差 = 经营估算 − 实际现金流 (商户号现金流不与经营数据累加)",
        },
    }

def merchant_cash_summary(db: Session) -> list[dict]:
    """商户号退款对账(按商户号) 【统一口径: 实际现金流E, 退款成功 不分桶】

    ⚠️ 2026-09-05 (#20): 数据源从 merchant_refunds(旧快照, 含 fee) 切换到
    refund_record_reports(BMS 退款记录报表)。新表无 fee(手续费)字段 → 返回 fee 恒 0
    (前端兼容保留该键, 实际手续费需另行评估或由旧表参考)。
    实际现金流 E = refund_record_reports.refund_status='退款成功' 全部合计, 对账基准.
    """
    rows = db.execute(
        select(
            StdRefundRecord.merchant_id,
            func.coalesce(func.sum(StdRefundRecord.refund_amount), 0).label("amount"),
            func.count().label("count"),
        )
        .where(*_route_e_cashflow_filter())
        .group_by(StdRefundRecord.merchant_id)
        .order_by(func.sum(StdRefundRecord.refund_amount).desc())
        .limit(50)
    ).all()
    return [
        {
            "merchant_id": r[0],
            "refund_amount": float(r[1] or 0),
            "count": r[2],
            "fee": 0.0,  # ⚠️ 新表 refund_record_reports 无 fee 字段, 恒 0
        }
        for r in rows
    ]

def wallet_balance_top(db: Session, limit: int = 20) -> list[dict]:
    """钱包余额分布(按账户,取最新余额) 【#332: 取数 std_wallet_flow(仅采纳行)】"""
    # 用窗口函数取每个账户最新一条记录的余额
    rows = db.execute(
        select(
            StdWalletFlow.account_id,
            StdWalletFlow.wallet_balance,
            StdWalletFlow.op_time,
        )
        .order_by(StdWalletFlow.account_id, StdWalletFlow.op_time.desc())
        .limit(limit * 5)
    ).all()
    # 去重保留每个账户最新
    seen: dict[str, dict] = {}
    for r in rows:
        if r[0] and r[0] not in seen:
            seen[r[0]] = {"account_id": r[0], "balance": float(r[1] or 0), "op_time": r[2].isoformat() if r[2] else None}
    return list(seen.values())[:limit]
