"""投诉分析扩展维度端点: complaint_extended_dims_endpoint(#252 B3-11)。

设计说明（Design Rationale）:
- 目的(替谁/答何决策): 运营/客服主管回答「投诉集中在哪些店铺/商户号/设备型号/
  套餐/售后期阶段」, 支撑针对性动作(店铺约谈/商户整改/型号召回评估/套餐调整)。
- 逻辑(为何这样组织): 复用 analytics.complaint.complaint_extended_dims(判据与 #87 同源),
  挂在 complaints 子路由下 —— 缓存 key 前缀 `complaint:` 自动纳入 cache_warmer 失效 pattern。
- 口径: 投诉判据 = 工单 service_category 空白或='投诉'(complaint_scope 唯一出口);
  店铺/商户号取 is_current 建链(回答「现在谁在投诉」, 非时点快照); 套餐经
  combo_name_alias(is_current) 归一; 未建链显式分桶暴露覆盖缺口。
- 取舍: 不做金额维度(投诉工单金额字段稀疏); 不做时点 SCD 快照(如需另开工单);
  每维 TopN + 其余归 (其他)。
- 工单: #252 · 排期: docs/reports/20261009-下一步开发计划-排期规划.md §六 B3-11
"""

from fastapi import Query

from app.api.deps import CurrentUser, DBSession
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.schemas.common import R
from app.services.analytics import complaint_extended_dims


@router.get("/extended-dims", response_model=R,
            summary="投诉扩展维度分布(店铺/商户号/设备型号/套餐/激活后时长) #252")
@cached_json(
    lambda *a, **kw: (
        f"complaint:extended-dims:{kw.get('date')}:{kw.get('start')}:{kw.get('end')}:{kw.get('limit')}"
    ),
    ttl=lambda **kw: cache_ttl_for(kw.get("start"), kw.get("end"), 300),
)
def complaint_extended_dims_endpoint(
    db: DBSession,
    _: CurrentUser,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(10, ge=1, le=50),
):
    """#252 B3-11: 五维投诉分布, 每维 TopN + (其他)。

    ⚠️ 店铺/商户号/套餐维度取**当前**建链与别名(is_current)——回答「现在哪些
       店铺/商户/套餐在投诉」, 运营动作面向当前归属; 非投诉发生时点快照。
    """
    return R(
        data=complaint_extended_dims(db, date=date, start=start, end=end, limit=limit)
    )
