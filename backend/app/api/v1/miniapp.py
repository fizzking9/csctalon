"""微信小程序精简业务看板 + 采集触发 API

设计目标(小程序界面尽量精简):
  1. GET  /miniapp/summary      — 精简看板: 今日 KPI + 退款挽回专项摘要 (复用 analytics 缓存)
  2. POST /miniapp/sync/trigger — 超级管理员启动数据采集 (BMS + SCRM 并发)
  3. GET  /miniapp/sync/status  — 查询当前采集任务进度

权限裁剪:
  - summary / status: 任意登录用户 (CurrentUser)
  - sync/trigger: 仅系统管理员 (AdminUser), 对应「超级管理员通过小程序启动数据采集」

缓存: 与 Web 端复用 today:* 键, 同步完成后由 sync_task_manager 统一失效。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.database import SessionLocal
from app.core.redis_client import cached_call
from app.schemas.common import R
from app.services import analytics as A
from app.services.bms_sync import DEFAULT_SYNC_TYPES, sync_all
from app.services.scrm_sync_service import (
    DATA_TYPE_OPERATOR,
    DATA_TYPE_SESSION_GROUP,
    DATA_TYPE_SESSION_PRIVATE,
    DATA_TYPE_SESSION_VIRTUAL,
    sync_scrm_all,
)
from app.services.sync_task_manager import sync_task_manager

router = APIRouter(prefix="/miniapp", tags=["小程序精简看板"])

TZ_CN = timezone(timedelta(hours=8))

SCRM_TYPES = [
    DATA_TYPE_OPERATOR,
    DATA_TYPE_SESSION_PRIVATE,
    DATA_TYPE_SESSION_GROUP,
    DATA_TYPE_SESSION_VIRTUAL,
]


class SyncTriggerRequest(BaseModel):
    """小程序触发数据采集请求(默认增量)"""

    time_range: str = Field(default="incremental", description="时间范围预设: incremental/today")
    ignore_cooldown: bool = Field(default=False, description="忽略冷却时间(仍防并发)")


@router.get("/summary", response_model=R, summary="小程序-精简业务看板摘要")
def miniapp_summary(db: DBSession, _: CurrentUser, date: str | None = None):
    """今日 KPI + 退款挽回专项摘要, 与 Web 今日看板共享缓存键。"""
    kpi = cached_call(f"today:kpi:{date}:None:None", 60, A.today_kpi, db, date, None, None)
    recovery = cached_call(f"today:rr:{date}:None:None", 60, A.refund_recovery_analysis, db, date, None, None)
    return R(data={"kpi": kpi, "recovery": recovery})


@router.get("/sync/status", response_model=R, summary="小程序-查询当前采集任务进度")
def miniapp_sync_status(me: AdminUser):
    gs = sync_task_manager.get_global_status()
    return R(data={
        "is_syncing": gs["is_syncing"],
        "is_writing_heavy": gs["is_writing_heavy"],
        "cooldown_remaining": gs["cooldown_remaining"],
        "last_success_at": gs["last_success_at"],
        "percent": gs["percent"],
        "current_phase": gs["current_phase"],
        "current_source": gs["current_source"],
        "task_id": gs["current_task_id"],
    })


@router.post("/sync/trigger", response_model=R, summary="小程序-超级管理员启动数据采集(BMS+SCRM并发)")
async def miniapp_sync_trigger(payload: SyncTriggerRequest, me: AdminUser):
    """管理员在小程序端一键启动数据采集。

    与调度层一致: BMS 与 SCRM 各自 runner 并发执行, BMS 冷却期间穿插 SCRM。
    """
    bms_types = list(DEFAULT_SYNC_TYPES)
    all_types = bms_types + SCRM_TYPES

    # 计算 BMS 增量时间范围 (SCRM 内部自行按 last_success 增量)
    db = SessionLocal()
    try:
        if payload.time_range == "today":
            now_cn = datetime.now(TZ_CN)
            start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
            time_ranges_dt = {dt: (start, now_cn) for dt in bms_types}
            time_ranges_display = {dt: (start.strftime("%m-%d 00:00(今日)"), now_cn.strftime("%m-%d %H:%M:%S")) for dt in bms_types}
        else:
            ranges = sync_task_manager.compute_incremental_ranges(db, bms_types)
            time_ranges_dt = {dt: (ranges[dt][0], ranges[dt][1]) for dt in bms_types}
            time_ranges_display = {dt: (ranges[dt][2], ranges[dt][3]) for dt in bms_types}
    finally:
        db.close()

    for dt in SCRM_TYPES:
        time_ranges_display[dt] = ("按 last_success 增量", datetime.now(TZ_CN).strftime("%m-%d %H:%M:%S"))

    async def bms_runner(task_id: str, on_progress_cb) -> list[dict]:
        return await sync_all(
            trigger_type="manual",
            triggered_by=me.username,
            on_progress=on_progress_cb,
            data_types=bms_types,
            time_ranges=time_ranges_dt,
        )

    async def scrm_runner(task_id: str, on_progress_cb) -> list[dict]:
        return await sync_scrm_all(
            trigger_type="manual",
            triggered_by=me.username,
            on_progress=on_progress_cb,
            sync_types=SCRM_TYPES,
        )

    try:
        task_id, msg = await sync_task_manager.submit_multi(
            trigger_type="manual",
            triggered_by=me.username,
            data_types=all_types,
            time_ranges=time_ranges_dt,
            time_ranges_display=time_ranges_display,
            runners=[bms_runner, scrm_runner],
            ignore_cooldown=payload.ignore_cooldown,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return R(data={"task_id": task_id, "message": msg})