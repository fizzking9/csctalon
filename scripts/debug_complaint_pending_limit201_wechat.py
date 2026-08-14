#!/usr/bin/env python3
"""调试 Part 2: 验证 H8 (LIMIT 201 ASC 导致取到的都是老已完单)  & 查找 商户号/微信投诉 实际落在哪张表/service_item

运行: cd backend && ./.venv/bin/python ../scripts/debug_complaint_pending_limit201_wechat.py
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo
from sqlalchemy import and_, or_, func, select, text
from app.core.database import SessionLocal
from app.models.ticket import ServiceTicket
from app.models.external_complaint import ExternalComplaint
from app.models.card_complaint import CardComplaint

TZ = ZoneInfo("Asia/Shanghai")
LOOKBACK = 7
_EXTERNAL_ITEM_COMPLAINT_KW = ("12315","工信部","消协","公共服务平台","消费者协会","监督局","市场监管","市监","315平台","12345","政务平台")
ST_KWS = _EXTERNAL_ITEM_COMPLAINT_KW + ("商品投诉","服务投诉","微信投诉")

def _to_status_bucket(raw_st):
    rs = raw_st or ""
    if any(k in rs for k in ["待处理","待接单","待派","未处理","new","open"]): return "待处理"
    if any(k in rs for k in ["处理中","进行中","处理","running","dispatch","已派"]): return "处理中"
    if any(k in rs for k in ["已完成","完成","办结","关闭","closed","done","finish","有效","已处理"]): return "已完成"
    if any(k in rs for k in ["终止","取消","取消处理","无效","拒绝","reject"]): return "已终止"
    return "未标注"
def _is_closed(b): return b in ("已完成","已终止")

now_sh = dt.datetime.now(TZ)
today_d = now_sh.date()
start_sh = dt.datetime.combine(today_d - dt.timedelta(days=LOOKBACK-1), dt.time(0,0,0), tzinfo=TZ)
end_sh = now_sh
_utc = dt.timezone.utc
ts_pending = start_sh.astimezone(_utc).replace(tzinfo=None)
te_pending = end_sh.astimezone(_utc).replace(tzinfo=None)

db = SessionLocal()
try:
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
    print(f"【H8 验证】取 A1 全部行,模拟 ASC LIMIT 201 vs 不限量 vs DESC LIMIT 201 后 status 过滤结果\n")
    # 先取全量
    rows_all = db.execute(select(
        ServiceTicket.ticket_no,
        ServiceTicket.register_time,
        ServiceTicket.service_item,
        ServiceTicket.service_category,
        ServiceTicket.status,
    ).where(st_where)).mappings().all()
    rows_all_sorted_asc = sorted(rows_all, key=lambda x: x["register_time"])
    rows_all_sorted_desc = sorted(rows_all, key=lambda x: x["register_time"], reverse=True)
    print(f"  取 A1 全量 (不限):             {len(rows_all):>5} 条")

    def _count_pending(rs):
        p = 0
        for r in rs:
            if not _is_closed(_to_status_bucket(r["status"])):
                p += 1
        return p

    print(f"  全量 未闭环(正确):              {_count_pending(rows_all):>5} 条")
    # ASC first 201 (这就是生产代码现在的 LIMIT 201)
    asc201 = rows_all_sorted_asc[:201]
    asc201_pending = _count_pending(asc201)
    asc201_statuses = Counter(r["status"] or "NULL" for r in asc201)
    print(f"\n  【生产代码现状】取 ASC LIMIT 201:  {len(asc201):>5} 条")
    print(f"    ASC 201 未闭环(用户实际看到):    {asc201_pending:>5} 条  ← 对一对截图 13 条!")
    print(f"    ASC 201 status 分布 Top8:        {asc201_statuses.most_common(8)}")

    # 结论: 如果 ASC 201 未闭环数 ≈ 用户看到的 13 条, 则 H8 坐实
    print()

    # DESC first 201 (验证新工单中有大量待处理)
    desc201 = rows_all_sorted_desc[:201]
    print(f"  对照组: DESC LIMIT 201:          {len(desc201):>5} 条, 未闭环: {_count_pending(desc201):>5} 条")
    # 全量中未闭环的注册时间分布（前10最早, 后10最新）
    pending_all = [r for r in rows_all if not _is_closed(_to_status_bucket(r["status"]))]
    pending_all_sorted_asc = sorted(pending_all, key=lambda x: x["register_time"])
    print(f"\n  全量 {len(pending_all)} 条未闭环注册时间范围: {pending_all_sorted_asc[0]['register_time']} ~ {pending_all_sorted_asc[-1]['register_time'] if pending_all_sorted_asc else 'NONE'}")
    print()

    # 再查: service_item 中「微信投诉/商户号」有多少条, 它们落在注册时间排序的第几名?
    # → 如果微信投诉都是近期的, 它们在 ASC 排序中排第 N 名, 若 N>201 就被 LIMIT 吃掉了
    print(f"【微信/商户号投诉定位】查找 A1 中含 微信/商户 关键词的 service_item 行")
    wechat_rows = []
    item_counter: Counter = Counter()
    for r in rows_all_sorted_asc:
        it = r["service_item"] or ""
        ca = r["service_category"] or ""
        item_counter[f"{ca}/{it}"] += 1
        if "微信" in it or "微信" in ca or "商户" in it or "商户" in ca:
            wechat_rows.append(r)
    print(f"  含'微信'或'商户'字样 service_item 的 A1 行: {len(wechat_rows)} 条")
    if wechat_rows:
        # 打印其中 status 不是已完成的 + 在 ASC 排序中序号
        pending_wechat = [r for r in wechat_rows if not _is_closed(_to_status_bucket(r["status"]))]
        print(f"    其中 未闭环: {len(pending_wechat)} 条, 已闭环: {len(wechat_rows)-len(pending_wechat)} 条")
        if pending_wechat:
            # 取前 10 条未闭环, 看它们在 ASC 全量排序中的位置(=行号+1), 若>201 就是被 LIMIT 吞了
            print(f"\n    前 10 条未闭环 微信/商户 投诉, 在 ASC 全量排序(老→新)中的位置:")
            idx_map = {r["ticket_no"]: i+1 for i, r in enumerate(rows_all_sorted_asc)}
            for r in pending_wechat[:10]:
                pos = idx_map.get(r["ticket_no"], "?")
                bucket = _to_status_bucket(r["status"])
                print(f"      #{pos:>4}  注册:{r['register_time']}  状态:{r['status']:<8}→{bucket}  cat/item:{r['service_category']}/{r['service_item']}")
            print(f"    (⚠ 如果 # 号 > 201 → 直接证明: ASC LIMIT 201 把这一批 微信商户号投诉 全给吞掉了!)")
        print(f"\n  全部 {len(wechat_rows)} 条 微信/商户 行: status 分布 {Counter(r['status'] or 'NULL' for r in wechat_rows).most_common()}")
    print(f"\n  A1 service_category/service_item 组合 Top 20 (投诉类工单最多的是哪些?):")
    for i, (ci, c) in enumerate(item_counter.most_common(20), 1):
        print(f"    {i:>2}. {ci:<40}  {c:>5} 条")

    # 额外: external_complaints 中全部记录(不管近7天, 看看 商户号/微信 投诉是否跑到了历史数据里)
    print(f"\n【外部投诉全表健康检查】external_complaints 总共有多少行 & platform 分布 & 最后一条 complaint_date")
    tot_ec = db.execute(select(func.count()).select_from(ExternalComplaint)).scalar() or 0
    print(f"  external_complaints 表总行数: {tot_ec} 行")
    if tot_ec > 0:
        last_date = db.execute(select(func.max(ExternalComplaint.complaint_date))).scalar()
        print(f"  最近一条 complaint_date = {last_date}")
        pf_dist = db.execute(select(func.coalesce(ExternalComplaint.platform,'NULL'), func.count())
            .group_by(func.coalesce(ExternalComplaint.platform,'NULL'))
            .order_by(func.count().desc())).all()
        print(f"  platform 分布: {pf_dist}")

finally:
    db.close()
