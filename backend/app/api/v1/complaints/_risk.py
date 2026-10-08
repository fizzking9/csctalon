"""夜间投诉风险优先清单端点: risk_priority_list_endpoint(#250 B3-4)。

设计说明（Design Rationale）:
- 目的(替谁/答何决策): 投诉专员无人值守时段前的优先介入清单 ——
  「哪些设备最可能升级为外部投诉」, 按风险分降序。
- 逻辑(为何这样组织): 复用 complaint_risk_service.compute_complaint_risk_priority_list
  (确定性四维评分: 充值多/流量少/愿沟通/同题重复), 挂在 complaints 子路由下;
  v1 只交后端 API, 前端接入另开工单。
- 口径: 投诉判据 = 工单 service_category 空白或='投诉'(complaint_scope 唯一出口);
  流量取最近完整月; 同源组取 status in (auto/confirmed)。
- 取舍: 不做缓存(4 个 GROUP BY 聚合, 2000+ 设备秒级, 每次实时算保证新鲜);
  不做自动推送(专员主动看); 权重/阈值走查询参数覆盖, 配置表化属 O-5。
- 工单: #250 · 排期: docs/reports/20261009-下一步开发计划-排期规划.md §六 B3-4
"""

from fastapi import Query

from app.api.deps import CurrentUser, DBSession
from app.api.v1.complaints._router import router
from app.schemas.common import R
from app.services.complaint_risk_service import compute_complaint_risk_priority_list


@router.get("/risk-priority-list", response_model=R,
            summary="夜间投诉风险优先清单(确定性评分, 按总分降序) #250")
def risk_priority_list_endpoint(
    db: DBSession,
    _: CurrentUser,
    limit: int = Query(100, ge=1, le=500),
    min_score: float = Query(0.0, ge=0, le=100),
    low_flow_gb: float = Query(5.0, gt=0, description="流量少阈值(GB, 最近完整月)"),
):
    """#250 B3-4: 投诉专员优先清单。

    返回 {total_scored, items:[{device_sn, total, level, scores, facts}]}。
    每项 scores/facts 可下钻解释: 四维得分 + 原始事实(充值笔数/金额、最近月流量、
    30 天接待次数、90 天投诉工单数、是否同源组、180 天外部投诉数)。
    """
    return R(data=compute_complaint_risk_priority_list(
        db, limit=limit, min_score=min_score, low_flow_gb=low_flow_gb,
    ))
