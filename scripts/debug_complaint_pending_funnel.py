#!/usr/bin/env python3
"""离线调试脚本: 近7天三张投诉表漏斗统计 - 验证 complaint_pending_list 为啥只有 13 条 & 商户号投诉没显示

运行方式 (在 backend 目录下,用项目 venv):
  cd /Users/tanzk/dev/csctalon/backend && ./.venv/bin/python ../scripts/debug_complaint_pending_funnel.py
"""
from __future__ import annotations

import sys
import os
import datetime as dt
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

# ── 把 backend/app 包加入 sys.path ──
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from sqlalchemy import and_, func, literal, or_, select, text
from app.core.database import SessionLocal
from app.models.ticket import ServiceTicket
from app.models.external_complaint import ExternalComplaint
from app.models.card_complaint import CardComplaint

TZ = ZoneInfo("Asia/Shanghai")
LOOKBACK = 7  # 倒查 7 天 (和生产代码 PENDING_LOOKBACK_DAYS 一致)

# ── 和生产代码完全一致的关键词 & 映射规则 ──
_EXTERNAL_ITEM_COMPLAINT_KW = (
    "12315", "工信部", "消协", "公共服务平台", "消费者协会",
    "监督局", "市场监管", "市监", "315平台", "12345", "政务平台",
)
ST_KWS = _EXTERNAL_ITEM_COMPLAINT_KW + ("商品投诉", "服务投诉", "微信投诉")


def _to_status_bucket(raw_st):
    rs = raw_st or ""
    if any(k in rs for k in ["待处理", "待接单", "待派", "未处理", "new", "open"]):
        return "待处理"
    if any(k in rs for k in ["处理中", "进行中", "处理", "running", "dispatch", "已派"]):
        return "处理中"
    if any(k in rs for k in ["已完成", "完成", "办结", "关闭", "closed", "done", "finish", "有效", "已处理"]):
        return "已完成"
    if any(k in rs for k in ["终止", "取消", "取消处理", "无效", "拒绝", "reject"]):
        return "已终止"
    return "未标注"


def _is_closed(bucket):
    """和生产逻辑一致: 已完成/已终止 → 闭环(在后处理阶段剔除列表)"""
    return bucket in ("已完成", "已终止")


def _clean_handler(raw):
    if not raw:
        return "未分配"
    return (raw.removeprefix("企微-").removeprefix("企微").strip()) or raw or "未分配"


def main():
    now_sh = dt.datetime.now(TZ)
    today_d = now_sh.date()
    te_pending = now_sh.replace(tzinfo=None)  # naive UTC aligned to DB storage
    # 视图起点 = 今天 (模拟用户今日视图, 倒查窗口 = te_pending - 6 天 因为含今天共 7 天)
    ts_pending_naive = te_pending - dt.timedelta(days=LOOKBACK - 1)
    # 转为 Asia/Shanghai 00:00 起点 (和 analytics._resolve_windows 对齐)
    start_sh = dt.datetime.combine(today_d - dt.timedelta(days=LOOKBACK - 1), dt.time(0, 0, 0), tzinfo=TZ)
    end_sh = now_sh
    _utc = dt.timezone.utc
    ts_pending = start_sh.astimezone(_utc).replace(tzinfo=None)
    te_pending = end_sh.astimezone(_utc).replace(tzinfo=None)

    print(f"{'=' * 80}")
    print(f"近 {LOOKBACK} 天投诉漏斗统计   (窗口: {start_sh.isoformat()} ~ {end_sh.isoformat()})")
    print(f"{'=' * 80}\n")

    db = SessionLocal()
    try:
        # ═══════════════════════════════════════════════════════════
        # A. service_tickets
        # ═══════════════════════════════════════════════════════════
        print("【A】service_tickets (内部投诉工单类)")
        print("-" * 60)
        # A0. 近7天所有工单总数 (作为分母参考)
        a0 = db.execute(select(func.count()).where(
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= ts_pending,
            ServiceTicket.register_time <= te_pending,
        )).scalar() or 0
        print(f"  A0. 近 {LOOKBACK} 天所有工单数(全部类别):            {a0:>6}")

        # A1. 投诉判定 WHERE 命中 (和生产代码完全一致的 st_where)
        st_where = and_(
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= ts_pending,
            ServiceTicket.register_time <= te_pending,
            or_(
                ServiceTicket.service_category.is_(None),
                ServiceTicket.service_category.ilike("%投诉%"),
                or_(*[ServiceTicket.service_item.ilike(f"%{k}%") for k in ST_KWS]),
            ),
        )
        a1 = db.execute(select(func.count()).where(st_where)).scalar() or 0
        print(f"  A1. 投诉判定 WHERE 匹配(第一步进入候选):       {a1:>6}" + (f"  ⚠ 如果这个就很小 → H1成立(判定过严)" if a1 < 50 else ""))

        # A1-补充: 看看被投诉判定 WHERE 过滤掉的那些工单 (总数 A0-A1) 里, service_item / service_category 分布 TOP20
        # → 用于判断有没有「微信投诉/商户号投诉」之类的变体没被我的关键词覆盖
        if a0 - a1 > 0:
            _non_match_sql = text("""
                SELECT COALESCE(service_category, 'NULL'),
                       COALESCE(service_item, 'NULL'),
                       COUNT(*) AS c
                FROM service_tickets
                WHERE register_time IS NOT NULL
                  AND register_time >= :ts AND register_time <= :te
                  AND NOT (
                    service_category IS NULL
                    OR service_category ILIKE '%投诉%'
                    OR service_item ILIKE ANY (ARRAY[
                        '%12315%','%工信部%','%消协%','%公共服务平台%','%消费者协会%',
                        '%监督局%','%市场监管%','%市监%','%315平台%','%12345%','%政务平台%',
                        '%商品投诉%','%服务投诉%','%微信投诉%'
                    ])
                  )
                GROUP BY 1, 2
                ORDER BY c DESC
                LIMIT 20;
            """)
            nm_rows = db.execute(_non_match_sql, {"ts": ts_pending, "te": te_pending}).fetchall()
            print(f"  ┌─ WHERE 被过滤的 {a0 - a1} 条中, service_category/service_item TOP20:")
            for i, r in enumerate(nm_rows, 1):
                print(f"  │   {i:>2}. [{r[0]:<20}] / [{r[1]:<30}] → {r[2]:>5} 条")
            print(f"  │   (⚠ 如果这里出现 微信/商户号/投诉 字样 → H1 板上钉钉:关键词缺漏)")

        # A2. 拉 A1 出来, 统计 status 分布 & 映射后被算成"已闭环/未闭环"的数量
        a2_rows = db.execute(select(
            ServiceTicket.ticket_no,
            ServiceTicket.service_item,
            ServiceTicket.service_category,
            ServiceTicket.status,
        ).where(st_where)).mappings().all()

        status_raw_counter: Counter = Counter()   # 原始 status 字符串计数
        status_bucket_counter: Counter = Counter() # 映射后 bucket 计数
        closed_by_misjudge = []  # 被算成 已完成/已终止 的原始 status, 看有没有误判
        still_pending_count = 0
        for r in a2_rows:
            raw = r["status"] or "NULL_STATUS"
            status_raw_counter[raw] += 1
            bucket = _to_status_bucket(r["status"])
            status_bucket_counter[bucket] += 1
            if _is_closed(bucket):
                closed_by_misjudge.append((raw, r["service_item"] or r["service_category"] or ""))
            else:
                still_pending_count += 1

        print(f"  A2. A1候选中 status 原始值分布 TOP10:")
        for i, (raw, c) in enumerate(status_raw_counter.most_common(10), 1):
            b = _to_status_bucket(None if raw == "NULL_STATUS" else raw)
            mark = " ⚠ 被算成已闭环!" if _is_closed(b) else ""
            print(f"       {i:>2}. [{raw:<25}]  {c:>5} 条  → bucket={b}" + mark)
        print(f"  A3. status bucket 汇总:")
        for b in ["待处理", "处理中", "未标注", "已完成", "已终止"]:
            c = status_bucket_counter.get(b, 0)
            print(f"       - {b:<8}: {c:>5} 条" + ("  (后处理阶段将被剔除出列表)" if _is_closed(b) else ""))
        print(f"  ➡  service_tickets 最终进入列表(未闭环): {still_pending_count} 条")

        # ═══════════════════════════════════════════════════════════
        # B. external_complaints (外部投诉)
        # ═══════════════════════════════════════════════════════════
        print("\n【B】external_complaints (外部投诉 12315/工信部/微信商户号等)")
        print("-" * 60)
        ec_where = and_(
            ExternalComplaint.complaint_date.isnot(None),
            ExternalComplaint.complaint_date >= ts_pending,
            ExternalComplaint.complaint_date <= te_pending,
        )
        b0 = db.execute(select(func.count()).where(ec_where)).scalar() or 0
        print(f"  B0. 近 {LOOKBACK} 天 external_complaints 总记录数:        {b0:>6}")

        # B0-补充: platform 分布(找出有没有 微信商户号/微信支付 这类独立 platform)
        pf_rows = db.execute(select(
            func.coalesce(ExternalComplaint.platform, 'NULL_PLATFORM'),
            func.count().label("c"),
        ).where(ec_where).group_by(func.coalesce(ExternalComplaint.platform, 'NULL_PLATFORM')
        ).order_by(text("c DESC"))).all()
        print(f"  ┌─ platform 分布 (若存在'微信/商户号'字样, 即为用户说的那一批):")
        for i, (pf, c) in enumerate(pf_rows, 1):
            print(f"  │   {i:>2}. [{pf:<30}] → {c:>5} 条")

        # B1. 取每条的 result/handle_time/handle_detail → 映射 bucket 分布
        b1_rows = db.execute(select(
            ExternalComplaint.platform,
            ExternalComplaint.result,
            ExternalComplaint.handle_time,
            ExternalComplaint.handle_detail,
            ExternalComplaint.complaint_category,
        ).where(ec_where)).mappings().all()

        b_result_counter: Counter = Counter()
        b_bucket_counter: Counter = Counter()
        b_closed = []  # 被算成已完成/已终止的, 看 result/handle_time 组合是不是误判
        b_pending_count = 0
        for r in b1_rows:
            # 完全复刻生产逻辑的 external status 推断
            rs_raw = ""
            if (r["result"] or "").find("有效") >= 0:
                rs_raw = "已完成"
            elif (r["result"] or "").find("无效") >= 0:
                rs_raw = "已终止"
            elif (r["result"] or "").find("处理") >= 0:
                rs_raw = "处理中"
            elif r["handle_time"] is not None:
                rs_raw = "已完成"
            elif r["handle_detail"]:
                rs_raw = "处理中"
            else:
                rs_raw = "未标注"
            bucket = _to_status_bucket(rs_raw)
            b_result_counter[r["result"] or "NULL_RESULT"] += 1
            b_bucket_counter[bucket] += 1
            if _is_closed(bucket):
                b_closed.append((r["result"] or "", r["platform"] or "", bool(r["handle_time"]), bool(r["handle_detail"])))
            else:
                b_pending_count += 1

        print(f"  B1. result 原始值分布 TOP10:")
        for i, (raw, c) in enumerate(b_result_counter.most_common(10), 1):
            # 重新推断 bucket
            rs_raw = ""
            if raw.find("有效") >= 0: rs_raw = "已完成"
            elif raw.find("无效") >= 0: rs_raw = "已终止"
            elif raw.find("处理") >= 0: rs_raw = "处理中"
            b = _to_status_bucket(rs_raw) or "未标注"
            mark = " ⚠ 被算成已闭环!" if _is_closed(b) else ""
            print(f"       {i:>2}. [{raw:<35}] {c:>5} 条 → 推断{bucket}" + mark)
        print(f"  B2. 综合(含 handle_time/handle_detail) bucket 汇总:")
        for bt in ["待处理", "处理中", "未标注", "已完成", "已终止"]:
            cc = b_bucket_counter.get(bt, 0)
            print(f"       - {bt:<8}: {cc:>5} 条" + ("  (剔除)" if _is_closed(bt) else ""))
        print(f"  ➡  external_complaints 最终进入列表(未闭环): {b_pending_count} 条")

        # ═══════════════════════════════════════════════════════════
        # C. card_complaints (卡商群投诉)
        # ═══════════════════════════════════════════════════════════
        print("\n【C】card_complaints (卡商群投诉)")
        print("-" * 60)
        cc_where = and_(
            CardComplaint.complaint_date.isnot(None),
            CardComplaint.complaint_date >= ts_pending,
            CardComplaint.complaint_date <= te_pending,
        )
        c0 = db.execute(select(func.count()).where(cc_where)).scalar() or 0
        print(f"  C0. 近 {LOOKBACK} 天 card_complaints 总记录数:              {c0:>6}")

        c1_rows = db.execute(select(
            CardComplaint.result,
            CardComplaint.handle_result,
            CardComplaint.solution,
        ).where(cc_where)).mappings().all()
        c_result_counter: Counter = Counter()
        c_bucket_counter: Counter = Counter()
        c_pending_count = 0
        for r in c1_rows:
            rs_raw = ""
            if (r["result"] or "").find("已处理") >= 0:
                rs_raw = "已完成"
            elif (r["result"] or "").find("未处理") >= 0:
                rs_raw = "待处理"
            elif (r["result"] or "").find("处理") >= 0:
                rs_raw = "处理中"
            elif r["handle_result"]:
                rs_raw = "已完成"
            elif r["solution"]:
                rs_raw = "处理中"
            else:
                rs_raw = "未标注"
            bucket = _to_status_bucket(rs_raw)
            c_result_counter[r["result"] or "NULL_RESULT"] += 1
            c_bucket_counter[bucket] += 1
            if not _is_closed(bucket):
                c_pending_count += 1
        print(f"  C1. result 原始值分布 TOP10:")
        for i, (raw, c) in enumerate(c_result_counter.most_common(10), 1):
            print(f"       {i:>2}. [{raw:<30}] {c:>5} 条")
        print(f"  C2. bucket 汇总:")
        for bt in ["待处理", "处理中", "未标注", "已完成", "已终止"]:
            cc = c_bucket_counter.get(bt, 0)
            print(f"       - {bt:<8}: {cc:>5} 条" + ("  (剔除)" if _is_closed(bt) else ""))
        print(f"  ➡  card_complaints 最终进入列表(未闭环): {c_pending_count} 条")

        # ═══════════════════════════════════════════════════════════
        # FINAL: 三表合并
        # ═══════════════════════════════════════════════════════════
        print("\n" + "=" * 80)
        print("【FINAL】三表漏斗合计")
        print("=" * 80)
        grand_total_candidates = a1 + b0 + c0
        grand_pending = still_pending_count + b_pending_count + c_pending_count
        print(f"  投诉判定 WHERE 通过(三表合计):   {grand_total_candidates:>6} 条")
        print(f"  status 过滤后最终未闭环:         {grand_pending:>6} 条")
        print(f"  (用户截图显示的 13 条 vs 这里算出 {grand_pending} 条 → 是否匹配?)")
        print()
        if grand_pending < 50:
            print("  ⚠  如果真实业务量 >> 这个数 → 下一步根据 A/B/C 各阶段的证据:")
            print("     - 若 A1 远小于真实投诉数 → H1 成立: 扩大 service_tickets WHERE 投诉判定条件/补关键词")
            print("     - 若 A1 够大但 A3 已完成太多 → H2 成立: 检查 status 原始值, 修正 _to_status_bucket")
            print("     - 若 B0 存在微信/商户号 platform 但 B2 几乎全是已完成 → H3 成立: 放宽 external status 推断")
            print()

    finally:
        db.close()


if __name__ == "__main__":
    main()
