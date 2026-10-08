"""积压未处理投诉: complaint_backlog / complaint_backlog_export。"""

from collections import Counter

from fastapi import Query

from app.api.deps import CurrentUser, DBSession

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._helpers import (
    _BACKLOG_AGING_BUCKETS,
    _parse_dt,
    _parse_dt_end,
    _query_backlog_rows,
)
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.schemas.common import R
from app.services.analytics import _device_dim_options


@router.get("/backlog", response_model=R, summary="积压未处理投诉审核分析(统一口径: 归一化状态非已完成)")
@cached_json(lambda *a, **kw: f"complaint:backlog:{kw.get('start')}:{kw.get('end')}:{kw.get('source_type')}:{kw.get('status')}:{kw.get('category')}:{kw.get('has_handler')}:{kw.get('page')}:{kw.get('page_size')}:{kw.get('sort')}:{kw.get('device_type')}:{kw.get('combo')}:{kw.get('merchant')}:{kw.get('active_start')}:{kw.get('active_end')}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 60))
def complaint_backlog(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="内部投诉/商户号投诉/外部投诉/(外)卡商"),
    status: str | None = Query(None, description="归一化状态: 处理中/待处理/待跟进"),
    category: str | None = Query(None, description="按工单服务项(分类)过滤"),
    has_handler: bool | None = Query(None, description="true=已分派(有真实处理人) / false=待分派(无处理人)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = Query("aging_asc", description="aging_asc=最旧积压优先(默认) / register_desc / register_asc"),
    device_type: str | None = Query(None, max_length=64, description="#273 设备类型(devices.device_type_code)"),
    combo: str | None = Query(None, max_length=128, description="#273 套餐(devices.current_combo_name)"),
    merchant: str | None = Query(None, max_length=128, description="#273 商户号(devices.merchant_name)"),
    active_start: str | None = Query(None, max_length=10, description="#273 激活时间起(YYYY-MM-DD)"),
    active_end: str | None = Query(None, max_length=10, description="#273 激活时间止(YYYY-MM-DD)"),
):
    """积压未处理投诉的查询/统计/审核分析。

    与 /summary、/analysis 的积压口径不同点: 本接口统一用 _norm_status 归一化判定,
    排除"状态仍为待处理但实际已真实完成(finish_time 晚于登记 3 分钟)"的工单,
    避免统计数与明细表对不上。
    统计维度(不受分页影响): 来源/分类/龄期/处理人/待分派/超期/已核查。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    backlog = _query_backlog_rows(db, ts, te, source_type, status, category, has_handler, sort,
                                  device_type, combo, merchant, active_start, active_end)
    by_source = [{"name": k, "count": v} for k, v in sorted(Counter(r["source"] for r in backlog).items(), key=lambda x: -x[1])]
    by_category = [
        {"name": k, "count": v}
        for k, v in sorted(Counter(r["category"] for r in backlog).items(), key=lambda x: -x[1])[:15]
    ]
    # 投诉原因分布(替代区分度低的来源子类分类)
    _REASON_ORDER = ["退款/售后", "网络/技术", "套餐/计费", "充值/规则", "其他", "未填写"]
    _reason_counter = Counter(r["reason"] for r in backlog)
    by_reason = [
        {"name": k, "count": _reason_counter.get(k, 0)}
        for k in _REASON_ORDER
        if _reason_counter.get(k)
    ] + [
        {"name": k, "count": v}
        for k, v in sorted(_reason_counter.items(), key=lambda x: -x[1])
        if k not in _REASON_ORDER
    ]
    aging_counter = Counter()
    for r in backlog:
        ad = r["aging_days"]
        if ad is None:
            continue  # 无登记时间, 不进龄期分桶
        bucket = next((b for b, lo, hi in _BACKLOG_AGING_BUCKETS if lo <= ad and (hi is None or ad <= hi)), ">14天")
        aging_counter[bucket] += 1
    by_aging = [{"name": b, "count": aging_counter.get(b, 0)} for b, _, _ in _BACKLOG_AGING_BUCKETS]
    # 来源×龄期交叉统计: 每个来源的龄期分布, 回答「哪个来源积压最久」
    #   ⚠️ 2026-09-10 (#47): 由三源扩为**四源** —— 新增 `(外)卡商`(用户要求各统计口单列)。
    _SOURCE_ORDER = ["内部投诉", "商户号投诉", "外部投诉", "(外)卡商"]
    _sa_map: dict[str, dict[str, int]] = {}
    for r in backlog:
        ad = r["aging_days"]
        src = r["source"]
        if ad is None:
            continue
        bucket = next((b for b, lo, hi in _BACKLOG_AGING_BUCKETS if lo <= ad and (hi is None or ad <= hi)), ">14天")
        _sa_map.setdefault(src, {b: 0 for b, _, _ in _BACKLOG_AGING_BUCKETS})[bucket] += 1
    by_source_aging = [
        {
            "source": src,
            "buckets": [{"name": b, "count": _sa_map.get(src, {}).get(b, 0)} for b, _, _ in _BACKLOG_AGING_BUCKETS],
        }
        for src in _SOURCE_ORDER
        if _sa_map.get(src)
    ]
    by_handler = [
        {"name": k, "count": v}
        for k, v in sorted(Counter(r["handler"] for r in backlog).items(), key=lambda x: -x[1])[:15]
    ]
    unassigned = sum(1 for r in backlog if r["handler"] == "未分配")
    overdue = sum(1 for r in backlog if r["aging_days"] is not None and r["aging_days"] >= 8)  # 登记满7天未完成
    audited_total = sum(1 for r in backlog if r["audited"])

    total = len(backlog)
    max_aging = max((r["aging_days"] for r in backlog if r["aging_days"] is not None), default=0)
    start_idx = (page - 1) * page_size
    items = backlog[start_idx:start_idx + page_size]

    return R(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "max_aging": max_aging,
        "by_source": by_source,
        "by_category": by_category,
        "by_reason": by_reason,
        "by_aging": by_aging,
        "by_source_aging": by_source_aging,
        "by_handler": by_handler,
        "unassigned": unassigned,
        "overdue": overdue,
        "audited": audited_total,
        "items": items,
    })

@router.get("/backlog/device-dim-options", response_model=R, summary="积压筛选-设备维度候选项(设备类型/套餐/商户号, #273)")
@cached_json(lambda *a, **kw: "complaint:backlog:device_dim_options", ttl=120)
def complaint_backlog_device_dim_options(db: DBSession, _: CurrentUser):
    """设备维度筛选候选项, 与退款明细 facets 同一口径(devices 表, #271/#273)。"""
    return R(data=_device_dim_options(db))


@router.get("/backlog/export", summary="积压未处理投诉明细导出Excel")
def complaint_backlog_export(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="内部投诉/商户号投诉/外部投诉"),
    status: str | None = Query(None, description="归一化状态: 处理中/待处理/待跟进"),
    category: str | None = Query(None, description="按工单服务项(分类)过滤"),
    has_handler: bool | None = Query(None, description="true=已分派 / false=待分派"),
    sort: str = Query("aging_asc", description="aging_asc=最旧积压优先(默认) / register_desc / register_asc"),
    device_type: str | None = Query(None, max_length=64, description="#273 设备类型"),
    combo: str | None = Query(None, max_length=128, description="#273 套餐"),
    merchant: str | None = Query(None, max_length=128, description="#273 商户号"),
    active_start: str | None = Query(None, max_length=10, description="#273 激活时间起"),
    active_end: str | None = Query(None, max_length=10, description="#273 激活时间止"),
):
    """导出积压未处理投诉明细 Excel。

    与 /backlog 完全同口径(_query_backlog_rows 统一归一化), 不受分页限制, 导出筛选后的全量行。
    """
    import io
    from urllib.parse import quote

    import pandas as pd
    from fastapi.responses import StreamingResponse

    from app.utils.excel import clean_df_for_excel

    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    rows = _query_backlog_rows(db, ts, te, source_type, status, category, has_handler, sort,
                               device_type, combo, merchant, active_start, active_end)

    df = pd.DataFrame([{
        "工单号": r["ticket_no"],
        "来源": r["source"],
        "分类": r["category"],
        "投诉原因": r["reason"],
        "登记时间": r["register_time"],
        "积压天数": r["aging_days"],
        "设备SN": r["device_sn"],
        "投诉内容": r["problem"],
        "处理人": r["handler"],
        "状态": r["status"],
        "完成时间": r["finish_time"],
        "已核查": "是" if r["audited"] else "否",
    } for r in rows])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        clean_df_for_excel(df).to_excel(writer, index=False, sheet_name="积压投诉")
    buf.seek(0)
    fname = quote(f"积压投诉明细_{start or '全量'}_{end or '至今'}.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )
