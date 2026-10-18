"""today 主题域子模块 refund (拆分自 today.py #226)

纯物理切片, 不改实现。原 today.py 头部 import 块原样保留,
函数体内惰性 import 随函数体原样保留。
"""
# ruff: noqa: F401, I001  # 拆分自原 today.py(#226), 保留原 import 顺序避免漂移
"""今日看板: today_kpi / 小时趋势 / 接待拆分 / 退款拆分 / 队列 / 员工效率。"""

import datetime as _dt
import logging
from decimal import Decimal
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select, text, union
from sqlalchemy.orm import Session

from app.models.ods_clean import CardComplaintClean
from app.models.ods_clean import CsRecordClean
from app.models.ods_clean import ExternalComplaintClean
from app.models.group import Group, PersonGroupMembership
from app.models.incident import Incident
from app.models.person import Person, PersonAlias
from app.models.refund_standard import StdRefundOnline  # #332: 退款统计切标准表
from app.models.refund_standard import StdRefundRecord  # #332: E 路切标准表(预留 import)
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.task import SpecialTask
from app.models.ticket import ServiceTicket
from app.models.todo import TodoItem
from app.models.refund_standard import StdWalletFlow  # #332: B 路切标准表(预留 import)
from app.services.complaint_scope import (
    ALL_BUCKETS,
    BUCKET_CARD,
    BUCKET_EXTERNAL,
    BUCKET_INTERNAL,
    # ⚠️ #53: BUCKET_OTHER 已从来源桶移除(四类完备)。保留导入仅供历史数据识别,
    #    若确认无处使用可删除。
    BUCKET_OTHER,  # noqa: F401
    CARD_SCOPE,
)
from app.services.system_accounts import SourceKind  # noqa: I001
from app.services.system_accounts import is_virtual_name as _sa_is_virtual_name

logger = logging.getLogger(__name__)

# 跨子模块引用(拆分自 app.services.analytics, 原同文件内直接调用)
from app.services.analytics._core import (
    _EXTERNAL_DONE_RESULTS_LOCAL,
    _EXTERNAL_ITEM_COMPLAINT_KW,
    _INCLUDE_COMPLAINT_FILES,
    COMPLAINT_SPECIALTY,
    COMPLAINT_SPECIALTY_WEIGHT,
    D,
    TZ,
    _complaint_source_type_case,
    _complaint_subitem_case,
    _derive_service_item_for_refund,
    _parse_date,
    _pct_delta,
    _resolve_windows,
    _route_a_online_any_status_filter,
    _route_a_online_refund_filter,
    _route_b_withdraw_filter,
    _route_e_cashflow_filter,
    _strip_qw_prefix,
    _ticket_category_case,
)
from app.services.analytics.cash import (
    _cash_routes_total,
    _route_c_shanmao_trend,
    _route_d_other_trend,
)
from app.services.analytics.complaint import _ticket_effective_finish_time_where
from app.services.analytics.reception import (
    _category_case,
    _merge_person_rows,
    _operator_to_person_cte,
)


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
        StdRefundOnline.created_at_raw.isnot(None),
        StdRefundOnline.created_at_raw >= range_start,
        StdRefundOnline.created_at_raw <= range_end,
        *_route_a_online_refund_filter(),
    )
    refunds_rows = db.execute(select(
        StdRefundOnline.refund_no, StdRefundOnline.device_sn, StdRefundOnline.operator, StdRefundOnline.auditor,
        StdRefundOnline.remark, StdRefundOnline.audit_remark, StdRefundOnline.package_name, StdRefundOnline.refund_reason,
        StdRefundOnline.refund_amount, StdRefundOnline.actual_amount, StdRefundOnline.created_at_raw, StdRefundOnline.updated_at_raw,
        StdRefundOnline.platform_order_no, StdRefundOnline.status,
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
        pairs = db.execute(select(StdRefundOnline.refund_no, ServiceTicket.ticket_no
        ).select_from(StdRefundOnline).join(ServiceTicket, and_(
            ServiceTicket.device_sn == StdRefundOnline.device_sn,
            ServiceTicket.device_sn.in_(sns),
            StdRefundOnline.device_sn.in_(sns),
            ServiceTicket.register_time.isnot(None),
            StdRefundOnline.created_at_raw.isnot(None),
            func.abs(func.extract("epoch", ServiceTicket.register_time - StdRefundOnline.created_at_raw)) < sec,
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
    # 批量查重(避免循环内逐条 SELECT 的 N+1): 一次性取出已存在的 GDREF_ 工单号集合,
    # 循环里直接 in 判断, 把 N 次 SELECT 降为 1 次(窗口内退款多时尤其明显)。
    unmatched_rows = [r for r in refunds_rows if r.refund_no not in have_ticket]
    candidate_tickets = ["GDREF_" + r.refund_no for r in unmatched_rows]
    existing_tickets: set[str] = set()
    if candidate_tickets:
        existing_tickets = set(db.scalars(
            select(ServiceTicket.ticket_no).where(ServiceTicket.ticket_no.in_(candidate_tickets))
        ).all())
    for r in unmatched_rows:
        # 幂等: 工单号 = GDREF_{refund_no}  (64 字符限制 OK, refund_no ~ 22 字符)
        ticket_no = "GDREF_" + r.refund_no
        if ticket_no in existing_tickets:
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
    between = and_(StdRefundOnline.created_at_raw.isnot(None),
                   StdRefundOnline.created_at_raw >= ts, StdRefundOnline.created_at_raw <= te,
                   *_route_a_online_refund_filter())
    _WIN_45D = 86400 * 45  # v3.1: ±30d → ±45d,修复 8/1 样本里 31.28d 这种工单登记稍早被错漏的情况

    # ── F2. 自动补建工单: 日期内 ±45d 未匹配到工单的退款, 自动建一条 service_ticket, 保证 接待人/原因 绩效不漏 ──
    _ensure_service_tickets_for_unmatched_refunds(db, ts, te, match_win_sec=_WIN_45D)

    # ── CTE 共享: 每个 refund 最佳匹配工单 ──
    # v3.2 排序升级 (P0修复):
    #   1) handler 非空优先 (避免"时差最小但handler=NULL的用户自助工单"覆盖掉"时差稍大但有真实处理人"的工单)
    #   2) scat/sitm 非空优先 (同理, 原因榜不要选上空分类的工单)
    #   3) 时间差最小 (原有逻辑, 作为次级排序)
    # 同时带出 StdRefundOnline.auditor/operator 作为最终兜底: 工单hdl仍空时直接回退到退款单审核人/操作人
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
        StdRefundOnline.refund_no.label("rid"),
        StdRefundOnline.refund_amount.label("amt"),
        StdRefundOnline.package_name.label("pkg"),
        StdRefundOnline.refund_reason.label("rrsn"),
        StdRefundOnline.auditor.label("raud"),   # 新增: 退款单审核人 (P2回退)
        StdRefundOnline.operator.label("rop"),   # 新增: 退款单操作人 (P2回退)
        # 工单字段 (左连接, 可能 NULL)
        ServiceTicket.service_category.label("scat"),
        ServiceTicket.service_item.label("sitm"),
        ServiceTicket.handler.label("hdl"),
        func.abs(func.extract("epoch",
            ServiceTicket.register_time - StdRefundOnline.created_at_raw
        )).label("diff_sec"),
    ).select_from(StdRefundOnline).outerjoin(ServiceTicket, and_(
        ServiceTicket.device_sn == StdRefundOnline.device_sn,
        StdRefundOnline.device_sn.isnot(None), StdRefundOnline.device_sn != "",
        ServiceTicket.register_time.isnot(None),
        func.abs(func.extract("epoch",
            ServiceTicket.register_time - StdRefundOnline.created_at_raw
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
        StdRefundOnline.package_name.label("pkg"),
        func.count().label("cnt"),
        func.coalesce(func.sum(StdRefundOnline.refund_amount), 0).label("amt"),
    ).where(between).group_by(StdRefundOnline.package_name
    ).order_by(func.sum(StdRefundOnline.refund_amount).desc()).limit(10)).all()
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

