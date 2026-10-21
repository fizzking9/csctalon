"""被投诉主体维度分析: complaint_subject_analysis。"""

from datetime import timezone

from fastapi import Query
from sqlalchemy import column, func, select, table

from app.api.deps import CurrentUser, DBSession

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._helpers import _norm_subject_entity, _parse_dt, _parse_dt_end
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.models.ticket import ServiceTicket

# #338: 投诉主体分析读 std_all 镜像
_EC = table("std_external_complaint_all",
            column("company_entity"), column("complaint_shop"),
            column("device_sn"), column("complaint_date"))

# #338: 投诉主体退款关联(全状态口径) → 读 std_refund_online_all 镜像
_REFUND_ALL = table(
    "std_refund_online_all",
    column("device_sn"), column("refund_method"), column("refund_amount"), column("created_at_raw"),
)
from app.schemas.common import R
from app.services.analytics import (
    TZ,
    _complaint_source_type_case,
    _ticket_category_case,
)


@router.get("/subject-analysis", response_model=R, summary="被投诉主体维度分析(主体统计/严重性分布/升级链/退款关联)")
@cached_json(lambda *a, **kw: f"complaint:subject:{kw.get('start')}:{kw.get('end')}:{kw.get('source_type')}:{kw.get('sort_by')}:{kw.get('top_n')}:{kw.get('compare')}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 120))
def complaint_subject_analysis(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str = Query(
        default="all",
        description="来源筛选: all=全部 / merchant=仅商户号 / external=仅外部 / both=升级主体(两源都有)",
    ),
    sort_by: str = Query(
        default="total",
        description="排序: total=总量 / external_ratio=外部占比 / refund_amount=涉诉金额 / delta=环比增幅",
    ),
    top_n: int = Query(default=0, ge=0, le=1000, description="取前 N 个主体; 0=全部"),
    compare: str = Query(default="none", description="对比期: none / prev(环比，紧邻等长前段)"),
):
    """被投诉主体维度分析。

    ★ #67 P2 新增筛选(2026-09-11): `source_type` / `sort_by` / `top_n` / `compare`。
      **全部可选，缺省时行为与改造前逐项一致**（向后兼容契约，有单测守护）。

    ★ 为什么主体页需要筛选(用户需求):
      主体榜默认按总量排序，但管理动作不同 ——
        · 治「升级」→ 看 both(两源都有) + 外部占比高
        · 治「成本」→ 看涉诉退款金额高
        · 治「新冒头」→ 看环比增幅高
      一个固定排序无法支持这三种决策。

    口径(2026-08-25 用户确认):
    - 主体统计仅覆盖有真实主体字段的两类来源:
      商户号投诉(工单 registrant=被投诉商户主体名) 与 外部投诉(external_complaints.company_entity/投诉店铺)。
      内部投诉(商品/服务投诉)工单无主体字段, 不纳入主体统计。
    - 严重性(内部<商户号<外部)以各来源数量分布呈现, 主体级以「外部投诉占比」体现严重度。
    - 升级链(传递性): 按设备SN汇总三源投诉组合, 呈现 内部→商户号→外部 升级漏斗。
    - 退款关联: 主体投诉SN关联的线上退款(refund_method='线上', created_at_raw 在范围内)笔数/金额,
      体现「投诉处理不好最终导致退款」。

    ⚠️ 金额单位: 元(Numeric(12,2) 原值), 与 `/complaints/overview` 一致。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    src_expr = _complaint_source_type_case()

    def _reg_conds(col):
        conds = []
        if ts:
            conds.append(col >= ts)
        if te:
            conds.append(col <= te)
        return conds

    def _day_str(dt) -> str | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return str(dt.astimezone(TZ).date())

    # ── 1. 商户号投诉主体 (工单 registrant=被投诉商户主体名) ──
    merchant_rows = db.execute(
        select(
            ServiceTicket.registrant,
            ServiceTicket.device_sn,
            ServiceTicket.register_time,
        )
        .where(src_expr == "商户号投诉")
        .where(ServiceTicket.registrant.isnot(None), ServiceTicket.registrant != "")
        .where(*_reg_conds(ServiceTicket.register_time))
    ).all()
    merchant_total = len(merchant_rows)
    merchant_by_subject: dict[str, dict] = {}
    for registrant, sn, rt in merchant_rows:
        subject = _norm_subject_entity(registrant)
        if not subject:
            continue
        b = merchant_by_subject.setdefault(subject, {"merchant_count": 0, "external_count": 0, "sns": set(), "trend": {}})
        b["merchant_count"] += 1
        if sn:
            b["sns"].add(sn)
        if rt:
            d = _day_str(rt)
            if d:
                b["trend"][d] = b["trend"].get(d, 0) + 1

    # ── 2. 外部投诉主体 (external_complaints.company_entity / 投诉店铺) ──
    _IGNORE_SHOP = {"/", "-", ""}
    ext_rows = db.execute(
        select(
            _EC.c.company_entity,
            _EC.c.complaint_shop,
            _EC.c.device_sn,
            _EC.c.complaint_date,
        ).where(*_reg_conds(_EC.c.complaint_date))
    ).all()
    external_total = len(ext_rows)
    external_unattributed = 0
    external_by_subject: dict[str, dict] = {}
    for company_entity, complaint_shop, sn, cd in ext_rows:
        subject = None
        if company_entity and str(company_entity).strip():
            subject = _norm_subject_entity(company_entity)
        elif complaint_shop and str(complaint_shop).strip() not in _IGNORE_SHOP:
            subject = _norm_subject_entity(complaint_shop)
        if not subject:
            external_unattributed += 1
            continue
        b = external_by_subject.setdefault(subject, {"merchant_count": 0, "external_count": 0, "sns": set(), "trend": {}})
        b["external_count"] += 1
        if sn:
            b["sns"].add(sn)
        if cd:
            d = _day_str(cd)
            if d:
                b["trend"][d] = b["trend"].get(d, 0) + 1

    # ── 3. 主体合并 (商户号+外部 同名主体合并) ──
    subjects: dict[str, dict] = {}
    for name, b in merchant_by_subject.items():
        subjects[name] = {
            "name": name,
            "merchant_count": b["merchant_count"],
            "external_count": 0,
            "sns": set(b["sns"]),
            "trend": dict(b["trend"]),
        }
    for name, b in external_by_subject.items():
        s = subjects.setdefault(name, {
            "name": name, "merchant_count": 0, "external_count": 0, "sns": set(), "trend": {},
        })
        s["external_count"] = b["external_count"]
        s["sns"].update(b["sns"])
        for d, c in b["trend"].items():
            s["trend"][d] = s["trend"].get(d, 0) + c

    # ── 4. 升级链漏斗 (按SN: 内部/商户号/外部 出现组合) ──
    sn_sources: dict[str, set] = {}
    for sn, src in db.execute(
        select(ServiceTicket.device_sn, src_expr)
        .where(_ticket_category_case() == "投诉")
        .where(ServiceTicket.device_sn.isnot(None))
        .where(*_reg_conds(ServiceTicket.register_time))
    ).all():
        sn_sources.setdefault(sn, set()).add(src or "内部投诉")
    for company_entity, complaint_shop, sn, _cd in ext_rows:
        if sn:
            sn_sources.setdefault(sn, set()).add("外部投诉")

    _FUNNEL_ORDER = [
        ("internal_only", "仅内部投诉"),
        ("merchant_only", "仅商户号投诉"),
        ("external_only", "仅外部投诉"),
        ("internal_merchant", "内部→商户号"),
        ("internal_external", "内部→外部"),
        ("merchant_external", "商户号→外部"),
        ("full_chain", "内部→商户号→外部"),
    ]
    funnel: dict[str, int] = {k: 0 for k, _ in _FUNNEL_ORDER}
    for srcs in sn_sources.values():
        has_int = "内部投诉" in srcs
        has_mer = "商户号投诉" in srcs
        has_ext = "外部投诉" in srcs
        if has_int and has_mer and has_ext:
            funnel["full_chain"] += 1
        elif has_int and has_mer:
            funnel["internal_merchant"] += 1
        elif has_int and has_ext:
            funnel["internal_external"] += 1
        elif has_mer and has_ext:
            funnel["merchant_external"] += 1
        elif has_int:
            funnel["internal_only"] += 1
        elif has_mer:
            funnel["merchant_only"] += 1
        elif has_ext:
            funnel["external_only"] += 1
    escalation_funnel = [{"key": k, "name": n, "count": funnel[k]} for k, n in _FUNNEL_ORDER]

    # ── 5. 退款关联 (主体投诉SN → 线上退款) ──
    all_subject_sns: set[str] = set()
    for s in subjects.values():
        all_subject_sns.update(s["sns"])
    refund_by_sn: dict[str, tuple[int, float]] = {}
    if all_subject_sns:
        for sn, cnt, amt in db.execute(
            select(_REFUND_ALL.c.device_sn, func.count(), func.coalesce(func.sum(_REFUND_ALL.c.refund_amount), 0))
            .where(_REFUND_ALL.c.refund_method == "线上")
            .where(_REFUND_ALL.c.device_sn.in_(all_subject_sns))
            .where(*_reg_conds(_REFUND_ALL.c.created_at_raw))
            .group_by(_REFUND_ALL.c.device_sn)
        ).all():
            refund_by_sn[sn] = (int(cnt), float(amt))

    refund_total_count = 0
    refund_total_amount = 0.0
    for s in subjects.values():
        rc = sum(refund_by_sn[sn][0] for sn in s["sns"] if sn in refund_by_sn)
        ra = sum(refund_by_sn[sn][1] for sn in s["sns"] if sn in refund_by_sn)
        s["refund_count"] = rc
        s["refund_amount"] = round(ra, 2)
        refund_total_count += rc
        refund_total_amount += ra
        s["total"] = s["merchant_count"] + s["external_count"]
        s["escalated"] = s["merchant_count"] > 0 and s["external_count"] > 0
        s.pop("sns", None)

    # ── 6. 主体Top榜 (默认按总量降序; #67 P2 支持筛选与排序) ──
    #   ★ source_type: all / merchant / external / both(升级主体)
    #     用「有效性」过滤而非改聚合 —— 保证 filtered 与 unfiltered 的**主体数口径一致**。
    _all_subjects = list(subjects.values())
    if source_type == "merchant":
        _filtered = [x for x in _all_subjects if x["merchant_count"] > 0]
    elif source_type == "external":
        _filtered = [x for x in _all_subjects if x["external_count"] > 0]
    elif source_type == "both":
        # 升级主体 = 同一主体既有商户号投诉又有外部投诉(事态升级的强信号)
        _filtered = [x for x in _all_subjects if x["merchant_count"] > 0 and x["external_count"] > 0]
    else:  # all: 不缺省过滤, 保持旧行为
        _filtered = _all_subjects

    # ── 6.1 环比(compare=prev): 对比期主体量 → delta_pct ──
    #   口径: 等长前段窗口, 与 /overview 的 prev 一致(紧邻、不重叠、等长)。
    if compare == "prev" and (ts or te):
        from datetime import timedelta as _td
        # 对比期 = 紧邻的等长前段窗口(与 /overview 的 prev 完全一致)
        _span_days = ((te - ts).days + 1) if (ts and te) else 30
        _pts = (ts - _td(days=_span_days)) if ts else None
        _pte = (ts - _td(microseconds=1)) if ts else None
        if _pts and _pte:
            _prev_map: dict[str, int] = {}
            # 商户号侧: 对比期登记
            for registrant, _sn, _rt in db.execute(
                select(ServiceTicket.registrant, ServiceTicket.device_sn, ServiceTicket.register_time)
                .where(src_expr == "商户号投诉")
                .where(ServiceTicket.registrant.isnot(None), ServiceTicket.registrant != "")
                .where(ServiceTicket.register_time >= _pts, ServiceTicket.register_time <= _pte)
            ).all():
                _n = _norm_subject_entity(registrant)
                if _n:
                    _prev_map[_n] = _prev_map.get(_n, 0) + 1
            # 外部侧: 对比期投诉日期
            for company_entity, complaint_shop, _sn, _cd in db.execute(
                select(_EC.c.company_entity, _EC.c.complaint_shop,
                       _EC.c.device_sn, _EC.c.complaint_date)
                .where(_EC.c.complaint_date >= _pts,
                       _EC.c.complaint_date <= _pte)
            ).all():
                _subj = None
                if company_entity and str(company_entity).strip():
                    _subj = _norm_subject_entity(company_entity)
                elif complaint_shop and str(complaint_shop).strip() not in {"/", "-", ""}:
                    _subj = _norm_subject_entity(complaint_shop)
                if _subj:
                    _prev_map[_subj] = _prev_map.get(_subj, 0) + 1
            for _s in _filtered:
                _pv = _prev_map.get(_s["name"], 0)
                _s["prev_total"] = _pv
                _s["delta_pct"] = (
                    round((_s["total"] - _pv) / _pv * 100.0, 1) if _pv > 0 else None
                )

    # ── 6.2 排序(#67 P2) ──
    def _ext_ratio(x):
        return (x["external_count"] / x["total"]) if x["total"] else 0.0

    def _sort_key(x):
        """排序键 —— 四种口径，全部以「降序」为语义(取负)。"""
        if sort_by == "external_ratio":
            return (-_ext_ratio(x), -x["total"], x["name"])
        if sort_by == "refund_amount":
            return (-(x.get("refund_amount") or 0), -x["total"], x["name"])
        if sort_by == "delta":
            # 环比增幅: 无基数的(delta_pct=None)排最后,
            # 避免"对比期为 0 的新冒头主体"霸榜(即便有值也不代表恶化规模)
            _d = x.get("delta_pct")
            return (-(_d if _d is not None else -1e9), -x["total"], x["name"])
        # total(默认, 与改造前完全一致)
        return (-x["total"], -x["external_count"], x["name"])

    _ranked = sorted(_filtered, key=_sort_key)
    # top_n=0 表示全部; 默认 0 → 保持旧行为(旧实现硬编码 [:20])
    #   ⚠️ 为向后兼容, 不传 top_n 时仍按 20 截断(旧行为)。
    subject_ranking = _ranked[:top_n] if top_n > 0 else _ranked[:20]

    # ── 7. 严重性数量分布 (商户号投诉 vs 外部投诉, 含主体识别口径) ──
    severity_distribution = [
        {"name": "商户号投诉", "count": merchant_total},
        {"name": "外部投诉", "count": external_total},
    ]

    # ── 8. 趋势: 全量按日 (商户号工单登记日 + 外部投诉日期) ──
    trend_map: dict[str, dict[str, int]] = {}
    for registrant, _sn, rt in merchant_rows:
        d = _day_str(rt)
        if not d:
            continue
        b = trend_map.setdefault(d, {"商户号投诉": 0, "外部投诉": 0})
        b["商户号投诉"] += 1
    for _ce, _cs, _sn, cd in ext_rows:
        d = _day_str(cd)
        if not d:
            continue
        b = trend_map.setdefault(d, {"商户号投诉": 0, "外部投诉": 0})
        b["外部投诉"] += 1
    subject_trend = [
        {"day": d, "商户号投诉": v["商户号投诉"], "外部投诉": v["外部投诉"]}
        for d, v in sorted(trend_map.items())
    ]

    # ── 9. Top5 主体逐日趋势 ──
    top5 = subject_ranking[:5]
    top_subject_trend = []
    for s in top5:
        trend = s.get("trend") or {}
        top_subject_trend.append({
            "name": s["name"],
            "trend": [{"day": d, "count": trend[d]} for d in sorted(trend)],
        })

    # 排名项不携带逐日 trend(仅Top5趋势需要, 避免响应体膨胀)
    for s in subject_ranking:
        s.pop("trend", None)

    return R(data={
        "summary": {
            "subject_total": len(subjects),
            "merchant_total": merchant_total,
            "external_total": external_total,
            "external_attributed": external_total - external_unattributed,
            "external_unattributed": external_unattributed,
            "refund_count": refund_total_count,
            "refund_amount": round(refund_total_amount, 2),
        },
        "subject_ranking": subject_ranking,
        # ★ #67 P2: 回显生效的筛选条件 —— 让使用者/前端能确认"我看到的是哪一档"
        "filters": {
            "source_type": source_type, "sort_by": sort_by,
            "top_n": top_n, "compare": compare,
            "ranked_total": len(_ranked),   # 筛选后主体总数(用于前端显示 "Top N / 共 M")
        },
        "severity_distribution": severity_distribution,
        "escalation_funnel": escalation_funnel,
        "subject_trend": subject_trend,
        "top_subject_trend": top_subject_trend,
    })
