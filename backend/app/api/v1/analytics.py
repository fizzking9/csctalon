"""分析看板路由 - 4 大分析

[DEPRECATED] 本模块的硬编码端点已被自助分析引擎取代。
- 新端点: POST /api/v1/analysis-views/execute (支持 view_id 或 inline_config)
- 预设视图: 已通过 preset_views_seed.py 注册 13 个预设视图,对应本模块的 4 大看板
- 引擎服务: app.services.analysis_engine.execute_view

本模块保留作为:
1. 旧前端页面(Operation.vue/Cash.vue 等)的兼容端点,响应格式不变
2. 数据对照基准(已验证与引擎结果完全一致)

后续旧前端页面迁移到 ViewRender.vue 后,本模块可删除。
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession
from app.core.redis_client import cached_call
from app.schemas.common import R
from app.services import analytics as A

router = APIRouter(prefix="/analytics", tags=["分析看板(已废弃-见analysis-views)"])


def _parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


@router.get("/summary", response_model=R, summary="首页汇总卡片")
def summary(db: DBSession, _: CurrentUser):
    return R(data=A.dashboard_summary(db))


# ---------- 看板1:运营分析 ----------


@router.get("/operation/trend", response_model=R, summary="退款趋势(按日)")
def op_trend(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.operation_trend(db, _parse_dt(start), _parse_dt(end)))


@router.get("/operation/status", response_model=R, summary="退费状态分布")
def op_status(db: DBSession, _: CurrentUser):
    return R(data=A.operation_status_dist(db))


@router.get("/operation/reason", response_model=R, summary="退费原因TOP")
def op_reason(db: DBSession, _: CurrentUser, limit: int = Query(10, ge=1, le=50)):
    return R(data=A.operation_reason_top(db, limit))


@router.get("/operation/method", response_model=R, summary="退款方式占比")
def op_method(db: DBSession, _: CurrentUser):
    return R(data=A.operation_method_dist(db))


# ---------- 看板2:现金流分析 ----------


@router.get("/cash/trend", response_model=R, summary="真实现金流出趋势")
def cash_trend(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.cash_out_trend(db, _parse_dt(start), _parse_dt(end)))


@router.get("/cash/breakdown", response_model=R, summary="真实现金流构成(直接退款+用户提现)")
def cash_breakdown(db: DBSession, _: CurrentUser):
    return R(data=A.cash_out_breakdown(db))


@router.get("/cash/wallet-vs-withdraw", response_model=R, summary="退回钱包vs实际提现")
def cash_compare(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.wallet_vs_withdraw(db, _parse_dt(start), _parse_dt(end)))


@router.get("/cash/merchant", response_model=R, summary="商户号对账")
def cash_merchant(db: DBSession, _: CurrentUser):
    return R(data=A.merchant_cash_summary(db))


@router.get("/cash/wallet-balance", response_model=R, summary="钱包余额分布")
def cash_balance(db: DBSession, _: CurrentUser, limit: int = Query(20, ge=1, le=100)):
    return R(data=A.wallet_balance_top(db, limit))


# ---------- 看板3:退款分类分析 ----------


@router.get("/category/service-item", response_model=R, summary="按服务项分类")
def cat_service(db: DBSession, _: CurrentUser):
    return R(data=A.category_by_service_item(db))


@router.get("/category/merchant", response_model=R, summary="按商户分类")
def cat_merchant(db: DBSession, _: CurrentUser, limit: int = Query(20, ge=1, le=100)):
    return R(data=A.category_by_merchant(db, limit))


@router.get("/category/package", response_model=R, summary="按套餐分类")
def cat_package(db: DBSession, _: CurrentUser, limit: int = Query(20, ge=1, le=100)):
    return R(data=A.category_by_package(db, limit))


# ---------- 看板4:人员操作分析 ----------


@router.get("/operator/ticket", response_model=R, summary="人员工单处理统计")
def op_ticket(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.operator_ticket_stats(db, _parse_dt(start), _parse_dt(end)))


@router.get("/operator/audit", response_model=R, summary="人员审核统计")
def op_audit(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.operator_audit_stats(db, _parse_dt(start), _parse_dt(end)))


@router.get("/operator/refund", response_model=R, summary="人员退费操作统计")
def op_refund(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.operator_refund_stats(db, _parse_dt(start), _parse_dt(end)))


# ---------- 看板5: 客服接待看板 (对齐周报4模块) ----------


@router.get("/reception/overview", response_model=R, summary="接待看板-总体接待量(按周)")
def reception_overview(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.reception_overview_trend(db, _parse_dt(start), _parse_dt(end)))


@router.get("/reception/category-summary", response_model=R, summary="接待看板-接待分项汇总(按周×分类)")
def reception_category(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.reception_category_summary(db, _parse_dt(start), _parse_dt(end)))


@router.get("/reception/per-capita", response_model=R, summary="接待看板-接待分项人均工作(按周×专项组)")
def reception_pc(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.reception_per_capita(db, _parse_dt(start), _parse_dt(end)))


@router.get("/reception/group-detail", response_model=R, summary="接待看板-各组接待明细(按组×人员×日)")
def reception_group(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.reception_group_detail(db, _parse_dt(start), _parse_dt(end)))


# ---------- 看板6: 退款-提现全链路看板 (合并 SN对账 + 发起退款→实际到账时效分析) ----------


@router.get("/refund-withdrawal/overview", response_model=R, summary="提现全链路看板-顶部KPI总览(路径对比+到账率)")
def rw_overview(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.refund_withdrawal_overview(db, start, end))


@router.get("/refund-withdrawal/time-distribution", response_model=R, summary="提现全链路看板-两条路径耗时分桶(用于柱状图)")
def rw_time_dist(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.refund_withdrawal_time_distribution(db, start, end))


@router.get("/refund-withdrawal/dimensions", response_model=R, summary="提现全链路看板-按处理人/原因/方式TopN聚合")
def rw_dims(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None,
            topn: int = Query(20, ge=1, le=100)):
    return R(data=A.refund_withdrawal_dimensions(db, start, end, topn))


@router.get("/refund-withdrawal/detail", response_model=R, summary="提现全链路看板-明细列表(支持排序/慢单筛选/分页)")
def rw_detail(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None,
              sort: str = Query("days_desc", pattern=r"^(days_(desc|asc)|amount_desc|d1_desc)$"),
              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
              slow_only: bool = False, path: str = Query("ALL", pattern=r"^(ALL|AB|A|B|X)$")):
    return R(data=A.refund_withdrawal_detail(db, start, end, sort, page, page_size, slow_only, path))


@router.get("/refund-withdrawal/daily-split", response_model=R, summary="提现全链路看板-按实际出款日拆分两条流(直退vs钱包提现)堆叠图")
def rw_daily_split(db: DBSession, _: CurrentUser, start: str | None = None, end: str | None = None):
    return R(data=A.refund_withdrawal_daily_split(db, start, end))


@router.get("/refund-withdrawal/sleeping", response_model=R, summary="提现时效-钱包沉淀清单(转入未提现,分页)")
def rw_sleeping(db: DBSession, _: CurrentUser,
                start: str | None = None, end: str | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)):
    return R(data=A.refund_withdrawal_sleeping(db, start, end, page=page, page_size=page_size))


# ---------- 看板7: 今日看板 (单日切面 + 实时节奏) ----------


def _add_filter_params(q):
    """给查询函数添加人员筛选参数装饰器"""
    return q


@router.get("/today/kpi", response_model=R, summary="今日看板-KPI汇总卡片(6项+昨日环比)")
def today_kpi_endpoint(db: DBSession, _: CurrentUser, date: str | None = None, start: str | None = None, end: str | None = None,
                      department: str | None = Query(None, description="按部门筛选"),
                      group_id: int | None = Query(None, description="按分组ID筛选"),
                      specialty_id: int | None = Query(None, description="按专项ID筛选")):
    # 60s 缓存: 切换聚合粒度时同参数重复请求命中缓存, 避免重复 DB 聚合
    key = f"today:kpi:{date}:{start}:{end}"
    return R(data=cached_call(key, 60, A.today_kpi, db, date, start, end))


@router.get("/today/hourly-trend", response_model=R, summary="今日看板-时间节奏(接待量柱+退款金额线,双Y轴,支持hour/day/week/month粒度)")
def today_hourly(db: DBSession, _: CurrentUser, date: str | None = None, start: str | None = None, end: str | None = None,
                 trend_grain: str | None = Query(None, pattern="^(hour|day|week|month)$", description="聚合粒度: hour=按小时, day=按日, week=按周, month=按月; 不传则根据时间跨度自动选择"),
                 department: str | None = Query(None), group_id: int | None = Query(None), specialty_id: int | None = Query(None)):
    key = f"today:hourly:{date}:{start}:{end}:{trend_grain}"
    return R(data=cached_call(key, 60, A.today_hourly_trend, db, date, start, end, trend_grain=trend_grain))


@router.get("/today/reception", response_model=R, summary="今日看板-接待分项(分类饼+Top客服柱)")
def today_reception(db: DBSession, _: CurrentUser, date: str | None = None, start: str | None = None, end: str | None = None,
                    department: str | None = Query(None), group_id: int | None = Query(None), specialty_id: int | None = Query(None)):
    key = f"today:reception:{date}:{start}:{end}"
    return R(data=cached_call(key, 60, A.today_reception_breakdown, db, date, start, end))


@router.get("/today/refund", response_model=R, summary="今日看板-退款分项(原因Top10+方式分布)")
def today_refund(db: DBSession, _: CurrentUser, date: str | None = None, start: str | None = None, end: str | None = None,
                 department: str | None = Query(None), group_id: int | None = Query(None), specialty_id: int | None = Query(None)):
    key = f"today:refund:{date}:{start}:{end}"
    return R(data=cached_call(key, 60, A.today_refund_breakdown, db, date, start, end))


@router.get("/today/queue", response_model=R, summary="今日看板-处理队列(今日到期待办+进行中专项+今日异常)")
def today_queue_endpoint(db: DBSession, _: CurrentUser, date: str | None = None, start: str | None = None, end: str | None = None,
                         department: str | None = Query(None), group_id: int | None = Query(None), specialty_id: int | None = Query(None)):
    key = f"today:queue:{date}:{start}:{end}"
    return R(data=cached_call(key, 60, A.today_queue, db, date, start, end))


@router.get("/today/staff", response_model=R, summary="今日看板-人均效率表(接待/退款/完工单合成权重)")
def today_staff(db: DBSession, _: CurrentUser, date: str | None = None,
                start: str | None = None, end: str | None = None,
                topn: int = Query(30, ge=1, le=200),
                qw_only: bool = Query(False, description="仅显示已绑定企微(Person.qw_user_id非空)的人员"),
                aggregate_by: str = Query("person", pattern="^(person|specialty|group|all)$",
                                          description="聚合方式: person=按人明细/specialty=按专项/group=按行政组/all=全员合计一行"),
                department: str | None = Query(None, description="按部门筛选"),
                group_id: int | None = Query(None, description="按分组ID筛选"),
                specialty_id: int | None = Query(None, description="按专项ID筛选")):
    # staff 接口是最重的(含 DISTINCT ON 关联), 缓存 90s 进一步降低切换开销
    key = f"today:staff:{date}:{start}:{end}:{topn}:{int(qw_only)}:{aggregate_by}:{department}:{group_id}:{specialty_id}"
    return R(data=cached_call(key, 90, A.today_staff_efficiency, db, date, start, end, topn, qw_only, aggregate_by,
                                           department=department, group_id=group_id, specialty_id=specialty_id))


@router.get("/today/refund-recovery", response_model=R, summary="今日看板-退款挽回专项分析(独立视图,不冲抵接待效率)")
def today_refund_recovery_endpoint(
    db: DBSession, _: CurrentUser, date: str | None = None,
    start: str | None = None, end: str | None = None,
    department: str | None = Query(None), group_id: int | None = Query(None), specialty_id: int | None = Query(None)
):
    """退款挽回专项分析: 基于退款挽回人员接待SN ∩ 最终退款SN 的交集分析.

    核心口径(2026-08-11修正: 分组≠专项, 不按组名判定):
      · 分母 = 所有人员(专职退款挽回专员+组长/主管+其他岗位)在周期内 cs_record 接待的 problem_category='退款' 的去重 SN
      · 专职退款挽回人员判定: Person.specialty='退款挽回' (与分组无关, 同一组内可有多专项分工)
      · 非专职人员挽回: 任何人员(含组长/主管)接待退款类SN但最终未产生退款行为 → 也算挽回成功
      · 挽留失败 = 分母SN 中, 同期出现在 refunds 线上退款单(非驳回/无需退款) 或 service_tickets service_category=退款 工单(非终止)中的 SN
      · 挽留成功 = 分母SN - 挽留失败SN
      · 成功率 = 挽留成功 / 分母
    """
    key = f"today:rr:{date}:{start}:{end}"
    return R(data=cached_call(key, 60, A.refund_recovery_analysis, db, date, start, end))


# ==============================================================
# 退款看板深度分析 - 9 个新接口 (2026-08-12)
# ==============================================================
@router.get("/refund/personnel-analysis", response_model=R, summary="退款看板-人员分析汇总")
def refund_personnel_analysis_endpoint(
    db: DBSession, _: CurrentUser,
    start: str | None = Query(None), end: str | None = Query(None),
    department: str | None = Query(None), group: str | None = Query(None),
    specialty: str | None = Query(None), staff_type: str | None = Query(None),
):
    key = f"refund:pa:{start}:{end}:{department}:{group}:{specialty}:{staff_type}"
    return R(data=cached_call(key, 60, A.refund_personnel_analysis, db, start, end,
                               department=department, group=group, specialty=specialty, staff_type=staff_type))


@router.get("/refund/person-devices", response_model=R, summary="退款看板-人员接待设备清单")
def refund_person_devices_endpoint(
    db: DBSession, _: CurrentUser,
    operator: str = Query(..., description="人员别名(如 企微-叶珍凤)"),
    start: str | None = Query(None), end: str | None = Query(None),
    result: str = Query("all", pattern="^(all|success|failed)$"),
):
    key = f"refund:pd:{operator}:{start}:{end}:{result}"
    return R(data=cached_call(key, 60, A.refund_person_devices, db, operator, start, end, result=result))


@router.get("/refund/personnel-trend", response_model=R, summary="退款看板-人员趋势")
def refund_personnel_trend_endpoint(
    db: DBSession, _: CurrentUser,
    start: str | None = Query(None), end: str | None = Query(None),
    operators: str | None = Query(None, description="逗号分隔的人员别名列表"),
    grain: str = Query("day", pattern="^(day|week)$"),
):
    key = f"refund:pt:{start}:{end}:{operators}:{grain}"
    return R(data=cached_call(key, 120, A.refund_personnel_trend, db, start, end, operators=operators, grain=grain))


@router.get("/refund/person-reason-matrix", response_model=R, summary="退款看板-人员×原因矩阵")
def refund_person_reason_matrix_endpoint(
    db: DBSession, _: CurrentUser,
    start: str | None = Query(None), end: str | None = Query(None),
    top: int = Query(15, ge=1, le=50),
):
    key = f"refund:prm:{start}:{end}:{top}"
    return R(data=cached_call(key, 120, A.refund_person_reason_matrix, db, start, end, top=top))


@router.get("/refund/device-analysis", response_model=R, summary="退款看板-设备分析")
def refund_device_analysis_endpoint(
    db: DBSession, _: CurrentUser,
    start: str | None = Query(None), end: str | None = Query(None),
    reason: str | None = Query(None), amount_min: float | None = Query(None),
    amount_max: float | None = Query(None), reception_count_min: int | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    key = f"refund:da:{start}:{end}:{reason}:{amount_min}:{amount_max}:{reception_count_min}:{limit}"
    return R(data=cached_call(key, 120, A.refund_device_analysis, db, start, end,
                               reason=reason, amount_min=amount_min, amount_max=amount_max,
                               reception_count_min=reception_count_min, limit=limit))


@router.get("/refund/device-detail", response_model=R, summary="退款看板-设备详情")
def refund_device_detail_endpoint(
    db: DBSession, _: CurrentUser,
    device_sn: str = Query(..., description="设备SN"),
    start: str | None = Query(None), end: str | None = Query(None),
):
    key = f"refund:dd:{device_sn}:{start}:{end}"
    return R(data=cached_call(key, 60, A.refund_device_detail, db, device_sn, start, end))


@router.get("/refund/type-trend", response_model=R, summary="退款看板-类型趋势")
def refund_type_trend_endpoint(
    db: DBSession, _: CurrentUser,
    start: str | None = Query(None), end: str | None = Query(None),
    grain: str = Query("day", pattern="^(day|week|month)$"),
    dimension: str = Query("reason", pattern="^(reason|staff_type)$"),
):
    key = f"refund:tt:{start}:{end}:{grain}:{dimension}"
    return R(data=cached_call(key, 120, A.refund_type_trend, db, start, end, grain=grain, dimension=dimension))


@router.get("/refund/problem-type-detail", response_model=R, summary="退款看板-problem_type细分Top10")
def refund_problem_type_detail_endpoint(
    db: DBSession, _: CurrentUser,
    start: str | None = Query(None), end: str | None = Query(None),
    top: int = Query(10, ge=1, le=30),
):
    key = f"refund:ptd:{start}:{end}:{top}"
    return R(data=cached_call(key, 120, A.refund_problem_type_detail, db, start, end, top=top))


@router.get("/refund/group-comparison", response_model=R, summary="退款看板-组间对比")
def refund_group_comparison_endpoint(
    db: DBSession, _: CurrentUser,
    start: str | None = Query(None), end: str | None = Query(None),
):
    key = f"refund:gc:{start}:{end}"
    return R(data=cached_call(key, 120, A.refund_group_comparison, db, start, end))


@router.get("/refund/decision-overview", response_model=R, summary="退款看板-决策概览(资金全景/钱包水位/涉诉归因/原因人员)")
def refund_decision_overview_endpoint(
    db: DBSession, _: CurrentUser,
    start: str | None = Query(None), end: str | None = Query(None),
    grain: str = Query("day", pattern="^(day|week)$"),
):
    key = f"refund:do:{start}:{end}:{grain}"
    return R(data=cached_call(key, 60, A.refund_decision_overview, db, start, end, grain=grain))


@router.get("/refund/export", summary="退款明细导出Excel(原始数据,自定义分析用)")
def refund_export_endpoint(
    db: DBSession, _: CurrentUser,
    start: str | None = Query(None), end: str | None = Query(None),
):
    """导出当前筛选区间内退款单原始明细 Excel(含涉诉分类/归一操作人/关联工单服务项)."""
    import io
    import pandas as pd
    from urllib.parse import quote
    from fastapi.responses import StreamingResponse

    rows = A.refund_export_original(db, start, end)
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="退款明细")
    buf.seek(0)
    fname = quote(f"退款明细_{start or '全量'}_{end or '至今'}.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )
