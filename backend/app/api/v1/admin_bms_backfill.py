"""bms_order 历史订单回填（admin）— 工单 #293

## 为什么需要这个接口
bms_orders 仅覆盖 2026-09-06 起(采集上线后每日增量), 历史订单从未抓取 →
近30天退款 SN 激活覆盖率封顶 78.7%(bms单源)/83.1%(并集兜底)。9 月初前激活的
设备, 其订单/激活记录在 BMS 订单系统中存在但从未采集(#293 实证)。

## 设计要点
- **复用既有同步链路**: sync_all(data_types=["bms_order"], time_ranges=...) 一次
  完成「Playwright 导出 → 解析 → upsert_bms_orders 幂等入库 → BmsSyncLog(manual)」,
  本模块不新增任何抓取/入库代码。
- **BMS 页面时间窗口一次只能跨 1 个月**(用户实证 2026-10-09) → 每次调用只执行
  **单个自然月**窗口; 多月回填由调用方逐月调用, 窗口间冷却 ≥90s(同系统 1 分钟频控)。
- **apply 默认 false**: 仅返回窗口计划, 不访问 BMS、不写库。
- upsert 幂等: 同月重复调用不产生重复行(create_date 锚点), 失败可安全重跑。

## 调用方式(运维侧逐月循环, 每次间隔 ≥90s)
    POST /admin/bms-backfill/month  {"month": "2026-08", "apply": true}
    → {"success": true, "total_rows": ..., "inserted_rows": ...}
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import column, desc, func, select, table

from app.api.deps import AdminUser, DBSession
from app.models.bms_sync_log import BmsSyncLog
from app.schemas.common import R
from app.services.analytics import TZ
from app.services.bms_sync import sync_all

# #338: 回填基线统计读 std_bms_order_all 镜像(仅取两列, 避免全列展开)
_BO = table("std_bms_order", column("device_sn"), column("create_date"))

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/bms-backfill", tags=["admin-bms-backfill"])

TRIGGERED_BY = "backfill:#293"


def _month_window(month: str) -> tuple[datetime, datetime]:
    """'YYYY-MM' → 该自然月 [1日00:00:00, 下月1日00:00:00-1s] (aware 北京)。

    BMS 页面单窗 ≤1 个月; fetcher 端按北京字面值填页面(fetch_utc=False 默认)。
    """
    try:
        first = datetime.strptime(month, "%Y-%m").replace(tzinfo=TZ)
    except ValueError as e:
        raise HTTPException(422, f"month 格式应为 YYYY-MM: {e}") from e
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt - timedelta(seconds=1)


def _baseline(db) -> dict:
    """回填基线: bms_orders 行数/SN数/最早订单时间(读 std_bms_order_all 镜像)。"""
    rows = db.scalar(select(func.count()).select_from(_BO)) or 0
    sns = db.scalar(select(func.count(func.distinct(_BO.c.device_sn)))) or 0
    min_create = db.scalar(select(func.min(_BO.c.create_date)))
    return {"rows": rows, "sns": sns, "min_create": min_create.isoformat() if min_create else None}


@router.get("/status", response_model=R, summary="回填基线与最近回填日志(admin)")
def status(db: DBSession, _: AdminUser):
    """基线统计 + 最近 manual 回填日志(逐月对账用)。"""
    logs = db.execute(
        select(BmsSyncLog)
        .where(BmsSyncLog.data_type == "bms_order", BmsSyncLog.trigger_type == "manual",
               BmsSyncLog.triggered_by == TRIGGERED_BY)
        .order_by(desc(BmsSyncLog.start_time)).limit(24)
    ).scalars().all()
    return R(data={
        "baseline": _baseline(db),
        "recent_logs": [
            {
                "window": f"{(lg.start_time or lg.created_at):%Y-%m}",
                "start_time": lg.start_time.isoformat() if lg.start_time else None,
                "status": lg.status,
                "total_rows": lg.total_rows,
                "inserted_rows": lg.inserted_rows,
                "updated_rows": lg.updated_rows,
                "failed_rows": lg.failed_rows,
                "error": (lg.error or "")[:200],
            }
            for lg in logs
        ],
    })


class MonthRequest(BaseModel):
    """窗口参数: month(自然月) 或 start_date/end_date(任意窗口, 跨度≤31天) 二选一。

    实测(2026-10-09): 31 天整月导出 BMS 后台生成 CSV 超 300s 下载超时 →
    建议 slice 用 **≤10 天**窗口(调用方逐窗调用, 窗间冷却 ≥90s)。
    """
    month: str | None = None
    start_date: str | None = None  # YYYY-MM-DD
    end_date: str | None = None  # YYYY-MM-DD(含当日, 闭区间)
    apply: bool = False  # false=仅返回窗口计划, 不访问 BMS


def _resolve_window(req: MonthRequest) -> tuple[datetime, datetime, str]:
    """解析请求 → (窗口起, 窗口止, 窗口标签), 校验跨度 ≤31 天。"""
    if req.start_date or req.end_date:
        if not (req.start_date and req.end_date):
            raise HTTPException(422, "start_date 与 end_date 必须成对给出")
        try:
            w_start = datetime.strptime(req.start_date, "%Y-%m-%d").replace(tzinfo=TZ)
            w_end = datetime.strptime(req.end_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=TZ)
        except ValueError as e:
            raise HTTPException(422, f"日期格式应为 YYYY-MM-DD: {e}") from e
        label = f"{req.start_date}~{req.end_date}"
    elif req.month:
        w_start, w_end = _month_window(req.month)
        label = req.month
    else:
        raise HTTPException(422, "需提供 month 或 start_date/end_date")
    if (w_end - w_start).total_seconds() > 31 * 86400:
        raise HTTPException(422, "窗口跨度超过 31 天(BMS 页面时间窗口限制), 请切细窗口")
    return w_start, w_end, label


@router.post("/month", response_model=R, summary="回填 bms_order 指定窗口(admin, ≤31天, 建议≤10天)")
async def backfill_month(req: MonthRequest, db: DBSession, _: AdminUser):
    """执行**单窗口**回填(复用 sync_all manual 链路)。

    ⚠️ 整月(31天)导出实测超时 → 建议按 ≤10 天切窗逐窗调用, 窗间冷却 ≥90s;
    ⚠️ 避开每日凌晨批次(约 00:00-01:00 北京)防止与定时同步撞频控。
    """
    w_start, w_end, label = _resolve_window(req)
    if not req.apply:
        return R(data={
            "window_label": label,
            "apply": False,
            "window": {"start": w_start.isoformat(), "end": w_end.isoformat()},
            "baseline": _baseline(db),
        }, message="计划预览(apply=false 未执行)")
    results = await sync_all(
        trigger_type="manual",
        triggered_by=TRIGGERED_BY,
        data_types=["bms_order"],
        time_ranges={"bms_order": (w_start, w_end)},
    )
    r = results[0] if results else {"success": False, "error": "sync_all 无返回"}
    return R(data={
        "window_label": label,
        "apply": True,
        "window": {"start": w_start.isoformat(), "end": w_end.isoformat()},
        "result": r,
        "baseline_after": _baseline(db),
    }, message=f"{label} 回填{'成功' if r.get('success') else '失败'}")
