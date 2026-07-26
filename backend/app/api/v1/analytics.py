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
