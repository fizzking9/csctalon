"""today 主题域子模块 reception (拆分自 today.py #226)

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

from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.external_complaint import ExternalComplaint
from app.models.group import Group, PersonGroupMembership
from app.models.incident import Incident
from app.models.person import Person, PersonAlias
from app.models.refund import RefundClean
from app.models.refund_record_report import RefundRecordReport
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.task import SpecialTask
from app.models.ticket import ServiceTicket
from app.models.todo import TodoItem
from app.models.wallet import WalletWithdrawal
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
    # 两个视角: 接待量(会话次数) vs 设备量(去重SN数)
    bucket = _category_case()
    cat_rows = db.execute(
        select(bucket, func.count().label("cnt"))
        .where(between)
        .group_by("category_bucket")
    ).all()
    cat_device_rows = db.execute(
        select(bucket, func.count(func.distinct(func.nullif(func.trim(CsRecord.device_sn), ""))).label("dcnt"))
        .where(between)
        .group_by("category_bucket")
    ).all()
    categories = []
    categories_device = []
    order = ["投诉", "退款", "套餐问题", "咨询", "综合问题", "其他问题"]
    # 接待量
    for r in cat_rows:
        cat = r.category_bucket if r.category_bucket in order else "其他问题"
        categories.append({"name": cat, "value": int(r.cnt or 0)})
    present = {c["name"] for c in categories}
    for nm in order:
        if nm not in present:
            categories.append({"name": nm, "value": 0})
    # 设备量 (独立设备SN去重)
    for r in cat_device_rows:
        cat = r.category_bucket if r.category_bucket in order else "其他问题"
        categories_device.append({"name": cat, "value": int(r.dcnt or 0)})
    present_d = {c["name"] for c in categories_device}
    for nm in order:
        if nm not in present_d:
            categories_device.append({"name": nm, "value": 0})

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
        # ⚠️ 标签名随 complaint_scope 变更为 'complaint_bucket'; SQLAlchemy 2.0 要求
        #    text() 显式包裹(不能用裸字符串引用 label)。
        .group_by("complaint_sub", text("complaint_bucket"))
        .order_by(text("cnt DESC"))
    ).all()
    complaint_subs = [
        {"name": r[0] or "未标注",
         "source_type": r[1] or BUCKET_INTERNAL,
         "value": int(r[2] or 0)}
        for r in comp_sub_rows
    ]
    # 叠加 external_complaints 今日 (按 complaint_category 聚合,未分类则归入 "外部投诉(监督局/315等)")
    # 2026-08-14: _INCLUDE_COMPLAINT_FILES=False 时跳过, ec_between=literal(False) 使下游查询返回空
    if _INCLUDE_COMPLAINT_FILES:
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
            select(func.coalesce(CardComplaint.problem_category, BUCKET_CARD),
                   func.count().label("cnt"))
            .where(cc_between)
            .group_by(func.coalesce(CardComplaint.problem_category, BUCKET_CARD))
            .order_by(text("cnt DESC"))
        ).all()
        for r in cc_rows:
            complaint_subs.append({
                "name": str(r[0] or BUCKET_CARD),
                "source_type": BUCKET_CARD,
                "value": int(r[1] or 0),
            })
    else:
        ec_between = literal(False)
        cc_between = literal(False)
    # 为避免重复计算,直接重算 total = 工单投诉 + 315补充表
    # ★★ 2026-09-11 (#53) 卡商剥离 ★★
    #   `complaint_total` **不再含卡商** —— 用户: "卡商的投诉 不纳入总量的管理,
    #   整体独立统计分析; 无论是界面上 还是各项指标的分析 统计 接口 业务逻辑 等等
    #   全部单算"。
    #   ⚠️ 卡商不进 `complaint_total` —— 这个是**用户口径**, 不受 #65 影响。
    _cnt_st = db.execute(select(func.count()).where(tk_between, _ticket_category_case() == "投诉")).scalar() or 0
    _cnt_ec = db.execute(select(func.count()).where(ec_between)).scalar() or 0
    _cnt_cc = db.execute(select(func.count()).where(cc_between)).scalar() or 0
    complaint_total = int(_cnt_st) + int(_cnt_ec)          # ← 不含卡商
    # ★★ #65: 卡商 = 工单侧真卡商 + 补充表 − 重叠 ★★
    #   ⚠️ #53 原公式为 `card_total = _cnt_cc`(纯补充表行数)。
    #      #57 证明「工单侧无卡商分类」的检测方式有误 —— 卡商在工单侧以
    #      `service_item='工信部投诉'` 形式存在。现有 28 条/近3月, 与补充表
    #      SN 重叠 16 条 → 不去重会双计。公式与外部侧平行。
    #   ⚠️ 与 `/summary` 的 `card.total` 保持**同一公式**(避免看板/决策页不一致)。
    _cnt_card_tk = db.execute(
        select(func.count()).where(tk_between, _ticket_category_case() == "投诉",
                                   _complaint_source_type_case() == BUCKET_CARD)
    ).scalar() or 0
    _ov_card_cc = db.execute(
        select(func.count()).select_from(CardComplaint)
        .where(cc_between, CardComplaint.matched_ticket_no.isnot(None),
               CardComplaint.matched_ticket_no.in_(
                   select(ServiceTicket.ticket_no).where(
                       tk_between, _ticket_category_case() == "投诉",
                       _complaint_source_type_case() == BUCKET_CARD,
                   )
               ))
    ).scalar() or 0
    card_total = int(_cnt_card_tk) + int(_cnt_cc) - int(_ov_card_cc)   # ← 卡商独立总量(并集)
    # 投诉来源类型聚合: 内部/商户号/外部 **三类**(卡商不在此列)
    # 2026-09-04 用户确认: 商户号投诉(微信)不得并入外部桶, 单列展示。
    # 原两桶口径 _complaint_source_type_simple_case(微信归外部) 已弃用。
    # ⚠️ #65: `_complaint_source_type_case` 现已含 **#58 软纠正** → 本聚合
    #    自动把「误录为工信部、实为监所」的工单归入外部桶。
    comp_src_rows = db.execute(
        select(_complaint_source_type_case(), func.count().label("cnt"))
        .where(tk_between, _ticket_category_case() == "投诉")
        # ⚠️ 用 text() 显式包裹标签: 标签名随 complaint_scope 变更为
        #    'complaint_bucket' (原 'complaint_source_type'),
        #    SQLAlchemy 2.0 不允许裸字符串引用 label。
        .group_by(text("complaint_bucket"))
        .order_by(text("cnt DESC"))
    ).all()
    complaint_by_source = [
        {"name": r[0] or BUCKET_INTERNAL, "value": int(r[1] or 0)}
        for r in comp_src_rows
    ]
    # 315 补充表(external_complaints) → 归入「外部投诉」桶
    if _cnt_ec > 0:
        found_outer = False
        for x in complaint_by_source:
            if x["name"] == BUCKET_EXTERNAL:
                x["value"] += int(_cnt_ec); found_outer = True; break
        if not found_outer:
            complaint_by_source.append({"name": BUCKET_EXTERNAL, "value": int(_cnt_ec)})
    # ★★ (#53) 卡商**已从来源桶移除** ★★
    #   用户: "(外)卡商 的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
    #   卡商改为独立字段 `card_*` 返回(见下方 card_source / card_total),
    #   **不再出现在 complaint_by_source**。旧 #47 的"单列成第4桶"口径已废弃。
    # 补齐三类桶,缺失补0 (前端颜色固定)
    _src_present = {x["name"] for x in complaint_by_source}
    for nm in ALL_BUCKETS:
        if nm not in _src_present:
            complaint_by_source.append({"name": nm, "value": 0})
    complaint_by_source.sort(key=lambda x: -x["value"])

    # ── (#53) 卡商独立统计体系(不参与投诉总量) ──
    #   口径 = 表自身行数(按 complaint_date 窗口), **不扣工单重叠**
    #   (卡商在工单侧无独立分类, 无重叠可言 —— 实证 item/category 含"卡商"均 0 行)
    card_by_category: list[dict] = []
    if _cnt_cc > 0:
        _cc_rows = db.execute(
            select(func.coalesce(CardComplaint.problem_category, CARD_SCOPE),
                   func.count().label("cnt"))
            .where(cc_between)
            .group_by(func.coalesce(CardComplaint.problem_category, CARD_SCOPE))
            .order_by(text("cnt DESC"))
        ).all()
        card_by_category = [
            {"name": str(r[0] or CARD_SCOPE), "value": int(r[1] or 0)}
            for r in _cc_rows
        ]

    # 右2: 投诉根因柱图 (口径严格 = complaint_total: service_tickets投诉 + external_complaints + card_complaints 全部覆盖)
    #
    # 分层统计策略:
    #   A. service_tickets投诉工单:
    #        A1. 能匹配到 cs_record ±24h → 用 cs_record 的 problem_category/type 精确归类 (原有6类)
    #        A2. 未匹配 cs_record → 基于 service_item/service_category 文本关键词粗略归类, 否则"待标注"
    #   B. external_complaints (外部投诉: 315/监督局等): 基于 complaint_category/platform/keyword 归类,
    #        否则归入"外部投诉平台受理类"(不虚构根因, 仅标识来自外部平台)
    #   C. card_complaints (卡商群投诉): 基于 problem_category/problem 文本关键词归类,
    #        否则归入"卡商群待标注"
    #
    # 最终保证: SUM(complaint_roots[*].count) == complaint_total, complaint_unmatched 仅作为覆盖率提示

    # ---------- A层: service_tickets 投诉工单根因 ----------
    st_root_sql = text(
        """
        WITH comp_tickets AS (
            SELECT
                st.ticket_no,
                st.service_item,
                st.service_category,
                st.device_sn,
                st.register_time
            FROM service_tickets st
            WHERE st.register_time IS NOT NULL
              AND st.register_time >= :ts
              AND st.register_time <= :te
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
                ct.service_category,
                c.problem_category,
                c.problem_type,
                -- 是否匹配到 cs_record: 1=匹配, 0=未匹配
                CASE WHEN c.problem_category IS NOT NULL THEN 1 ELSE 0 END AS has_match
            FROM comp_tickets ct
            LEFT JOIN cs_records c
              ON ct.device_sn = c.device_sn
              AND c.operation_time IS NOT NULL
              AND c.operation_time BETWEEN (ct.register_time - INTERVAL '24 hours')
                                       AND (ct.register_time + INTERVAL '24 hours')
            ORDER BY ct.ticket_no,
                     CASE WHEN c.problem_category IS NULL THEN 1 ELSE 0 END,
                     ABS(EXTRACT(EPOCH FROM (c.operation_time - ct.register_time))) ASC
        )
        SELECT
            CASE
                -- ====== A1: 已匹配 cs_record → 用 cs_record 的精确归类 ======
                WHEN has_match = 1 AND (
                    COALESCE(problem_category, '') = '网络问题'
                    OR COALESCE(problem_type, '') ~* '(网|卡|信号|离线|WiFi|激活网卡)'
                ) THEN '网络质量类'
                WHEN has_match = 1 AND (
                    COALESCE(problem_category, '') = '套餐问题'
                    OR COALESCE(problem_type, '') ~* '(套餐|涨价|流量统计|360天|包月|加油包|返现|预存|切网)'
                ) THEN '套餐争议类'
                WHEN has_match = 1 AND COALESCE(problem_category, '') = '退款'
                THEN '退款协商失败类'
                WHEN has_match = 1 AND (
                    COALESCE(problem_category, '') = '降成问题'
                    OR COALESCE(problem_type, '') ~* '(限速|降成|钻石VIP|VIP|赠送)'
                ) THEN '降成争议类'
                WHEN has_match = 1 AND (
                    COALESCE(problem_category, '') IN ('其他问题', '激活实名')
                    OR COALESCE(problem_type, '') ~* '(注销|发票|退换货)'
                ) THEN '其它(注销/发票/退换)'

                -- ====== A2: 未匹配 cs_record 或 用cs_record仍落入待标注 → 用 service_item 文本关键词粗略归类 ======
                WHEN COALESCE(service_item, '') ~* '(网|卡|信号|离线|WiFi|无网络|网速|断网|网卡)'
                THEN '网络质量类'
                WHEN COALESCE(service_item, '') ~* '(套餐|涨价|流量统计|360天|包月|加油包|返现|预存|切网)'
                THEN '套餐争议类'
                WHEN COALESCE(service_item, '') ~* '(退款|退费|退钱|不予退款|拒绝退款)'
                THEN '退款协商失败类'
                WHEN COALESCE(service_item, '') ~* '(限速|降成|钻石VIP|VIP|赠送)'
                THEN '降成争议类'
                WHEN COALESCE(service_item, '') ~* '(注销|发票|退换货|退货|换货)'
                THEN '其它(注销/发票/退换)'

                -- ═══════════════════════════════════════════════════════════
                -- 2026-08-13 新增: A3 基于 service_item "投诉大类名" 直接归位
                --   现实数据 80%+ service_item 就是投诉子类(不带具体问题),
                --   之前全部落入 待标注 导致饼图"今日投诉 6条 待标注100%"
                -- ═══════════════════════════════════════════════════════════
                -- A3-1: 商品投诉 → 商品质量/售后类 (实物投诉)
                WHEN COALESCE(service_item, '') ~* '商品投诉'
                THEN '商品质量/售后类'
                -- A3-2: 服务投诉 → 服务态度/时效类 (非商品性投诉, 客服/流程/时效问题)
                WHEN COALESCE(service_item, '') ~* '服务投诉'
                THEN '服务态度/时效类'
                -- A3-3: 微信投诉(商户号投诉,独立展示单独桶)
                WHEN COALESCE(service_item, '') ~* '微信投诉'
                THEN '微信商户号投诉类'
                -- A3-4: 外部平台名(12315/工信部/消协/公共服务/市监/政务) → 外部平台投诉受理类
                --       注意: 这里不重复 外部投诉(ExternalComplaint)表, 这些工单是 service_tickets
                --       中通过 "投诉子类" 字段直接录入"全国12315平台"等名称的记录
                WHEN COALESCE(service_item, '') ~* '(12315|工信部|消协|消费者协会|公共服务|监督局|市场监管|市监|12345|政务平台)'
                THEN '外部平台投诉受理类'

                -- 真的啥都匹配不到 → 待标注(剩下 <5% 预留人工标注接口)
                ELSE '待标注'
            END AS root_cause,
            COUNT(*) AS cnt,
            SUM(has_match) AS matched_cnt  -- 有多少条是通过 cs_record 精确匹配的 (用于计算覆盖率)
        FROM nearest_cs
        GROUP BY 1
        ORDER BY cnt DESC;
        """
    )
    st_root_rows = db.execute(st_root_sql, {"ts": ts, "te": te}).fetchall()
    complaint_roots_map: dict[str, int] = {}
    st_matched_count = 0
    for r in st_root_rows:
        nm = str(r[0]); cnt = int(r[1] or 0); mcnt = int(r[2] or 0)
        complaint_roots_map[nm] = complaint_roots_map.get(nm, 0) + cnt
        st_matched_count += mcnt

    # ---------- B层: external_complaints 外部投诉(平台受理) ----------
    def _ec_root_cause(complaint_category: str | None, platform: str | None, keyword: str | None) -> str:
        """external_complaint 根因归类 (基于文本关键词, 不虚构根因)"""
        blob = " ".join(x or "" for x in [complaint_category or "", platform or "", keyword or ""])
        b = blob.lower()
        net_kw = ("网", "卡", "信号", "离线", "wifi", "无网络", "网速", "断网", "网卡")
        pkg_kw = ("套餐", "涨价", "流量统计", "360天", "包月", "加油包", "返现", "预存", "切网")
        ref_kw = ("退款", "退费", "退钱", "不予退款", "拒绝退款")
        down_kw = ("限速", "降成", "钻石vip", "vip", "赠送")
        oth_kw = ("注销", "发票", "退换货", "退货", "换货")
        if any(k in b for k in net_kw): return "网络质量类"
        if any(k in b for k in pkg_kw): return "套餐争议类"
        if any(k in b for k in ref_kw): return "退款协商失败类"
        if any(k in b for k in down_kw): return "降成争议类"
        if any(k in b for k in oth_kw): return "其它(注销/发票/退换)"
        # 无可用关键词 → 归入"外部平台投诉受理类"(独立桶, 保持6+1类)
        return "外部平台投诉受理类"

    ec_list_rows = db.execute(
        select(ExternalComplaint.complaint_category,
               ExternalComplaint.platform,
               ExternalComplaint.keyword)
        .where(ec_between)
    ).all()
    for r in ec_list_rows:
        rc = _ec_root_cause(r[0], r[1], r[2])
        complaint_roots_map[rc] = complaint_roots_map.get(rc, 0) + 1

    # ---------- C层: card_complaints 卡商群投诉 ----------
    def _cc_root_cause(problem_category: str | None, problem: str | None) -> str:
        """card_complaint 根因归类 (基于文本关键词)"""
        blob = " ".join(x or "" for x in [problem_category or "", problem or ""])
        b = blob.lower()
        net_kw = ("网", "卡", "信号", "离线", "wifi", "无网络", "网速", "断网", "网卡")
        pkg_kw = ("套餐", "涨价", "流量统计", "360天", "包月", "加油包", "返现", "预存", "切网")
        ref_kw = ("退款", "退费", "退钱", "不予退款", "拒绝退款")
        down_kw = ("限速", "降成", "钻石vip", "vip", "赠送")
        oth_kw = ("注销", "发票", "退换货", "退货", "换货")
        if any(k in b for k in net_kw): return "网络质量类"
        if any(k in b for k in pkg_kw): return "套餐争议类"
        if any(k in b for k in ref_kw): return "退款协商失败类"
        if any(k in b for k in down_kw): return "降成争议类"
        if any(k in b for k in oth_kw): return "其它(注销/发票/退换)"
        return "卡商群待标注类"

    cc_list_rows = db.execute(
        select(CardComplaint.problem_category,
               CardComplaint.problem)
        .where(cc_between)
    ).all()
    for r in cc_list_rows:
        rc = _cc_root_cause(r[0], r[1])
        complaint_roots_map[rc] = complaint_roots_map.get(rc, 0) + 1

    # ---------- 汇总根因 ----------
    complaint_roots = [{"name": k, "count": v} for k, v in complaint_roots_map.items()]
    # 所有根因的总数 = complaint_total (保证柱图总数与投诉总数匹配)
    sum(r["count"] for r in complaint_roots)
    # 补齐标准根因桶 (6 标准 + 2 扩展 + A3新增3桶 + 待标注) 缺失补 0
    #   新增: 商品质量/售后类 / 服务态度/时效类 / 微信商户号投诉类 — 来自 service_tickets.service_item 大类名
    root_order = [
        "网络质量类", "套餐争议类", "退款协商失败类",
        "降成争议类", "其它(注销/发票/退换)",
        "商品质量/售后类", "服务态度/时效类", "微信商户号投诉类",
        "外部平台投诉受理类", "卡商群待标注类", "待标注",
    ]
    present_root = {r["name"] for r in complaint_roots}
    for nm in root_order:
        if nm not in present_root:
            complaint_roots.append({"name": nm, "count": 0})
    complaint_roots.sort(key=lambda x: -x["count"])

    # 覆盖率统计: service_tickets 中有多少条精确匹配到了 cs_record
    # complaint_unmatched 含义调整为: 未精确匹配 cs_record 的数量 (其余用文本归类或扩展桶)
    # 这样 SUM(roots.count) == complaint_total 严格成立, unmatched 仅作质量提示
    complaint_unmatched = max(0, _cnt_st - st_matched_count)

    # ═══════════════════════════════════════════════════════════
    # 2026-08-13 新增: 投诉处理人 × 处理状态 统计 (三张投诉表合并)
    #   - 处理人来源: service_tickets.handler / ExternalComplaint.handler / CardComplaint.handler
    #   - 统一归一化: "企微-洪佳" → "洪佳"，空值 → "未分配"
    #   - 状态口径: 3表统一映射为 [待处理/处理中/已完成/已终止/未标注] 5 桶
    # ═══════════════════════════════════════════════════════════
    _handler_sql = text("""
        WITH st_h AS (
            SELECT
                COALESCE(NULLIF(TRIM(st.handler), ''), '未分配') AS raw_handler,
                st.status AS raw_status,
                '内部投诉'::text AS source_type,
                COUNT(*)::int AS cnt
            FROM service_tickets st
            WHERE st.register_time IS NOT NULL
              AND st.register_time >= :ts AND st.register_time <= :te
              AND (
                st.service_category IS NULL
                OR st.service_category ILIKE '%投诉%'
                OR st.service_item ILIKE ANY (ARRAY[
                    '%12315%', '%工信部%', '%消协%', '%公共服务平台%', '%消费者协会%',
                    '%监督局%', '%市场监管%', '%市监%', '%315平台%', '%12345%', '%政务平台%',
                    '%商品投诉%', '%服务投诉%', '%微信投诉%'
                ])
              )
            GROUP BY 1, 2, 3
        ),
        ec_h AS (
            SELECT
                -- external_complaints 处理人: follower(跟进人)
                COALESCE(NULLIF(TRIM(ec.follower), ''), '未分配') AS raw_handler,
                -- 没有 status 列, 直接基于 result + handle_time 判断状态
                CASE
                    WHEN COALESCE(ec.result, '') ILIKE '%有效%' THEN '已完成'
                    WHEN COALESCE(ec.result, '') ILIKE '%无效%' THEN '已终止'
                    WHEN COALESCE(ec.result, '') ILIKE '%处理%' THEN '处理中'
                    WHEN ec.handle_time IS NOT NULL THEN '已完成'
                    WHEN COALESCE(ec.handle_detail, '') <> '' THEN '处理中'
                    ELSE '未标注'
                END AS raw_status,
                '外部投诉'::text AS source_type,
                COUNT(*)::int AS cnt
            FROM external_complaints ec
            WHERE ec.complaint_date IS NOT NULL
              AND ec.complaint_date >= :ts AND ec.complaint_date <= :te
            GROUP BY 1, 2, 3
        ),
        cc_h AS (
            SELECT
                COALESCE(NULLIF(TRIM(cc.handler), ''), '未分配') AS raw_handler,
                -- card_complaints 没有 status 列, 直接基于 result + handle_result 判断
                CASE
                    WHEN COALESCE(cc.result, '') ILIKE '%已处理%' THEN '已完成'
                    WHEN COALESCE(cc.result, '') ILIKE '%未处理%' THEN '待处理'
                    WHEN COALESCE(cc.result, '') ILIKE '%处理%' THEN '处理中'
                    WHEN COALESCE(cc.handle_result, '') <> '' THEN '已完成'
                    WHEN COALESCE(cc.solution, '') <> '' THEN '处理中'
                    ELSE '未标注'
                END AS raw_status,
                '卡商群投诉'::text AS source_type,
                COUNT(*)::int AS cnt
            FROM card_complaints cc
            WHERE cc.complaint_date IS NOT NULL
              AND cc.complaint_date >= :ts AND cc.complaint_date <= :te
            GROUP BY 1, 2, 3
        ),
        all_h AS (
            SELECT raw_handler, raw_status, source_type, cnt FROM st_h
            UNION ALL
            SELECT raw_handler, raw_status, source_type, cnt FROM ec_h
            UNION ALL
            SELECT raw_handler, raw_status, source_type, cnt FROM cc_h
        )
        SELECT raw_handler, raw_status, source_type, SUM(cnt)::int AS cnt
        FROM all_h
        GROUP BY 1, 2, 3
        ORDER BY SUM(cnt) DESC;
    """)
    handler_rows = db.execute(_handler_sql, {"ts": ts, "te": te}).mappings().all()

    # 聚合: {handler: {total: N, by_status: {st: N}, by_source: {src: N}}}
    handlers_acc: dict[str, dict] = {}
    for hr in handler_rows:
        raw = str(hr["raw_handler"] or "")
        # 去除 "企微-" 前缀 (企微-洪佳 → 洪佳), 再首尾 strip
        cleaned = raw.removeprefix("企微-").removeprefix("企微").strip() if raw else ""
        handler = cleaned or raw or "未分配"
        if not handler:
            handler = "未分配"
        raw_st = str(hr["raw_status"] or "未标注")
        # 状态口径统一映射: 待处理/处理中/已完成/已终止/未标注
        rs = raw_st
        if any(k in rs for k in ["待处理", "待接单", "待派", "未处理", "new", "open"]):
            status_bucket = "待处理"
        elif any(k in rs for k in ["处理中", "进行中", "处理", "running", "dispatch", "已派"]):
            status_bucket = "处理中"
        elif any(k in rs for k in ["已完成", "完成", "办结", "关闭", "closed", "done", "finish", "有效", "已处理"]):
            status_bucket = "已完成"
        elif any(k in rs for k in ["终止", "取消", "取消处理", "无效", "拒绝", "reject"]):
            status_bucket = "已终止"
        else:
            status_bucket = "未标注"
        src_bucket = str(hr["source_type"] or "未标注")
        cnt = int(hr["cnt"] or 0)
        acc = handlers_acc.setdefault(handler, {
            "total": 0,
            "by_status": {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0},
            # ⚠️ 2026-09-10 (#47): by_source 的键为**统计分类桶**常量, 不再用字面"卡商群投诉"
            #    ("卡商群投诉"是腾讯文档 Sheet 名/数据源名, 与统计分类是两回事)
            "by_source": {BUCKET_INTERNAL: 0, BUCKET_EXTERNAL: 0, BUCKET_CARD: 0},
        })
        acc["total"] += cnt
        acc["by_status"][status_bucket] = acc["by_status"].get(status_bucket, 0) + cnt
        if src_bucket in (BUCKET_INTERNAL, BUCKET_EXTERNAL, BUCKET_CARD):
            acc["by_source"][src_bucket] = acc["by_source"].get(src_bucket, 0) + cnt
        elif src_bucket == "卡商群投诉":  # 兼容旧值(未归一的上游数据)
            acc["by_source"][BUCKET_CARD] = acc["by_source"].get(BUCKET_CARD, 0) + cnt
        else:
            acc["by_source"]["未标注"] = acc["by_source"].get("未标注", 0) + cnt

    # 取 Top 15 处理人 (按 total 倒序), "未分配" 固定排首位 if 存在
    complaint_handlers = []
    if handlers_acc.get("未分配"):
        unallocated = handlers_acc.pop("未分配")
        complaint_handlers.append({"handler": "未分配", **unallocated})
    sorted_names = sorted(handlers_acc.keys(), key=lambda n: -handlers_acc[n]["total"])[:15]
    for nm in sorted_names:
        complaint_handlers.append({"handler": nm, **handlers_acc[nm]})

    # 同时汇总: 3 表合并后的投诉处理状态分布(与饼图对齐)
    status_agg: dict[str, int] = {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0}
    for ch in complaint_handlers:
        for st, c in ch["by_status"].items():
            status_agg[st] = status_agg.get(st, 0) + int(c or 0)
    complaint_status_all = [{"name": k, "value": v} for k, v in status_agg.items() if v]

    # ═══════════════════════════════════════════════════════════
    # 2026-08-14: 拆分内部/外部投诉处理人排行
    #   - 内部投诉处理人: source_type == '内部投诉' (仅 service_tickets)
    #   - 外部投诉处理人: source_type == '外部投诉' + '卡商群投诉'
    #   - 因数量级差异较大，分开展示更清晰
    #
    # ⚠️⚠️ 2026-09-10 (#47) 变更: 卡商**从外部拆出成独立表** ⚠️⚠️
    #   用户要求"(外)卡商 这个分类; 在各个统计口 都单列出来"。
    #   旧口径(2026-08-14): external = '外部投诉' + '卡商群投诉'(合并)。
    #   新口径: 三张表并列 —— 内部投诉 / 外部投诉 / (外)卡商。
    #   ⚠️ 本变更与原 LOCKED A-20260902-016 §问题3 冲突
    #      (其明文规定 `complaint_external_handlers = 外部投诉 + 卡商群投诉`),
    #      需走人工审核修订该 LOCKED 文档(见 docs/arch/_pending/A-20260910-005)。
    #      `complaint_external_handlers` 字段**保留**(向后兼容), 但语义已**不含卡商**;
    #      新增 `complaint_card_handlers` 承载卡商。
    # ═══════════════════════════════════════════════════════════
    internal_handlers_acc: dict[str, dict] = {}
    external_handlers_acc: dict[str, dict] = {}
    card_handlers_acc: dict[str, dict] = {}
    for hr in handler_rows:
        raw = str(hr["raw_handler"] or "")
        cleaned = raw.removeprefix("企微-").removeprefix("企微").strip() if raw else ""
        handler = cleaned or raw or "未分配"
        if not handler:
            handler = "未分配"
        raw_st = str(hr["raw_status"] or "未标注")
        rs = raw_st
        if any(k in rs for k in ["待处理", "待接单", "待派", "未处理", "new", "open"]):
            status_bucket = "待处理"
        elif any(k in rs for k in ["处理中", "进行中", "处理", "running", "dispatch", "已派"]):
            status_bucket = "处理中"
        elif any(k in rs for k in ["已完成", "完成", "办结", "关闭", "closed", "done", "finish", "有效", "已处理"]):
            status_bucket = "已完成"
        elif any(k in rs for k in ["终止", "取消", "取消处理", "无效", "拒绝", "reject"]):
            status_bucket = "已终止"
        else:
            status_bucket = "未标注"
        src_bucket = str(hr["source_type"] or "未标注")
        cnt = int(hr["cnt"] or 0)
        if src_bucket == BUCKET_INTERNAL:
            acc = internal_handlers_acc.setdefault(handler, {
                "total": 0,
                "by_status": {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0},
            })
        elif src_bucket == BUCKET_CARD or src_bucket == "卡商群投诉":
            # 卡商独立表(#47): 兼容旧值 '卡商群投诉' 与新增 BUCKET_CARD('(外)卡商')
            acc = card_handlers_acc.setdefault(handler, {
                "total": 0,
                "by_status": {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0},
            })
        else:
            acc = external_handlers_acc.setdefault(handler, {
                "total": 0,
                "by_status": {"待处理": 0, "处理中": 0, "已完成": 0, "已终止": 0, "未标注": 0},
            })
        acc["total"] += cnt
        acc["by_status"][status_bucket] = acc["by_status"].get(status_bucket, 0) + cnt

    def _build_handler_list(acc_dict, top_n=15):
        result = []
        if acc_dict.get("未分配"):
            unallocated = acc_dict.pop("未分配")
            result.append({"handler": "未分配", **unallocated})
        sorted_names = sorted(acc_dict.keys(), key=lambda n: -acc_dict[n]["total"])[:top_n]
        for nm in sorted_names:
            result.append({"handler": nm, **acc_dict[nm]})
        return result

    complaint_internal_handlers = _build_handler_list(internal_handlers_acc)
    # ⚠️ 语义变更(#47): 不再含卡商。卡商见下方 complaint_card_handlers。
    complaint_external_handlers = _build_handler_list(external_handlers_acc)
    # 卡商处理人排行（新增, #47 卡商单列）
    complaint_card_handlers = _build_handler_list(card_handlers_acc)

    # ═══════════════════════════════════════════════════════════
    # 2026-08-13 新增: 近期未处理投诉列表
    #   - 覆盖口径: 三张投诉表 (service_tickets 投诉类 + external_complaints + card_complaints)
    #   - 待处理定义: status_bucket ∉ {已完成, 已终止}  (即 待处理/处理中/未标注 任一都算"未闭环")
    #   - 有效信息列: 来源/编号/投诉时间/投诉人/SN/投诉标题或内容/状态/处理人
    #   - 跨度限制: 仅当 窗口≤31 天才返回列表(用户反馈"跨度太长意义不大"),
    #       超过 31 天 → 返回 [] + skipped=true + 提示文本,避免前端被成千上万条淹没
    # ═══════════════════════════════════════════════════════════
    span_days = (te - ts).days + 1
    PENDING_LIMIT_DAYS = 31
    PENDING_LIMIT_ROWS = 200

    def _to_status_bucket(raw_st: str | None) -> str:
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

    def _clean_handler(raw: str | None) -> str:
        if not raw:
            return "未分配"
        return (raw.removeprefix("企微-").removeprefix("企微").strip()) or raw or "未分配"

    complaint_pending: list[dict] = []
    pending_skipped = False
    pending_skip_reason = ""
    # ═══════════════════════════════════════════════════════════
    # 倒查窗口(创建时间范围): 不管视图选的是哪一天/哪一周,
    # 投诉待处理列表统一取 "视图终点 向前 7 天内 创建的 + 到现在仍未闭环"
    #   例如: 视图=08-13单日  →  创建窗口=[08-06, 08-13] (倒查7天,含今天)
    #        视图=08-01~08-07周 → 创建窗口=[min(08-01, 07-31), 08-07] = [07-31, 08-07] (也附带7天回查)
    #   这样即使选 "今日" 视图, 也能看到 前 6 天内积压的未闭环投诉 (否则只看当天创建的会漏掉老积压)
    # ═══════════════════════════════════════════════════════════
    PENDING_LOOKBACK_DAYS = 7
    ts_pending = min(ts, te - timedelta(days=PENDING_LOOKBACK_DAYS - 1))  # 创建窗口起点(含今天,所以-1)
    te_pending = te  # 创建窗口终点 = 视图终点(实时窗口 = now)

    if span_days > PENDING_LIMIT_DAYS:
        pending_skipped = True
        pending_skip_reason = (
            f"当前查询跨度 {span_days} 天, 超过 {PENDING_LIMIT_DAYS} 天阈值;"
            " 未处理列表仅在日/周视图下提供, 请缩短时间范围后查看。"
        )
    else:
        # ── A. service_tickets 投诉类工单 (未闭环) ──
        # 注意: 创建时间用 ts_pending / te_pending(倒查7天窗口), 而不是视图原始窗口
        st_kws = _EXTERNAL_ITEM_COMPLAINT_KW + ("商品投诉", "服务投诉", "微信投诉")
        st_where = and_(
            ServiceTicket.register_time.isnot(None),
            ServiceTicket.register_time >= ts_pending,
            ServiceTicket.register_time <= te_pending,
            or_(
                ServiceTicket.service_category.is_(None),
                ServiceTicket.service_category.ilike("%投诉%"),
                or_(*[ServiceTicket.service_item.ilike(f"%{k}%") for k in st_kws]),
            ),
        )
        # ═══════════════════════════════════════════════════════════
        # ⚠ 2026-08-13 根因修复:
        # 旧逻辑: SQL 层 ORDER BY ASC LIMIT 201 → Python 层 bucket 过滤 → 只取到了最早 201 行,
        #         而最早 201 条投诉 (8/6-8/7 创建的历史老单) 95%+ 都是已完成, 导致:
        #         ① 真实未闭环 208 条 只展示了 9-13 条 (1/15 量级放大漏单)
        #         ② 109 条 微信商户号投诉 中有大量排在 ASC 序号 #202+ 的被 LIMIT 吞掉
        # 新策略:
        #   1. SQL 层只做【倒查7天创建 + 投诉判定】两道前置过滤, 不做 LIMIT
        #      (近 7 天投诉类工单仅 ~1276 条, 内存压力完全可接受, 远小于全量工单 7595 条)
        #   2. Python 层先按 status bucket 过滤掉 已完成/已终止 → 得到真正未闭环的全集
        #   3. 合并三表后按 occur_time ASC(最早积压排第一), 再在 Python 层统一 LIMIT 200
        #      (超过 200 条时才截断, 并附 tooltip 提示运营优先处理最早那一批)
        # ═══════════════════════════════════════════════════════════
        st_pending_rows = db.execute(
            select(
                ServiceTicket.ticket_no,
                ServiceTicket.register_time,
                ServiceTicket.device_sn,
                ServiceTicket.phone,
                ServiceTicket.service_item,
                ServiceTicket.service_category,
                ServiceTicket.status,
                ServiceTicket.handler,
                ServiceTicket.remark,
            )
            .where(st_where)
            .order_by(ServiceTicket.register_time.asc())
            #  ⚠️ 移除 LIMIT: 让所有近 7 天内创建、命中投诉判定的行都进入 Python 层 status 过滤
        ).mappings().all()
        for r in st_pending_rows:
            bucket = _to_status_bucket(r["status"])
            if bucket in ("已完成", "已终止"):
                continue
            # 投诉标题/摘要: 优先 service_item, 否则 service_category, 兜底 remark 前 32 字
            title_parts = []
            if r["service_item"]:
                title_parts.append(r["service_item"])
            elif r["service_category"]:
                title_parts.append(r["service_category"])
            if r["remark"]:
                _rmk = str(r["remark"])[:32]
                if _rmk and (not title_parts or _rmk not in title_parts[-1]):
                    title_parts.append(_rmk)
            title = " · ".join(title_parts) or "内部投诉工单(未登记详情)"
            complaint_pending.append({
                "source_type": "内部投诉",
                "no": r["ticket_no"] or "",
                "occur_time": r["register_time"].isoformat() if r["register_time"] else "",
                "complainant": "",
                "complainant_phone": r["phone"] or "",
                "device_sn": r["device_sn"] or "",
                "title": title,
                "status_bucket": bucket,
                "status_raw": r["status"] or "",
                "handler": _clean_handler(r["handler"]),
            })

        # ── B. external_complaints 外部投诉 (未闭环) ──
        # 注意: complaint_pending 的三表查询直接使用表模型，不受 _INCLUDE_COMPLAINT_FILES 标志影响
        # 该标志仅控制投诉子类饼图/根因等聚合查询，并不阻止未闭环列表查询
        ec_where = and_(
            ExternalComplaint.complaint_date.isnot(None),
            ExternalComplaint.complaint_date >= ts_pending,
            ExternalComplaint.complaint_date <= te_pending,
        )
        ec_pending_rows = db.execute(
            select(
                ExternalComplaint.complaint_no,
                ExternalComplaint.complaint_date,
                ExternalComplaint.complainant,
                ExternalComplaint.complainant_phone,
                ExternalComplaint.device_sn,
                ExternalComplaint.platform,
                ExternalComplaint.problem,
                ExternalComplaint.complaint_category,
                ExternalComplaint.result,
                ExternalComplaint.handle_time,
                ExternalComplaint.handle_detail,
                ExternalComplaint.follower,
            )
            .where(ec_where)
            .order_by(ExternalComplaint.complaint_date.asc())
            # ⚠️ 移除 LIMIT (和 service_tickets 同理, 统一放到 Python 层 过滤完 bucket 后再 LIMIT)
        ).mappings().all()
        for r in ec_pending_rows:
            # 状态推断: 复用 handler_stats 口径 (result/handle_time/handle_detail)
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
            if bucket in ("已完成", "已终止"):
                continue
            # 投诉摘要: 平台 + 分类/问题
            title_parts = []
            if r["platform"]:
                title_parts.append(f"[{r['platform']}]")
            if r["complaint_category"]:
                title_parts.append(r["complaint_category"])
            if r["problem"]:
                _pb = str(r["problem"])[:64]
                if _pb:
                    title_parts.append(_pb)
            title = " ".join(title_parts) or "外部投诉(未登记详情)"
            handler_raw = r["follower"]
            complaint_pending.append({
                "source_type": "外部投诉",
                "no": r["complaint_no"] or "",
                "occur_time": r["complaint_date"].isoformat() if r["complaint_date"] else "",
                "complainant": r["complainant"] or "",
                "complainant_phone": r["complainant_phone"] or "",
                "device_sn": r["device_sn"] or "",
                "title": title,
                "status_bucket": bucket,
                "status_raw": r["result"] or rs_raw,
                "handler": _clean_handler(handler_raw),
            })

        # ── C. card_complaints 卡商群投诉 (未闭环) ──
        cc_where = and_(
            CardComplaint.complaint_date.isnot(None),
            CardComplaint.complaint_date >= ts_pending,
            CardComplaint.complaint_date <= te_pending,
        )
        cc_pending_rows = db.execute(
            select(
                CardComplaint.id,
                CardComplaint.complaint_date,
                CardComplaint.complainant,
                CardComplaint.complainant_phone,
                CardComplaint.device_sn,
                CardComplaint.complaint_group,
                CardComplaint.problem,
                CardComplaint.problem_category,
                CardComplaint.result,
                CardComplaint.handle_result,
                CardComplaint.solution,
                CardComplaint.handler,
            )
            .where(cc_where)
            .order_by(CardComplaint.complaint_date.asc())
            # ⚠️ 移除 LIMIT (和 service_tickets 同理, 统一放到 Python 层 过滤完 bucket 后再 LIMIT)
        ).mappings().all()
        for r in cc_pending_rows:
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
            if bucket in ("已完成", "已终止"):
                continue
            title_parts = []
            if r["complaint_group"]:
                title_parts.append(f"[{r['complaint_group']}]")
            if r["problem_category"]:
                title_parts.append(r["problem_category"])
            if r["problem"]:
                _pb = str(r["problem"])[:64]
                if _pb:
                    title_parts.append(_pb)
            title = " ".join(title_parts) or "(外)卡商投诉(未登记详情)"
            complaint_pending.append({
                "source_type": BUCKET_CARD,   # #47: 独立桶, 不再并入外部投诉
                "no": f"CC-{r['id']}" if r["id"] is not None else "",
                "occur_time": r["complaint_date"].isoformat() if r["complaint_date"] else "",
                "complainant": r["complainant"] or "",
                "complainant_phone": r["complainant_phone"] or "",
                "device_sn": r["device_sn"] or "",
                "title": title,
                "status_bucket": bucket,
                "status_raw": r["result"] or rs_raw,
                "handler": _clean_handler(r["handler"]),
            })

        # 合并三张表结果后按 投诉时间 ASC(最早积压排第一, 倒逼优先处理最久未动的工单)
        complaint_pending.sort(
            key=lambda x: (x["occur_time"] or "9999-12-31"),
        )
        # 截断超过限制,并给出提示
        if len(complaint_pending) > PENDING_LIMIT_ROWS:
            complaint_pending = complaint_pending[:PENDING_LIMIT_ROWS]
            pending_skipped = True
            pending_skip_reason = (
                f"未闭环投诉超过 {PENDING_LIMIT_ROWS} 条, 仅展示最早需处理的前 {PENDING_LIMIT_ROWS} 条;"
                " 请优先处理最早的投诉或缩小查询范围。"
            )

    # 注意: pending_total 口径 = "倒查 7 天窗口内仍未闭环" 的实际条数(和列表一致)
    # 而 complaint_status_all 里的待处理/处理中统计仍然是「原视图窗口」(和饼图/处理人排行口径一致, 不改动)
    complaint_pending_total = len(complaint_pending)

    # 数据一致性校验: 确保 total 与 list 长度一致，防止前端展示不一致
    import logging
    _log = logging.getLogger(__name__)
    if complaint_pending_total > 0 and len(complaint_pending) == 0:
        _log.warning(f"complaint_pending 数据不一致: total={complaint_pending_total}, list 为空")
        complaint_pending_total = 0
    elif complaint_pending_total != len(complaint_pending):
        _log.warning(f"complaint_pending 数据不一致: total={complaint_pending_total}, list_len={len(complaint_pending)}")

    return {
        "date": d_iso,
        "date_end": d_end_iso,
        # 原接待分类
        "categories": sorted(categories, key=lambda x: -x["value"]),
        "categories_device": sorted(categories_device, key=lambda x: -x["value"]),
        "top_staff": top_staff,
        # 新增: 今日投诉
        # ★★ 2026-09-11 (#53): complaint_total **不含卡商** ★★
        #    卡商已剥离为独立体系, 见下方 card_* 字段。
        "complaint_total": complaint_total,
        "complaint_subs": complaint_subs,     # 投诉子类 (饼图: 商品投诉/服务投诉/微信投诉/12315/工信部/公共服务平台, 每项带 source_type)
        # 投诉来源 内部/商户号/外部 **三类**(2026-09-11 #53: 卡商已移出, 见 card_*)
        #   2026-09-04 问题3: 商户号不并入外部
        "complaint_by_source": complaint_by_source,
        # ── (#53) 卡商独立统计体系（不参与投诉总量）──
        #   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
        #   口径(#65): 工单侧真卡商 + `card_complaints` − 重叠(与 /summary 同公式)。
        "card_total": card_total,                    # 卡商独立总量(非 complaint_total 的一部分)
        "card_scope": CARD_SCOPE,                    # "(外)卡商" —— 前端标注用
        "card_by_category": card_by_category,        # 卡商问题分类分布
        "complaint_roots": complaint_roots,   # 投诉根因 (柱图: 6+3+扩展桶)
        "complaint_unmatched": complaint_unmatched,  # 未匹配到接待记录的投诉数
        # ↓↓↓ 2026-08-13 新增: 投诉处理人 + 处理状态全景 ↓↓↓
        "complaint_handlers": complaint_handlers,  # Top 15 处理人(合并): {handler, total, by_status, by_source}
        "complaint_internal_handlers": complaint_internal_handlers,  # 2026-08-14 新增: 内部投诉处理人 Top 15
        # ⚠️ 2026-09-10 (#47) 语义变更: **不再含卡商**(卡商见 complaint_card_handlers)。
        #    原 LOCKED A-20260902-016 §问题3 规定 = 外部投诉 + 卡商群投诉(合并),
        #    现按用户要求拆开单列, 字段名保留以向后兼容。
        "complaint_external_handlers": complaint_external_handlers,  # 外部投诉处理人 Top 15
        "complaint_card_handlers": complaint_card_handlers,  # (#47 新增) (外)卡商处理人 Top 15
        "complaint_status_all": complaint_status_all,  # 三表合并处理状态 5 桶(对齐处理人status)
        # ↓↓↓ 2026-08-13 新增: 近期未处理投诉列表 ↓↓↓
        "complaint_pending_total": complaint_pending_total,   # 未闭环投诉总数(待+处理中+未标注), 与 complaint_status_all 口径一致
        "complaint_pending_skipped": pending_skipped,         # 是否因跨度超限而被跳过(日/周视图=false, 长跨度=true)
        "complaint_pending_skip_reason": pending_skip_reason, # 跳过/截断说明(给前端 tooltip)
        "complaint_pending_list": complaint_pending,          # 待处理明细列表(最多200条)
    }

