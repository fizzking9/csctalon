"""对账复核服务 - ABCD/E 五路手动对账复核留痕(A-20260906-002 LOCKED / #22)

复用统一核算口径(_cash_routes_total) 与 E 路逐笔勾稽分类(water_statement 的
_build_route_maps/_classify_report), 保证复核快照与展示页同窗口口径一致。
复核批次落 reconcile_checks(仅留痕, 不参与任何统计聚合)。

功能:
1. preview_check(db, start, end, grain): 生成对账预览(不落库)
   - route_snapshot: _cash_routes_total 输出(五路+经营+实际+差异)
   - by_route_of_e:   E 水单勾稽归属(A/B/C/D/unmatched)
   - dupe_detect:     D 路同 order_no 多行/无SN乱年份检出(辅助复核)
2. confirm_check(db, user, ...): 发起复核, 落库 reconcile_checks
3. list_checks / get_check: 批次查询
4. set_conclusion(db, user, id, conclusion, note): 补/改结论与备注
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.manual_refund import ManualRefund
from app.models.reconcile_check import ReconcileCheck
from app.models.reconcile_match import ReconcileMatch
from app.models.refund import Refund
from app.models.refund_record_report import RefundRecordReport
from app.models.shanmao_refund import ShanmaoRefund
from app.models.wallet import WalletWithdrawal

logger = logging.getLogger(__name__)

_TZ_CN = timezone(timedelta(hours=8))

# 与 analytics._route_e_cashflow_filter 一致(避免循环 import, 本地常量)
_E_ROUTE_SUCCESS_STATUSES = ("退款成功", "成功", "SUCCESS")

# 复核结论枚举(校验用)
CONCLUSIONS = ("一致", "差异已确认", "存疑待查")

# 逐笔勾稽人工确认状态(A-20260906-002 v1.1 / #22)
CONFIRM_STATUSES = ("已核对一致", "差异已确认", "存疑待查", "已排除重复", "待核对")

# 残留脏行判定年份(与 reconcile.backfill 护栏一致: 无SN + 年份异常)
_CURRENT_YEARS = (2025, 2026)


def _to_utc_aware(dt: datetime) -> datetime:
    """参数按北京时间 → UTC aware(与 reconcile._to_utc_aware 同语义, 避免循环 import)"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_TZ_CN).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_cn(dt: datetime) -> datetime:
    """UTC aware → 北京时间(序列化用)"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ_CN)


# ─────────────────────────────────────────────────────────────
# 1. 对账预览(不落库)
# ─────────────────────────────────────────────────────────────
def _route_snapshot(db, start: datetime, end: datetime) -> dict:
    """五路合计 + 经营/实际/差异 时点快照(复用 _cash_routes_total 口径)"""
    from app.services.analytics import _cash_routes_total

    # _cash_routes_total 用 UTC aware 时间窗即可(底层按存储时间戳过滤)
    return _cash_routes_total(db, start=start, end=end)


def _e_by_route(db, start: datetime, end: datetime) -> dict:
    """E 水单勾稽归属(A/B/C/D/unmatched) 笔数金额(复用 water_statement 分类逻辑)"""
    from app.services.reconcile import _build_route_maps, _classify_report

    base_rows = db.scalars(
        select(RefundRecordReport).where(
            RefundRecordReport.refund_status.in_(_E_ROUTE_SUCCESS_STATUSES),
            RefundRecordReport.refund_time >= start,
            RefundRecordReport.refund_time <= end,
        )
    ).all()
    maps = _build_route_maps(db, start, end)
    by_route: dict[str, dict] = {
        "A": {"count": 0, "amount": 0.0},
        "B": {"count": 0, "amount": 0.0},
        "C": {"count": 0, "amount": 0.0},
        "D": {"count": 0, "amount": 0.0},
        "unmatched": {"count": 0, "amount": 0.0},
    }
    for r in base_rows:
        route = _classify_report(r, maps)
        bucket = by_route.setdefault(route, {"count": 0, "amount": 0.0})
        bucket["count"] += 1
        bucket["amount"] += float(r.refund_amount or 0)
    # 归一(仅保留 5 桶; unmatched 命名给前端)
    return {k: {"count": v["count"], "amount": round(v["amount"], 2)} for k, v in by_route.items()}


def _dupe_detect(db, start: datetime, end: datetime) -> dict:
    """D 路重复/脏数据检出(辅助复核; 不阻塞)"""
    # 同 order_no 多行(窗口内已退款, 按 COALESCE(op_time,register_time) 落窗)
    rows = db.execute(
        select(
            ManualRefund.order_no,
            func.count().label("n"),
            func.count(ManualRefund.device_sn).label("with_sn"),
            func.coalesce(func.sum(ManualRefund.refund_amount), 0).label("amt"),
        )
        .where(
            ManualRefund.is_refunded == "是",
            ManualRefund.order_no.is_not(None),
            ManualRefund.order_no != "",
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time).is_not(None),
            # 落窗过滤: 与第二段 no_sn_weird 口径一致(均在所选时间窗内), 避免"全域 vs 窗口"口径矛盾 (#21/#22 Reviewer W2)
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time) >= start,
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time) <= end,
        )
        .group_by(ManualRefund.order_no)
        .having(func.count() > 1)
    ).all()
    dup_amt = 0.0
    dup_groups = 0
    for _o, n, _ws, amt in rows:
        dup_groups += 1
        dup_amt += float(amt or 0) * (n - 1) / n  # 超额占比估算(每单多出 n-1/n)
    # 无SN+年份异常残留行(窗口内, 与治理脚本同口径; 行数可控, 逐行判年份可接受)
    residual_rows = 0
    residual_amt = 0.0
    for m in db.scalars(
        select(ManualRefund).where(
            ManualRefund.is_refunded == "是",
            (ManualRefund.device_sn.is_(None)) | (ManualRefund.device_sn == ""),
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time).is_not(None),
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time) >= start,
            func.coalesce(ManualRefund.op_time, ManualRefund.register_time) <= end,
        )
    ).all():
        if m.register_time is None:
            continue
        try:
            yr = m.register_time.astimezone(_TZ_CN).year
        except (ValueError, OSError):
            continue
        if yr not in _CURRENT_YEARS:
            # 该行 COALESCE(op_time,register_time) 已落在窗口(上面 SQL 已过滤), 直接计
            residual_rows += 1
            residual_amt += float(m.refund_amount or 0)

    return {
        "dup_order_groups": dup_groups,
        "dup_amount_est": round(dup_amt, 2),
        "no_sn_weird_rows_in_win": residual_rows,
        "no_sn_weird_amt_in_win": round(residual_amt, 2),
        "note": "仅辅助提示(限所选窗口): dup_amount_est 为同单多行超额占比估算, no_sn_weird 为无SN+乱年份残留行(治理#21 目标)",
    }


def _gen_check_no(db, day: str) -> str:
    """生成复核单号 RC+YYYYMMDD+序号"""
    prefix = f"RC{day.replace('-', '')}"
    exists = db.scalar(
        select(func.count()).select_from(ReconcileCheck).where(
            ReconcileCheck.check_no.like(f"{prefix}%")
        )
    ) or 0
    return f"{prefix}{exists + 1:03d}"


# ─────────────────────────────────────────────────────────────
# 2. 逐路对齐(A-20260909-002 LOCKED): 以 E 为基准调校 ABCD
# ─────────────────────────────────────────────────────────────
def per_route_alignment(db, start: datetime, end: datetime) -> dict:
    """逐路对齐汇总(A-20260909-002 §〇/§2.2, 用户 2026-09-09)。

    顶层口径: E(商户号真实退款流水)=财务权威基准, 用 E 调校 ABCD 运营登记台账。
    对每一路只报: 已对齐(该路登记在 E 能找到) / 误记差异(该路有 E 无, 疑误操作/残留)。
    "漏记/空悬"(E 有 ABCD 无) 与 A∩D 歧义 归入 issues 单列。
    - A: 仅 refund_method='线上'(转入提现账户/线下 不对 E, §一·5)
    - B: 仅 op_type='用户提现'(链路: remark RF → A转入(refund_no) → platform_order_no → E)
    - C: 闪猫已退款(WXALR order_no → E)
    - D: 手动已退款 剔 dup_residual/ignored, 剔 A线上 已承接(歧义另列)
    匹配 E 不限窗口(键匹配即真实水单; E 覆盖起点前历史单由前端 e_data_until 提示)。

    ⚠️ SQL 全部参数化(:s/:e 绑定), 不拼接用户/时间输入(AGENTS 铁律 2)。
    """
    from sqlalchemy import text

    start_u, end_u = _to_utc_aware(start), _to_utc_aware(end)
    e_ok = "r.refund_status IN ('退款成功','成功','SUCCESS')"

    def _count(sql_tpl: str, extra: dict | None = None) -> int:
        params = {"s": start_u, "e": end_u}
        if extra:
            params.update(extra)
        return int(db.execute(text(sql_tpl), params).scalar() or 0)

    # ── A 线上直退(仅 refund_method='线上') ──
    a_total = _count("""
        SELECT count(*) FROM refunds a
        WHERE a.status='已退款' AND a.refund_method='线上'
          AND a.created_at_raw >= :s AND a.created_at_raw <= :e""")
    a_in_e = _count(f"""
        SELECT count(*) FROM refunds a
        WHERE a.status='已退款' AND a.refund_method='线上'
          AND a.created_at_raw >= :s AND a.created_at_raw <= :e
          AND EXISTS (SELECT 1 FROM refund_record_reports r
             WHERE r.platform_order_no = a.platform_order_no AND {e_ok})""")

    # ── B 用户提现(链路: remark 退费单号 → A转入单 refund_no → platform_order_no → E) ──
    # ⚠️ 2026-09-09 修复: 退费单号前缀含 RF/RE/RB, 原只认 RF 漏 RE(用户案例 99元) → 扩 R[FEB]
    b_total = _count("""
        SELECT count(*) FROM wallet_withdrawals w
        WHERE w.op_type='用户提现' AND w.op_time >= :s AND w.op_time <= :e
          AND (w.remark IS NULL OR NOT (
              w.remark LIKE '%冻结%' OR w.remark LIKE '%提现失败%' OR w.remark LIKE '%退款失败%'
              OR w.remark LIKE '%驳回%' OR w.remark LIKE '%拒绝%' OR w.remark LIKE '%未到账%'
              OR w.remark LIKE '%超期订单不允许退款%'))""")
    b_chain_ok = _count(f"""
        SELECT count(*) FROM wallet_withdrawals w
        WHERE w.op_type='用户提现' AND w.op_time >= :s AND w.op_time <= :e
          AND w.remark ~ 'R[FEB][0-9]+'
          AND EXISTS (SELECT 1 FROM refunds a
             JOIN refund_record_reports r ON r.platform_order_no = a.platform_order_no
             WHERE a.refund_no = substring(w.remark from 'R[FEB][0-9]+')
               AND a.refund_method='转入提现账户' AND a.status='已退款' AND {e_ok})""")

    # ── C 闪猫已退款(order_no(WXALR) → E) ──
    c_total = _count("""
        SELECT count(*) FROM shanmao_refunds c
        WHERE c.status='已退款'
          AND COALESCE(c.audit_time, c.created_at) >= :s AND COALESCE(c.audit_time, c.created_at) <= :e""")
    c_in_e = _count(f"""
        SELECT count(*) FROM shanmao_refunds c
        WHERE c.status='已退款'
          AND COALESCE(c.audit_time, c.created_at) >= :s AND COALESCE(c.audit_time, c.created_at) <= :e
          AND c.order_no IS NOT NULL
          AND EXISTS (SELECT 1 FROM refund_record_reports r
             WHERE r.platform_order_no = c.order_no AND {e_ok})""")

    # ── D 手动已退款(剔 dup/ignored; order_no → E) ──
    d_base = """
        SELECT count(*) FROM manual_refunds m
        WHERE m.is_refunded='是'
          AND (m.quality_status IS NULL OR m.quality_status NOT IN ('dup_residual','ignored'))
          AND COALESCE(m.op_time, m.register_time) >= :s AND COALESCE(m.op_time, m.register_time) <= :e
    """
    d_total = _count(d_base)
    d_in_e = _count(d_base + f"""
        AND m.order_no IS NOT NULL
        AND EXISTS (SELECT 1 FROM refund_record_reports r
           WHERE r.platform_order_no = m.order_no AND {e_ok})""")

    # D 中与 A线上已退款重叠(order_no 也记在 A) = A∩D 归属歧义(单列, 不并入 D 差异)
    d_overlap_a = _count(d_base + """
        AND m.order_no IS NOT NULL
        AND EXISTS (SELECT 1 FROM refunds a
           WHERE a.platform_order_no = m.order_no AND a.status='已退款' AND a.refund_method='线上')""")

    # ── issues: E 空悬(窗口内 E 单无任何路承接) ──
    # 空悬细分(2026-09-09 用户需求): 归属判断(空悬大概率=某路漏登记)
    #   - WXALR 前缀 → C 路(闪猫)采集缺/漏记
    #   - 近2天 → E 采集延迟, 待复查
    #   - 历史非WXALR → 疑 D 路(手动)少登记(用户: ABC 无空悬且非近2天 → 大概率 D 漏登)
    e_total_win = _count(f"""
        SELECT count(*) FROM refund_record_reports r
        WHERE {e_ok} AND r.refund_time >= :s AND r.refund_time <= :e""")
    _orphan_where = """
          AND NOT EXISTS (SELECT 1 FROM refunds a WHERE a.platform_order_no = r.platform_order_no AND a.status='已退款')
          AND NOT EXISTS (SELECT 1 FROM manual_refunds m WHERE m.order_no = r.platform_order_no AND m.is_refunded='是')
          AND NOT EXISTS (SELECT 1 FROM shanmao_refunds c WHERE c.order_no = r.platform_order_no AND c.status='已退款')"""
    _orphan_base = f"""
        SELECT count(*) FROM refund_record_reports r
        WHERE {e_ok} AND r.refund_time >= :s AND r.refund_time <= :e""" + _orphan_where
    e_orphan = _count(_orphan_base)
    e_orphan_wxalr = _count(_orphan_base + " AND r.platform_order_no LIKE 'WXALR%'")
    # 近2天(E 最新退款日之前 2 天内 → 可能延迟): 以 end_u-2天 为界
    recent_iso = (end_u - timedelta(days=2)).isoformat()
    e_orphan_recent = _count(_orphan_base + " AND r.refund_time >= :recent",
                             extra={"recent": recent_iso})
    e_orphan_wxalr_recent = _count(_orphan_base + " AND r.platform_order_no LIKE 'WXALR%' AND r.refund_time >= :recent",
                                   extra={"recent": recent_iso})

    return {
        "e_data_until": _e_data_until(db),
        "routes": {
            "A": {"method": "线上", "total": a_total, "matched_in_e": a_in_e,
                  "unmatched": a_total - a_in_e},
            "B": {"method": "用户提现", "total": b_total, "chain_ok": b_chain_ok,
                  "chain_break": b_total - b_chain_ok},
            "C": {"total": c_total, "matched_in_e": c_in_e, "unmatched": c_total - c_in_e},
            "D": {"total": d_total, "matched_in_e": d_in_e, "unmatched": d_total - d_in_e,
                  "overlap_a": d_overlap_a},
        },
        "issues": {
            "e_orphan": e_orphan,
            # 空悬归属细分: wxalr=C(闪猫)缺源; recent=E延迟近端; 其余历史疑D少登记
            "e_orphan_wxalr": e_orphan_wxalr,
            "e_orphan_recent": e_orphan_recent,
            "e_orphan_wxalr_recent": e_orphan_wxalr_recent,
            "a_d_overlap": d_overlap_a,
            "e_total_in_window": e_total_win,
        },
    }


def alignment_detail(db, route: str, start: datetime, end: datetime,
                     page: int = 1, page_size: int = 50) -> dict:
    """逐路对齐差异明细(A-20260909-002 §2.3, 一期只读)。

    route ∈ {A, B, C, D, orphan}: 返回该路"以 E 为基准未对齐/空悬"的经营单/水单列表。
    - A: A线上已退款, created 在窗, 但 platform_order_no 在 E(成功)找不到 → 疑误记/漏采
    - B: B用户提现 op 在窗, 链路(RF→A转入→E)断 → chain_break
    - C: C已退款 窗内, order_no 在 E 找不到
    - D: D已退款(非 dup/ignored) 窗内, order_no 在 E 找不到(不含与 A线上 重叠者)
    - orphan: E 成功水单 窗内, 无任何路承接
    匹配 E 不限窗。返回分页明细(供前端下钻展示)。
    """
    from sqlalchemy import text

    start_u, end_u = _to_utc_aware(start), _to_utc_aware(end)
    e_ok = "r.refund_status IN ('退款成功','成功','SUCCESS')"
    params = {"s": start_u, "e": end_u, "lim": page_size, "off": (page - 1) * page_size}
    route = (route or "").strip().upper()

    if route == "A":
        sql = f"""
            SELECT a.refund_no AS biz_no, a.platform_order_no AS order_no, a.device_sn,
                   a.refund_amount AS amount, a.created_at_raw AS occur_time, a.operator AS who
            FROM refunds a
            WHERE a.status='已退款' AND a.refund_method='线上'
              AND a.created_at_raw >= :s AND a.created_at_raw <= :e
              AND NOT EXISTS (SELECT 1 FROM refund_record_reports r WHERE r.platform_order_no=a.platform_order_no AND {e_ok})
            ORDER BY a.created_at_raw DESC LIMIT :lim OFFSET :off"""
    elif route == "B":
        sql = """
            SELECT w.id AS biz_no, w.remark AS order_no, w.device_sn,
                   w.op_amount AS amount, w.op_time AS occur_time, w.operator AS who
            FROM wallet_withdrawals w
            WHERE w.op_type='用户提现' AND w.op_time >= :s AND w.op_time <= :e
              AND (w.remark IS NULL OR NOT (
                  w.remark LIKE '%冻结%' OR w.remark LIKE '%提现失败%' OR w.remark LIKE '%退款失败%'
                  OR w.remark LIKE '%驳回%' OR w.remark LIKE '%拒绝%' OR w.remark LIKE '%未到账%'
                  OR w.remark LIKE '%超期订单不允许退款%'))
              AND NOT (w.remark ~ 'R[FEB][0-9]+' AND EXISTS (
                  SELECT 1 FROM refunds a JOIN refund_record_reports r ON r.platform_order_no=a.platform_order_no
                  WHERE a.refund_no=substring(w.remark from 'R[FEB][0-9]+')
                    AND a.refund_method='转入提现账户' AND a.status='已退款' AND """ + e_ok + """))
            ORDER BY w.op_time DESC LIMIT :lim OFFSET :off"""
    elif route == "C":
        sql = f"""
            SELECT c.refund_no AS biz_no, c.order_no AS order_no, c.device_sn,
                   c.actual_amount AS amount, COALESCE(c.audit_time, c.created_at) AS occur_time, c.operator AS who
            FROM shanmao_refunds c
            WHERE c.status='已退款'
              AND COALESCE(c.audit_time, c.created_at) >= :s AND COALESCE(c.audit_time, c.created_at) <= :e
              AND NOT EXISTS (SELECT 1 FROM refund_record_reports r WHERE r.platform_order_no=c.order_no AND {e_ok})
            ORDER BY COALESCE(c.audit_time, c.created_at) DESC LIMIT :lim OFFSET :off"""
    elif route == "D":
        sql = f"""
            SELECT m.id AS biz_no, m.order_no AS order_no, m.device_sn,
                   m.refund_amount AS amount, COALESCE(m.op_time, m.register_time) AS occur_time,
                   COALESCE(m.refund_operator, m.registrant) AS who, m.quality_status AS q
            FROM manual_refunds m
            WHERE m.is_refunded='是'
              AND (m.quality_status IS NULL OR m.quality_status NOT IN ('dup_residual','ignored'))
              AND COALESCE(m.op_time, m.register_time) >= :s AND COALESCE(m.op_time, m.register_time) <= :e
              AND NOT EXISTS (SELECT 1 FROM refund_record_reports r WHERE r.platform_order_no=m.order_no AND {e_ok})
            ORDER BY COALESCE(m.op_time, m.register_time) DESC LIMIT :lim OFFSET :off"""
    elif route == "ORPHAN":
        sql = f"""
            SELECT r.id AS biz_no, r.platform_order_no AS order_no, r.device_sn,
                   r.refund_amount AS amount, r.refund_time AS occur_time, r.merchant_name AS who
            FROM refund_record_reports r
            WHERE {e_ok} AND r.refund_time >= :s AND r.refund_time <= :e
              AND NOT EXISTS (SELECT 1 FROM refunds a WHERE a.platform_order_no=r.platform_order_no AND a.status='已退款')
              AND NOT EXISTS (SELECT 1 FROM manual_refunds m WHERE m.order_no=r.platform_order_no AND m.is_refunded='是')
              AND NOT EXISTS (SELECT 1 FROM shanmao_refunds c WHERE c.order_no=r.platform_order_no AND c.status='已退款')
            ORDER BY r.refund_time DESC LIMIT :lim OFFSET :off"""
    else:
        raise ValueError(f"route 非法: {route}, 应为 A/B/C/D/orphan")

    rows = db.execute(text(sql), params).all()
    items = []
    recent_bound = end_u - timedelta(days=2)  # 近2天判定界(UTC aware)
    for r in rows:
        ono = r.order_no or ""
        sugg = None
        if route == "ORPHAN":
            # 空悬归属建议(2026-09-09 用户): WXALR→C缺源; 近2天→E延迟; 历史→疑D少登记
            is_recent = r.occur_time is not None and r.occur_time >= recent_bound
            if str(ono).startswith("WXALR"):
                sugg = ("近2天" if is_recent else "历史") + " · 疑 C 路(闪猫)漏记/采集缺(WXALR 单)"
            elif is_recent:
                sugg = "近2天 · E 采集延迟, 待复查(补采后可能消失)"
            else:
                sugg = "历史 · ABC 无此单且非近2天, 大概率 D 路(手动)少登记, 建议补录到 D"
        items.append({
            "biz_no": r.biz_no if r.biz_no is not None else str(r.order_no or ""),
            "order_no": r.order_no,
            "device_sn": r.device_sn,
            "amount": float(r.amount) if r.amount is not None else None,
            "time": _to_cn(r.occur_time).isoformat() if r.occur_time else None,
            "who": r.who,
            "quality_status": getattr(r, "q", None),
            "suggest": sugg,
        })
    return {"route": route, "page": page, "page_size": page_size, "items": items,
            "count": len(items), "has_more": len(items) == page_size}


def build_check_payload(db, start: datetime, end: datetime, grain: str) -> dict:
    """生成复核落库所需快照(route_snapshot / by_route_of_e / dupe_detect)"""
    start_u = _to_utc_aware(start)
    end_u = _to_utc_aware(end)
    return {
        "route_snapshot": _route_snapshot(db, start_u, end_u),
        "by_route_of_e": _e_by_route(db, start_u, end_u),
        "dupe_detect": _dupe_detect(db, start_u, end_u),
    }


def _e_data_until(db) -> str | None:
    """E 路(refund_record_reports)退款成功数据最新日期(北京)。"""
    from sqlalchemy import text

    e_max = db.execute(text(
        "SELECT max(refund_time) FROM refund_record_reports "
        "WHERE refund_status IN ('退款成功', '成功', 'SUCCESS')"
    )).scalar()
    if e_max is None:
        return None
    return e_max.astimezone(_TZ_CN).strftime("%Y-%m-%d")


def preview_check(db, start: datetime, end: datetime, grain: str = "day") -> dict:
    """生成对账预览(不落库)"""
    payload = build_check_payload(db, start, end, grain)
    return {
        "window": {
            "start": _to_cn(_to_utc_aware(start)).isoformat(),
            "end": _to_cn(_to_utc_aware(end)).isoformat(),
            "grain": grain,
        },
        # E 数据截止日(北京): 窗口晚于该日时 E 侧未采全, 偏差仅供参考(前端警示用)
        "e_data_until": _e_data_until(db),
        **payload,
    }


# ─────────────────────────────────────────────────────────────
# 2. 复核批次落库 / 查询 / 改结论
# ─────────────────────────────────────────────────────────────
def _user_display_name(user) -> str | None:
    """取用户显示名(User.display_name → username 兜底)"""
    if user is None:
        return None
    return getattr(user, "display_name", None) or getattr(user, "username", None)


def confirm_check(db, user, start: datetime, end: datetime, grain: str = "day",
                  conclusion: str | None = None, note: str | None = None) -> ReconcileCheck:
    """发起复核: 冻结时点快照落库 reconcile_checks"""
    if conclusion and conclusion not in CONCLUSIONS:
        raise ValueError(f"conclusion 非法: {conclusion}, 应为 {CONCLUSIONS}")
    start_u = _to_utc_aware(start)
    end_u = _to_utc_aware(end)
    if start_u >= end_u:
        raise ValueError("start 必须早于 end")

    payload = build_check_payload(db, start_u, end_u, grain)
    check = ReconcileCheck(
        check_no=_gen_check_no(db, _to_cn(start_u).strftime("%Y-%m-%d")),
        window_start=start_u,
        window_end=end_u,
        grain=grain,
        route_snapshot=payload["route_snapshot"],
        by_route_of_e=payload["by_route_of_e"],
        dupe_detect=payload["dupe_detect"],
        conclusion=conclusion,
        note=note,
        reviewed_by=user.id if user else None,
        reviewed_name=_user_display_name(user),
        reviewed_at=datetime.now(timezone.utc),
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    return check


def list_checks(db, page: int = 1, page_size: int = 20, conclusion: str | None = None) -> dict:
    """复核批次列表(新→旧)"""
    q = select(ReconcileCheck)
    if conclusion:
        q = q.where(ReconcileCheck.conclusion == conclusion)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.order_by(ReconcileCheck.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_ser_check(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_check(db, check_id: int) -> ReconcileCheck | None:
    return db.get(ReconcileCheck, check_id)


def set_conclusion(db, user, check_id: int, conclusion: str | None = None,
                   note: str | None = None) -> ReconcileCheck:
    """补/改结论与备注(留痕: 记录复核人/时间)"""
    if conclusion and conclusion not in CONCLUSIONS:
        raise ValueError(f"conclusion 非法: {conclusion}, 应为 {CONCLUSIONS}")
    check = db.get(ReconcileCheck, check_id)
    if not check:
        raise ValueError("复核批次不存在")
    if conclusion is not None:
        check.conclusion = conclusion
    if note is not None:
        check.note = note
    check.reviewed_by = user.id if user else None
    check.reviewed_name = _user_display_name(user)
    check.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(check)
    return check


# ─────────────────────────────────────────────────────────────
# 序列化
# ─────────────────────────────────────────────────────────────
def _ser_check(r: ReconcileCheck) -> dict:
    return {
        "id": r.id,
        "check_no": r.check_no,
        "window_start": _to_cn(r.window_start).isoformat() if r.window_start else None,
        "window_end": _to_cn(r.window_end).isoformat() if r.window_end else None,
        "grain": r.grain,
        "route_snapshot": r.route_snapshot,
        "by_route_of_e": r.by_route_of_e,
        "dupe_detect": r.dupe_detect,
        "conclusion": r.conclusion,
        "note": r.note,
        "reviewed_by": r.reviewed_by,
        "reviewed_name": r.reviewed_name,
        "reviewed_at": _to_cn(r.reviewed_at).isoformat() if r.reviewed_at else None,
        "created_at": _to_cn(r.created_at).isoformat() if r.created_at else None,
    }


# ─────────────────────────────────────────────────────────────
# 逐笔勾稽明细核对 (A-20260906-002 v1.1 / #22)
# E 水单每笔 → 自动归类 + 候选 ABCD 经营单 → 人工确认(仅打标, 不改源)
# ─────────────────────────────────────────────────────────────
def _route_candidates(db, report: RefundRecordReport) -> dict:
    """某 E 笔的候选匹配 ABCD 经营单明细(供人工确认)。

    复用 water_statement 分类语义(订单号命中 A/D、设备+金额命中 D/B/C), 但返回
    更细的候选列表(matched_ref), 而非仅 route 标签。只读不改源。
    """
    from app.services.reconcile import _build_route_maps, _classify_report, _is_success_status

    if not _is_success_status(report.refund_status):
        return {"auto_route": None, "candidates": []}

    order_no = (report.platform_order_no or "").strip()
    sn = (report.device_sn or "").strip()
    amt = float(report.refund_amount) if report.refund_amount is not None else None

    maps = _build_route_maps(db, None, None)
    auto_route = _classify_report(report, maps)

    candidates: list[dict] = []
    # A 路: platform_order_no 命中退费单
    if order_no:
        a_rows = db.execute(
            select(Refund.refund_no, Refund.refund_amount, Refund.device_sn)
            .where(Refund.platform_order_no == order_no)
            .limit(10)
        ).all()
        for rno, ramt, rsn in a_rows:
            candidates.append({
                "route": "A", "source": "refunds", "ref_no": rno,
                "amount": float(ramt) if ramt is not None else None, "sn": rsn,
            })
    # D 路: order_no 命中手动台账
    if order_no:
        d_rows = db.execute(
            select(ManualRefund.id, ManualRefund.refund_amount, ManualRefund.device_sn,
                   ManualRefund.registrant)
            .where(ManualRefund.order_no == order_no, ManualRefund.is_refunded == "是")
            .limit(10)
        ).all()
        for mid, mamt, msn, mreg in d_rows:
            candidates.append({
                "route": "D", "source": "manual_refunds", "source_id": mid,
                "ref_no": order_no, "amount": float(mamt) if mamt is not None else None,
                "sn": msn, "extra": mreg,
            })
    # B 路: 设备+金额 命中钱包提现
    if sn and amt is not None:
        b_rows = db.execute(
            select(WalletWithdrawal.id, WalletWithdrawal.op_amount, WalletWithdrawal.remark)
            .where(WalletWithdrawal.device_sn == sn, WalletWithdrawal.op_type == "用户提现")
            .limit(10)
        ).all()
        for wid, wamt, wremark in b_rows:
            if wamt is not None and abs(float(wamt) - amt) <= 0.01:
                candidates.append({
                    "route": "B", "source": "wallet_withdrawals", "source_id": wid,
                    "amount": float(wamt), "sn": sn, "extra": (wremark or "")[:40],
                })
    # C 路: 订单号(WXALR)或设备命中闪猫已退款
    # ⚠️ 2026-09-09 (M-4): C 路 device_sn 覆盖 0%, 原仅 SN 匹配全漏配 → 优先 order_no(WXALR)
    if order_no:
        c_rows = db.execute(
            select(ShanmaoRefund.refund_no, ShanmaoRefund.actual_amount, ShanmaoRefund.device_sn)
            .where(ShanmaoRefund.order_no == order_no, ShanmaoRefund.status == "已退款")
            .limit(10)
        ).all()
        for cno, camt, csn in c_rows:
            candidates.append({
                "route": "C", "source": "shanmao_refunds", "source_id": cno,
                "amount": float(camt) if camt is not None else None, "sn": csn,
            })
    if sn:
        c_rows = db.execute(
            select(ShanmaoRefund.refund_no, ShanmaoRefund.actual_amount)
            .where(ShanmaoRefund.device_sn == sn, ShanmaoRefund.status == "已退款")
            .limit(10)
        ).all()
        for cno, camt in c_rows:
            candidates.append({
                "route": "C", "source": "shanmao_refunds", "source_id": cno,
                "amount": float(camt) if camt is not None else None, "sn": sn,
            })

    return {"auto_route": auto_route, "candidates": candidates}


def ensure_line(db, check_id: int | None, report: RefundRecordReport) -> ReconcileMatch:
    """确保 reconcile_matches 有该 E 笔的记录(不存在则按自动归类创建)。"""
    line = db.scalar(
        select(ReconcileMatch).where(
            ReconcileMatch.check_id == check_id,
            ReconcileMatch.report_id == report.id,
        )
    )
    if line:
        return line
    cand = _route_candidates(db, report)
    line = ReconcileMatch(
        check_id=check_id,
        report_id=report.id,
        platform_order_no=report.platform_order_no,
        refund_amount=report.refund_amount,
        refund_time=report.refund_time,
        auto_route=cand["auto_route"],
        matched_ref=cand["candidates"],
        confirm_status="待核对",
    )
    db.add(line)
    db.commit()
    db.refresh(line)
    return line


def list_lines(db, check_id: int, route: str | None = None, status: str | None = None,
               page: int = 1, page_size: int = 50) -> dict:
    """批次逐笔勾稽列表(E 笔 + auto_route + 人工状态)。"""
    check = db.get(ReconcileCheck, check_id)
    if not check:
        raise ValueError("复核批次不存在")

    q = select(ReconcileMatch).where(ReconcileMatch.check_id == check_id)
    if route:
        q = q.where(ReconcileMatch.auto_route == route)
    if status:
        q = q.where(ReconcileMatch.confirm_status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.order_by(ReconcileMatch.refund_time.desc().nullslast(), ReconcileMatch.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [_ser_line(r) for r in rows], "total": total,
            "page": page, "page_size": page_size}


def lines_summary(db, check_id: int) -> dict:
    """批次内逐笔确认进度。"""
    rows = db.execute(
        select(ReconcileMatch.auto_route, ReconcileMatch.confirm_status, func.count())
        .where(ReconcileMatch.check_id == check_id)
        .group_by(ReconcileMatch.auto_route, ReconcileMatch.confirm_status)
    ).all()
    by_route: dict[str, int] = {}
    by_status: dict[str, int] = {}
    total = 0
    confirmed = 0
    for route, status, cnt in rows:
        total += int(cnt)
        by_route[route or "?"] = by_route.get(route or "?", 0) + int(cnt)
        st = status or "待核对"
        by_status[st] = by_status.get(st, 0) + int(cnt)
        if st != "待核对":
            confirmed += int(cnt)
    return {"total": total, "confirmed": confirmed, "by_route": by_route,
            "by_status": by_status}


def confirm_line(db, user, check_id: int, report_id: int, confirm_status: str,
                 confirm_note: str | None = None) -> ReconcileMatch:
    """对单笔打确认状态(仅写 reconcile_matches, 不改任何源表)。"""
    if confirm_status not in CONFIRM_STATUSES:
        raise ValueError(f"confirm_status 非法: {confirm_status}, 应为 {CONFIRM_STATUSES}")
    check = db.get(ReconcileCheck, check_id)
    if not check:
        raise ValueError("复核批次不存在")
    report = db.get(RefundRecordReport, report_id)
    if not report:
        raise ValueError("E 退款记录报表不存在")

    line = ensure_line(db, check_id, report)
    line.confirm_status = confirm_status
    if confirm_note is not None:
        line.confirm_note = confirm_note
    line.confirmed_by = user.id if user else None
    line.confirmed_name = _user_display_name(user)
    line.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(line)
    return line


def _ser_line(r: ReconcileMatch) -> dict:
    return {
        "id": r.id,
        "check_id": r.check_id,
        "report_id": r.report_id,
        "platform_order_no": r.platform_order_no,
        "refund_amount": float(r.refund_amount) if r.refund_amount is not None else None,
        "refund_time": _to_cn(r.refund_time).isoformat() if r.refund_time else None,
        "auto_route": r.auto_route,
        "matched_ref": r.matched_ref or [],
        "confirm_status": r.confirm_status,
        "confirm_note": r.confirm_note,
        "confirmed_name": r.confirmed_name,
        "confirmed_at": _to_cn(r.confirmed_at).isoformat() if r.confirmed_at else None,
    }
