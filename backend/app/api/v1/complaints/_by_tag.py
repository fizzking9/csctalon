"""投诉按设备标签组合维度: complaint_by_device_tag_endpoint(#249 B3-3)。

设计说明（Design Rationale）:
- 目的(替谁/答何决策): 运营/客服主管回答「哪些标签人群(批次/专项)的投诉最集中」,
  支撑标签圈定的设备群是否需要专项处理动作(清退/换套餐/限速复核)。
- 逻辑(为何这样组织): 复用 analytics.complaint.complaint_by_device_tag(口径与 #87 同源),
  挂在 complaints 子路由下 —— 缓存 key 前缀 `complaint:` 自动纳入 cache_warmer 失效 pattern。
- 口径: 投诉判据 = 工单服务类别空白 OR '投诉'(complaint_scope 唯一出口); 标签取 active 绑定;
  一台设备多标签时每个标签各计一次(组合维度, 非互斥分桶, 总和≠投诉总量);
  未打标 SN 归入 "(未打标)" 桶暴露覆盖缺口。
- 取舍: 不在本端点做人均/比率 —— 标签群不是真实组织单元(人数口径无意义);
  不下钻到单条工单 —— 下钻走 /devices/tags/{tag_id}/devices → 设备 360 时间线。
- 工单: #249 · 设计: A-20261007-002 §3.6 标签事实化
"""

from fastapi import Query

from app.api.deps import CurrentUser, DBSession
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.schemas.common import R
from app.services.analytics import complaint_by_device_tag


@router.get("/by-device-tag", response_model=R,
            summary="投诉按设备标签组合维度(组合口径, 非互斥分桶) #249")
@cached_json(
    lambda *a, **kw: (
        f"complaint:by-device-tag:{kw.get('date')}:{kw.get('start')}:{kw.get('end')}:{kw.get('limit')}"
    ),
    ttl=lambda **kw: cache_ttl_for(kw.get("start"), kw.get("end"), 300),
)
def complaint_by_device_tag_endpoint(
    db: DBSession,
    _: CurrentUser,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    """#249 B3-3 (A-20261007-002 §3.6): 投诉按设备标签组合聚合。

    ⚠️ 「组合维度」语义: 一台设备可有多个 active 标签, 该 SN 的投诉在每个所属标签
       各计一次 —— 各标签单量之和 **不等于** 投诉总量, 不与 /health-metrics 对账。
    ⚠️ "(未打标)" 桶 = 投诉 SN 中无任何 active 标签的部分, 用于暴露标签覆盖率缺口。
    """
    return R(
        data=complaint_by_device_tag(db, date=date, start=start, end=end, limit=limit)
    )
