"""积压未处理投诉: complaint_backlog / complaint_backlog_export。"""

from collections import Counter
from datetime import datetime
from typing import Annotated

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
from app.services.analytics import (
    ACTIVE_AGE_BUCKETS,
    _active_age_bucket,
    _age_days_between,
    _device_dim_attrs,
    _device_dim_options,
)


def _device_dim_groups(db, rows: list[dict]) -> dict[str, list[dict]]:
    """#291 设备维度汇总(积压口径): 设备类型/套餐/商户号 + 激活时间段四桶。

    与明细筛选(#273/#291 active_age)同用 _device_dim_attrs(SN→设备属性,
    激活基准=bms_orders 最早激活日), 保证「汇总数字 ↔ 明细表」同源自洽:
    点击任一桶 → 前端设置对应筛选 → /backlog 明细行数 = 桶计数。
    未填SN/未建档 显式分桶暴露覆盖缺口, 不丢数据。
    """
    attrs = _device_dim_attrs(db, {r["device_sn"] for r in rows if r["device_sn"]})

    def _cat_group(pick) -> list[dict]:
        g: dict[str, int] = {}
        for r in rows:
            sn = r["device_sn"]
            a = attrs.get(sn) if sn else None
            if a is not None:
                key = pick(a) or "(未填)"
            else:
                key = "(未建档)" if sn else "(未填SN)"
            g[key] = g.get(key, 0) + 1
        return [{"name": k, "count": v} for k, v in sorted(g.items(), key=lambda x: -x[1])][:12]

    # 激活时间段: 锚点=工单登记日(与 #291 active_age 筛选同锚), 固定桶顺序返回
    age_g: dict[str, int] = {b: 0 for b in ACTIVE_AGE_BUCKETS}
    for r in rows:
        a = attrs.get(r["device_sn"]) if r["device_sn"] else None
        reg = r["register_time"]
        try:
            ev = datetime.strptime(str(reg)[:10], "%Y-%m-%d").date() if reg else None
        except ValueError:
            ev = None
        age_g[_active_age_bucket(_age_days_between(ev, a["active_date"]) if a else None)] += 1
    return {
        "by_device_type": _cat_group(lambda a: a["type_name"]),
        "by_combo": _cat_group(lambda a: a["combo"]),
        "by_merchant": _cat_group(lambda a: a["merchant"]),
        "by_active_age": [{"name": b, "count": age_g[b]} for b in ACTIVE_AGE_BUCKETS],
    }


@router.get("/backlog", response_model=R, summary="积压未处理投诉审核分析(统一口径: 归一化状态非已完成)")
@cached_json(lambda *a, **kw: f"complaint:backlog:{kw.get('start')}:{kw.get('end')}:{kw.get('source_type')}:{kw.get('status')}:{kw.get('category')}:{kw.get('has_handler')}:{kw.get('page')}:{kw.get('page_size')}:{kw.get('sort')}:{kw.get('device_type')}:{kw.get('combo')}:{kw.get('merchant')}:{kw.get('active_start')}:{kw.get('active_end')}:{kw.get('active_age')}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 60))
def complaint_backlog(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    # ⚠️ 全参数用 Annotated 声明(默认值=真 None/int) —— 不能写 `x = Query(None)`:
    #    cache_warmer 会直调本函数 __wrapped__(不带 Query 声明对象), `Query(None)` 是
    #    truthy 的声明对象, 会被当筛选值进 SQL → can't adapt type 'Query'(#293 实录)。
    source_type: Annotated[str | None, Query(description="内部投诉/商户号投诉/外部投诉/(外)卡商")] = None,
    status: Annotated[str | None, Query(description="归一化状态: 处理中/待处理/待跟进")] = None,
    category: Annotated[str | None, Query(description="按工单服务项(分类)过滤")] = None,
    has_handler: Annotated[bool | None, Query(description="true=已分派(有真实处理人) / false=待分派(无处理人)")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    sort: Annotated[str, Query(description="aging_asc=最旧积压优先(默认) / register_desc / register_asc")] = "aging_asc",
    device_type: Annotated[str | None, Query(max_length=64, description="#273 设备类型(devices.device_type_code)")] = None,
    combo: Annotated[str | None, Query(max_length=128, description="#273 套餐(devices.current_combo_name)")] = None,
    merchant: Annotated[str | None, Query(max_length=128, description="#273 商户号(devices.merchant_name)")] = None,
    active_start: Annotated[str | None, Query(max_length=10, description="#273 激活时间起(YYYY-MM-DD)")] = None,
    active_end: Annotated[str | None, Query(max_length=10, description="#273 激活时间止(YYYY-MM-DD)")] = None,
    active_age: Annotated[str | None, Query(max_length=10, pattern=r"^(le7|8-30|31-180|gt180|none)$",
                                            description="#291 激活后天数桶下钻(登记日−bms_orders最早激活日)")] = None,
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
                                  device_type, combo, merchant, active_start, active_end, active_age)
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
        # #291 设备维度汇总(积压口径, 与明细同源可下钻)
        **_device_dim_groups(db, backlog),
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
    # 同上: Annotated 根治直调 Query 默认值坑(#293)
    source_type: Annotated[str | None, Query(description="内部投诉/商户号投诉/外部投诉")] = None,
    status: Annotated[str | None, Query(description="归一化状态: 处理中/待处理/待跟进")] = None,
    category: Annotated[str | None, Query(description="按工单服务项(分类)过滤")] = None,
    has_handler: Annotated[bool | None, Query(description="true=已分派 / false=待分派")] = None,
    sort: Annotated[str, Query(description="aging_asc=最旧积压优先(默认) / register_desc / register_asc")] = "aging_asc",
    device_type: Annotated[str | None, Query(max_length=64, description="#273 设备类型")] = None,
    combo: Annotated[str | None, Query(max_length=128, description="#273 套餐")] = None,
    merchant: Annotated[str | None, Query(max_length=128, description="#273 商户号")] = None,
    active_start: Annotated[str | None, Query(max_length=10, description="#273 激活时间起")] = None,
    active_end: Annotated[str | None, Query(max_length=10, description="#273 激活时间止")] = None,
    active_age: Annotated[str | None, Query(max_length=10, pattern=r"^(le7|8-30|31-180|gt180|none)$",
                                            description="#291 激活后天数桶下钻(登记日−bms_orders最早激活日)")] = None,
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
                               device_type, combo, merchant, active_start, active_end, active_age)

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
