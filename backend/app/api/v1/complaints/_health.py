"""健康度指标: complaint_health_metrics_endpoint(#87)。"""

from app.api.deps import CurrentUser, DBSession

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.schemas.common import R
from app.services.analytics import (
    complaint_health_metrics,
)


@router.get("/health-metrics", response_model=R,
            summary="投诉健康度指标(活跃SN/投诉率三维/三岗位负荷/重复投诉率) #87")
@cached_json(lambda *a, **kw: f"complaint:health:{kw.get('date')}:{kw.get('start')}:{kw.get('end')}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 60))
def complaint_health_metrics_endpoint(
    db: DBSession,
    _: CurrentUser,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    """#87 第一批投诉健康度指标（设计文档 A-20260914-001 §4.1）。

    ⚠️ 缓存 key 前缀为 `complaint:` → **自动纳入** `cache_warmer.SCOPE_PATTERNS`
       的 `complaint:*` 失效 pattern，口径变更后清缓存即生效（无需另加配置）。

    ★ 口径要点（详见 `analytics.complaint_health_metrics._spec`，前端 ⓘ 直接引用）：
      · 活跃 SN = 窗口内有接待记录的去重设备 SN（所有比率的分母基底）
      · 投诉率 = 投诉 SN / 活跃 SN（三个维度：全站/对外/对内）
      · 岗位负荷 = **岗位归因**分子 / 在岗人数；组长与专员分子为投诉结案数，客服为接待数
      · **重复投诉率** = 30 天内同 SN 投诉 ≥2 次占比 —— 是**升级的代理信号**，
        不是真实升级率（真实值待升级投诉表到位）
      · 分母为 0 时比率返回 **null**（前端显示「—」而非 0%，避免误读为"零投诉"）
    """
    return R(data=complaint_health_metrics(db, date=date, start=start, end=end))
