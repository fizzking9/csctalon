"""BMS + SCRM 数据同步 API

2026-08-02 优化: 引入 SyncTaskManager 管理长任务
  - POST /task/submit   异步提交同步任务(立即返回 task_id)
  - GET  /task/status   查询任务进度(轮询用)
  - GET  /task/preflight 预检:是否可执行/增量时间范围/冷却剩余
  - POST /sync-all      (旧接口,兼容旧前端)内部改为走新 submit 模式

2026-Q2 新增 SCRM(微信聚合平台)对接:
  - GET  /scrm/status    查询 SCRM 4 个数据源最近同步状态
  - POST /scrm/sync      手动触发 SCRM 同步(可指定 sync_types + 时间范围)
  - GET  /scrm/logs      查询 SCRM 同步日志
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.api.deps import AdminUser, CurrentUser, DBSession, ManagerUser
from app.models.bms_sync_log import BmsSyncLog
from app.models.scrm_sync_log import ScrmSyncLog
from app.schemas.common import R
from app.services.bms_fetcher import (
    BMS_DATA_SOURCES,
    BmsFetcher,
    _storage_state_path,
    _try_auto_login_via_ocr,
)
from app.services.bms_scheduler import bms_scheduler
from app.services.bms_sync import get_sync_status, sync_all
from app.services.captcha_session import (
    CaptchaSession,
    bytes_to_data_uri,
    captcha_session_manager,
    try_ocr_captcha,
    try_ocr_math_captcha,
)
from app.services.scrm_client import SCRM_DATA_SOURCES
from app.services.scrm_sync_service import (
    DATA_TYPE_OPERATOR,
    DATA_TYPE_SESSION_GROUP,
    DATA_TYPE_SESSION_PRIVATE,
    DATA_TYPE_SESSION_VIRTUAL,
    get_scrm_sync_status,
    sync_scrm_all,
)
from app.services.sync_source_config_service import get_sync_source_config
from app.services.sync_task_manager import sync_task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fetch", tags=["数据中心-BMS同步"])

# 东八区
TZ_CN = timezone(timedelta(hours=8))

# BMS 可同步数据类型(手动触发/定时校验用)
# 含五源扩容新增的 3 个 Playwright 源(BMS_DATA_SOURCES 里有但非 default_sync 也可手动勾选)
BMS_VALID_TYPES = {
    "refund", "shanmao_refund", "ticket", "wallet", "device_combo",
    "cs_record", "device_tag", "device_tag_binding",
    "refund_record_report", "bms_order", "return_tracking", "device_profile",
}


def _compute_time_range(
    range_type: str,
    custom_start: datetime | None = None,
    custom_end: datetime | None = None,
) -> tuple[datetime, datetime]:
    """根据预设类型计算时间范围(东八区)"""
    now = datetime.now(TZ_CN)
    if range_type == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now
    elif range_type == "yesterday":
        y = now - timedelta(days=1)
        start = y.replace(hour=0, minute=0, second=0, microsecond=0)
        end = y.replace(hour=23, minute=59, second=59, microsecond=0)
        return start, end
    elif range_type == "week":
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now
    elif range_type == "custom":
        if not custom_start or not custom_end:
            raise HTTPException(status_code=400, detail="自定义时间范围需提供 custom_start 和 custom_end")
        return custom_start, custom_end
    else:
        raise HTTPException(status_code=400, detail=f"不支持的时间范围类型: {range_type}")


# ---------- 请求/响应模型 ----------


class SyncAllRequest(BaseModel):
    """一键同步请求"""

    data_types: list[str] | None = Field(None, description="要同步的数据源,默认勾选的源。可选: refund/ticket/wallet/device_combo/cs_record")
    time_range: str = Field("incremental", description="时间范围预设: incremental(增量接续上次,默认)/today/yesterday/week/custom")
    custom_start: datetime | None = Field(None, description="自定义开始时间(time_range=custom 时生效)")
    custom_end: datetime | None = Field(None, description="自定义结束时间(time_range=custom 时生效)")
    force: bool = Field(False, description="强制执行(绕过冷却和正在执行的检查,谨慎使用)")
    ignore_cooldown: bool = Field(False, description="忽略冷却时间(仍防并发)")


class SubmitResponse(BaseModel):
    task_id: str
    message: str


class TaskStatusResponse(BaseModel):
    task_id: str | None
    busy: bool
    cooldown_remaining: int
    task: dict | None
    # 全局读写并发辅助字段(前端可据此显示黄条/屏蔽刷新等)
    is_syncing: bool
    is_writing_heavy: bool
    last_success_at: str | None
    current_phase: str | None
    current_source: str | None
    percent: int | None


class PreflightSource(BaseModel):
    data_type: str
    name: str
    last_sync_end: str | None = None
    start_time: str
    end_time: str


class PreflightResponse(BaseModel):
    busy: bool
    current_task_id: str | None
    current_trigger: str | None
    cooldown_remaining: int
    cooldown_minutes: int
    time_ranges: list[PreflightSource]
    has_credentials: dict[str, bool]


class SyncResultItem(BaseModel):
    data_type: str
    name: str
    success: bool
    file_path: str | None = None
    filename: str | None = None
    total_rows: int | None = None
    inserted_rows: int | None = None
    updated_rows: int | None = None
    failed_rows: int | None = None
    error: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class SyncAllResponse(BaseModel):
    results: list[SyncResultItem]
    total_success: int
    total_count: int


class SyncLogOut(BaseModel):
    id: int
    data_type: str
    start_time: str
    end_time: str
    status: str
    trigger_type: str
    triggered_by: str | None = None
    file_path: str | None = None
    filename: str | None = None
    total_rows: int | None = None
    inserted_rows: int | None = None
    updated_rows: int | None = None
    failed_rows: int | None = None
    error: str | None = None
    created_at: str
    finished_at: str | None = None


class DataSourceStatus(BaseModel):
    data_type: str
    name: str
    url: str | None = None
    has_credentials: bool = False
    default_sync: bool = True
    enabled: bool = True
    requires_captcha: bool = False  # True 表示需验证码(如闪猫退款):同步设置页有验证码登录入口
    last_sync: str | None = None
    last_status: str | None = None
    last_query_end: str | None = None  # 上次查询截止时间(数据已同步至此时间点)
    last_rows: int | None = None


class ScheduleStatus(BaseModel):
    enabled: bool
    # #51: 槽位体系(新). schedule_hours/daytime_hours 保留为兼容字段
    #   —— 旧前端按 "h:00" 渲染, 不宜直接删除(否则前后端必须同时发布)
    slots: list[str] | None = None
    slot_count: int | None = None
    start_hour: int | None = None
    end_hour: int | None = None
    interval_minutes: int | None = None
    next_slot: str | None = None
    schedule_hours: list[int]
    daytime_hours: list[int] | None = None
    catchup_hour: int | None = None
    next_run: str | None = None
    current_progress: dict | None = None
    has_credentials: Any  # bool 或 {"bms":bool, "scrm":bool}(新版)
    busy: bool | None = None
    cooldown_remaining: int | None = None
    is_halted: bool | None = None
    halted_biz_date: str | None = None
    halted_types: list[str] | None = None
    halted_count: int | None = None


class ScheduleConfigIn(BaseModel):
    """高频节拍配置入参(#51)"""

    start_hour: int = Field(..., description="高频时段起(含), 0~23")
    end_hour: int = Field(..., description="高频时段止(不含), 1~24; 24 = 至当日 24:00")
    interval_minutes: int = Field(..., description="采集间隔(分钟), 5~180")


# ---------- API 端点 ----------


@router.get("/data-sources", response_model=R[list[DataSourceStatus]], summary="获取可同步的数据源列表")
def list_sync_data_sources(db: DBSession, _: CurrentUser):
    """获取所有 BMS 可同步数据源及其最近同步状态(凭证/开关来自 sync_source_configs)"""
    statuses = get_sync_status(db)
    result = [
        DataSourceStatus(
            data_type=s["data_type"],
            name=s["name"],
            url=s.get("url"),
            has_credentials=s.get("has_credentials", False),
            default_sync=s.get("default_sync", True),
            enabled=s.get("enabled", True),
            requires_captcha=s.get("requires_captcha", False),
            last_sync=s.get("last_sync"),
            last_status=s.get("last_status"),
            last_query_end=s.get("last_end_time"),
            last_rows=s.get("last_rows"),
        )
        for s in statuses
    ]
    return R(data=result)


@router.post("/sync-all", response_model=R[SyncAllResponse | SubmitResponse], summary="一键同步 BMS 数据(兼容旧版)")
async def sync_all_data(payload: SyncAllRequest, current_user: CurrentUser):
    """同步 BMS 数据 - 兼容旧版前端

    新版前端请使用 POST /task/submit + GET /task/status 模式。
    本接口保留以兼容老前端调用方式,内部逻辑与 submit 保持一致(异步返回 task_id,
    若前端未升级,这里为了兼容仍返回 task 执行完成后的 SyncAllResponse,
    通过在后端侧等待最长 8 分钟实现)。
    """
    valid_types = BMS_VALID_TYPES
    data_types = payload.data_types if payload.data_types else None
    if data_types:
        for dt in data_types:
            if dt not in valid_types:
                raise HTTPException(status_code=400, detail=f"不支持的数据类型: {dt}, 可选: {valid_types}")

    # 走新的 submit 逻辑,返回 task_id(新版前端直接用)
    submit_resp = await _do_submit(payload, current_user)
    task_id = submit_resp.task_id

    # 兼容旧前端: 等待最多 480 秒,完成后返回 SyncAllResponse
    waited = 0
    while waited < 480:
        task = sync_task_manager.get_task(task_id)
        if task and task.status in ("success", "failed", "partial"):
            results = task.results or []
            items = [SyncResultItem(**r) for r in results]
            success_count = sum(1 for r in results if r.get("success"))
            return R(data=SyncAllResponse(
                results=items, total_success=success_count, total_count=len(items),
            ))
        await asyncio.sleep(2)
        waited += 2
    # 超时了,直接返回 task_id 让前端知道
    return R(data=submit_resp)


# ---------- 新版 Task 管理接口 ----------

@router.get("/task/preflight", response_model=R[PreflightResponse], summary="同步前预检:增量时间/冷却/忙闲")
def task_preflight(db: DBSession, _: CurrentUser, data_types: str = ""):
    """
    进入同步页面时或点击同步前调用,返回:
     - 是否有任务在跑(防重)
     - 当前任务是谁触发的
     - 冷却剩余秒数
     - 若现在执行(增量模式),每个源会抓从什么时间~什么时间
     - 各源凭证状态
    """
    dts = [s for s in data_types.split(",") if s] if data_types else sorted(BMS_VALID_TYPES - {"device_tag", "device_tag_binding"})
    statuses = get_sync_status(db)
    has_credentials: dict[str, bool] = {}
    names: dict[str, str] = {}
    last_end: dict[str, str | None] = {}
    for s in statuses:
        dt = s["data_type"]
        has_credentials[dt] = s.get("has_credentials", False)
        names[dt] = s.get("name", dt)
        last_end[dt] = s.get("last_end_time")

    # 增量范围
    ranges = sync_task_manager.compute_incremental_ranges(db, dts)
    preflight_sources: list[PreflightSource] = []
    for dt in dts:
        start_dt, end_dt, start_disp, end_disp = ranges[dt]
        preflight_sources.append(PreflightSource(
            data_type=dt,
            name=names.get(dt, dt),
            last_sync_end=last_end.get(dt),
            start_time=start_disp,
            end_time=end_disp,
        ))

    busy = sync_task_manager.is_busy()
    ct = sync_task_manager.get_current_task()
    return R(data=PreflightResponse(
        busy=busy,
        current_task_id=ct.task_id if (ct and ct.status == "running") else None,
        current_trigger=(
            (ct.triggered_by or "") + ("(定时)" if ct.trigger_type == "scheduled" else "(手动)")
            if ct and ct.status == "running" else None
        ),
        cooldown_remaining=sync_task_manager.remaining_cooldown_seconds(),
        cooldown_minutes=int(sync_task_manager.cooldown_seconds / 60),
        time_ranges=preflight_sources,
        has_credentials=has_credentials,
    ))


async def _do_submit(payload: SyncAllRequest, current_user) -> SubmitResponse:
    """内部: 提交同步任务到 SyncTaskManager"""
    valid_types = BMS_VALID_TYPES
    data_types = payload.data_types if payload.data_types else [
        dt for dt in valid_types
    ]  # None 时默认全量包含,但在 sync_all 内部会再按 default_sync 过滤?不,这里按默认勾选源
    # 修正: data_types 为 None 时按 DEFAULT_SYNC_TYPES(default_sync=True 的源)
    from app.services.bms_sync import DEFAULT_SYNC_TYPES
    if payload.data_types is None:
        data_types = list(DEFAULT_SYNC_TYPES)
    else:
        for dt in data_types:
            if dt not in valid_types:
                raise HTTPException(status_code=400, detail=f"不支持的数据类型: {dt}")

    # 1. 计算时间范围
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        if payload.time_range == "custom":
            if not payload.custom_start or not payload.custom_end:
                raise HTTPException(status_code=400, detail="自定义时间范围需提供 custom_start 和 custom_end")
            start, end = payload.custom_start, payload.custom_end
            time_ranges_dt = {dt: (start, end) for dt in data_types}
            time_ranges_display = {
                dt: (start.strftime("%m-%d %H:%M:%S") + "(自定义)", end.strftime("%m-%d %H:%M:%S"))
                for dt in data_types
            }
        elif payload.time_range == "today":
            now_cn = datetime.now(TZ_CN)
            start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now_cn
            time_ranges_dt = {dt: (start, end) for dt in data_types}
            time_ranges_display = {
                dt: (start.strftime("%m-%d 00:00:00(今日)"), end.strftime("%m-%d %H:%M:%S"))
                for dt in data_types
            }
        elif payload.time_range == "yesterday":
            y = datetime.now(TZ_CN) - timedelta(days=1)
            start = y.replace(hour=0, minute=0, second=0, microsecond=0)
            end = y.replace(hour=23, minute=59, second=59, microsecond=0)
            time_ranges_dt = {dt: (start, end) for dt in data_types}
            time_ranges_display = {
                dt: (start.strftime("%m-%d 00:00:00(昨日)"), end.strftime("%m-%d 23:59:59"))
                for dt in data_types
            }
        elif payload.time_range == "week":
            now_cn = datetime.now(TZ_CN)
            start = (now_cn - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now_cn
            time_ranges_dt = {dt: (start, end) for dt in data_types}
            time_ranges_display = {
                dt: (start.strftime("%m-%d 00:00(近一周)"), end.strftime("%m-%d %H:%M:%S"))
                for dt in data_types
            }
        else:
            # 默认 incremental
            inc = sync_task_manager.compute_incremental_ranges(db, data_types)
            time_ranges_dt = {dt: (inc[dt][0], inc[dt][1]) for dt in data_types}
            time_ranges_display = {dt: (inc[dt][2], inc[dt][3]) for dt in data_types}
    finally:
        db.close()

    # 2. 构造 runner coroutine(包装 sync_all 的旧同步函数,返回 list[dict])
    async def runner(task_id: str, on_progress_cb) -> list[dict]:
        return await sync_all(
            trigger_type="manual",
            triggered_by=current_user.username,
            on_progress=on_progress_cb,
            data_types=data_types,
            time_ranges=time_ranges_dt,
        )

    # 3. 用户手动发起同步 → 视为用户确认恢复,清除当天熔断
    try:
        bms_scheduler.reset_halt()
    except Exception as _rh_e:
        logger.warning(f"清除熔断标记失败(不影响手动提交): {_rh_e}")

    # 4. 提交到 manager
    try:
        task_id, msg = await sync_task_manager.submit(
            trigger_type="manual",
            triggered_by=current_user.username,
            data_types=data_types,
            time_ranges=time_ranges_dt,
            time_ranges_display=time_ranges_display,
            runner_coro=runner,
            force=payload.force,
            ignore_cooldown=payload.ignore_cooldown or payload.time_range in ("custom", "yesterday", "week"),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return SubmitResponse(task_id=task_id, message=msg)


@router.post("/task/submit", response_model=R[SubmitResponse], summary="[新版]异步提交同步任务")
async def task_submit(payload: SyncAllRequest, current_user: CurrentUser):
    """提交同步任务,立即返回 task_id,请轮询 GET /task/status?task_id=... 获取进度"""
    resp = await _do_submit(payload, current_user)
    return R(data=resp)


@router.get("/task/status", response_model=R[TaskStatusResponse], summary="[新版]查询同步任务进度")
def task_status(_: CurrentUser, task_id: str | None = None):
    """
    - task_id 为空: 返回当前(最近一次)任务,但若为终态且超过5分钟则不返回(避免每次打开页面都显示历史"智能助手处理记录导入"等日志)
    - task_id 不为空: 返回指定任务
    同时返回全局 busy 状态和冷却秒数,方便前端判断 UI
    """
    task = sync_task_manager.get_task(task_id)
    task_public = None
    if task:
        is_terminal = task.status in ("success", "failed", "partial")
        # 当用户未指定 task_id 时,若是终态且 finished_at 超过 5 分钟,则视为"历史残留"不返回
        # 避免用户一打开页面就看到上次同步的日志(如智能助手处理记录导入),产生"系统总在后台跑任务"的错觉
        if task_id is None and is_terminal and task.finished_at is not None:
            elapsed = (datetime.now(TZ_CN) - task.finished_at.replace(tzinfo=TZ_CN) if task.finished_at.tzinfo is None else datetime.now(TZ_CN) - task.finished_at.astimezone(TZ_CN)).total_seconds()
            if elapsed > 300:
                task = None
        if task is not None:
            task_public = task.to_public_dict()
    gs = sync_task_manager.get_global_status()
    return R(data=TaskStatusResponse(
        task_id=task.task_id if task else None,
        busy=sync_task_manager.is_busy(),
        cooldown_remaining=sync_task_manager.remaining_cooldown_seconds(),
        task=task_public,
        is_syncing=gs["is_syncing"],
        is_writing_heavy=gs["is_writing_heavy"],
        last_success_at=gs["last_success_at"],
        current_phase=gs["current_phase"],
        current_source=gs["current_source"],
        percent=gs["percent"],
    ))


@router.get("/sync-logs", response_model=R[list[SyncLogOut]], summary="获取同步日志")
def list_sync_logs(
    db: DBSession,
    _: CurrentUser,
    data_type: str | None = None,
    limit: int = 50,
):
    """获取 BMS 同步日志列表"""
    stmt = select(BmsSyncLog)
    if data_type:
        stmt = stmt.where(BmsSyncLog.data_type == data_type)
    stmt = stmt.order_by(desc(BmsSyncLog.created_at)).limit(limit)

    logs = list(db.scalars(stmt))
    return R(
        data=[
            SyncLogOut(
                id=log.id,
                data_type=log.data_type,
                start_time=log.start_time.isoformat() if log.start_time else "",
                end_time=log.end_time.isoformat() if log.end_time else "",
                status=log.status,
                trigger_type=log.trigger_type,
                triggered_by=log.triggered_by,
                file_path=log.file_path,
                filename=log.filename,
                total_rows=log.total_rows,
                inserted_rows=log.inserted_rows,
                updated_rows=log.updated_rows,
                failed_rows=log.failed_rows,
                error=log.error,
                created_at=log.created_at.isoformat() if log.created_at else "",
                finished_at=log.finished_at.isoformat() if log.finished_at else None,
            )
            for log in logs
        ]
    )


@router.get("/schedule/status", response_model=R[ScheduleStatus], summary="获取定时同步状态")
def get_schedule_status(_: CurrentUser):
    """获取定时同步调度器状态"""
    status = bms_scheduler.get_status()
    return R(data=ScheduleStatus(**status))


# ─────────────────────────────────────────────────────────────────────
# 高频节拍配置 (#51, A-20260910-006 LOCKED v1.0)
#   用户诉求: "在配置界面我看没法调整" → 本组端点把「时段 + 间隔」开放为可配。
#   低频档(6:00)/5:00 补跑/8:00 全量 **不受本配置影响**。
# ─────────────────────────────────────────────────────────────────────
@router.get("/schedule/config", response_model=R, summary="读取高频节拍配置")
def get_schedule_config(db: DBSession, _: ManagerUser):
    """读当前高频节拍配置(含派生槽位清单, 供前端直接展示)。"""
    from app.services import scheduler_settings_service as sss

    data = sss.to_dict(sss.get_settings(db))
    data["notice"] = (
        "修改后自下一个槽位生效, 无需重启; "
        "低频档(每日 1 次)/凌晨补跑/首槽全量兜底 不受本配置影响。"
    )
    return R(data=data)


@router.put("/schedule/config", response_model=R, summary="更新高频节拍配置(管理员)")
def update_schedule_config(payload: ScheduleConfigIn, db: DBSession, me: AdminUser):
    """更新高频节拍配置。

    校验(非法 → 400, 不静默取默认):
      - 间隔 5~180 分钟(下限依据: 每轮实测约 4.2 分钟, 低于此值必然重叠)
      - 0 ≤ 起始 < 结束 ≤ 24
      - 时段与间隔组合必须产生 ≥1 个采集点
    """
    from app.services import scheduler_settings_service as sss

    data = sss.update_settings(
        db,
        start_hour=payload.start_hour,
        end_hour=payload.end_hour,
        interval_minutes=payload.interval_minutes,
        actor=me.username,
    )
    data["notice"] = "配置已生效(下一槽位起), 无需重启后端。"
    return R(data=data)


@router.post("/schedule/start", response_model=R[ScheduleStatus], summary="启动定时同步")
async def start_schedule(current_user: CurrentUser):
    """启动 BMS + SCRM 定时同步(8-23/0 每小时 + 5:00 补跑 + 8:00 全量兜底)

    前提: BMS 的退款/工单/钱包 或 SCRM 的 clientId/secret 至少一项已在同步设置中配置
    """
    if bms_scheduler.is_running:
        return R(data=ScheduleStatus(**bms_scheduler.get_status()))

    # 检查凭证(新版 has_credentials 是 dict {bms, scrm},至少一个为 True 即可启动)
    status = bms_scheduler.get_status()
    hc = status.get("has_credentials")
    ok_to_start = False
    if isinstance(hc, dict):
        ok_to_start = bool(hc.get("bms") or hc.get("scrm"))
    else:
        ok_to_start = bool(hc)
    if not ok_to_start:
        raise HTTPException(
            status_code=400,
            detail="BMS 和 SCRM 均未配置凭证,请在 系统管理-同步设置 中至少配置一种数据源",
        )

    # 必须在事件循环中调用,因为 bms_scheduler.start() 内部使用 asyncio.create_task
    bms_scheduler.start()
    return R(data=ScheduleStatus(**status))


@router.post("/schedule/stop", response_model=R[ScheduleStatus], summary="停止定时同步")
def stop_schedule(_: CurrentUser):
    """停止 BMS + SCRM 定时同步"""
    bms_scheduler.stop()
    return R(data=ScheduleStatus(**bms_scheduler.get_status()))


# ================================================================
# SCRM (微信聚合平台) 专用同步接口(独立于 BMS Playwright 同步)
# ================================================================

SCRM_VALID_TYPES = {
    DATA_TYPE_OPERATOR, DATA_TYPE_SESSION_PRIVATE, DATA_TYPE_SESSION_GROUP, DATA_TYPE_SESSION_VIRTUAL,
}
SCRM_TYPE_NAME = {
    DATA_TYPE_OPERATOR: "客服操作员(全量快照)",
    DATA_TYPE_SESSION_PRIVATE: "私聊会话(增量)",
    DATA_TYPE_SESSION_GROUP: "群聊会话(增量)",
    DATA_TYPE_SESSION_VIRTUAL: "虚拟会话(增量)",
}


class ScrmSyncRequest(BaseModel):
    sync_types: list[str] | None = Field(None, description=f"要同步的 SCRM 源,默认全量。可选: {sorted(SCRM_VALID_TYPES)}")
    time_range: str = Field("incremental", description="时间范围: incremental(增量接续上次)/today/yesterday/week/custom")
    custom_start: datetime | None = Field(None, description="time_range=custom 时生效")
    custom_end: datetime | None = Field(None, description="time_range=custom 时生效")
    force: bool = Field(False, description="强制执行(绕过冷却和忙锁,谨慎使用)")
    ignore_cooldown: bool = Field(False, description="忽略冷却(仍防并发)")


class ScrmLogOut(BaseModel):
    id: int
    data_type: str
    sync_scope: str | None = None
    start_time: str
    end_time: str
    status: str
    trigger_type: str
    triggered_by: str | None = None
    api_call_count: int = 0
    api_total_reported: int | None = None
    fetched_rows: int = 0
    inserted_rows: int = 0
    updated_rows: int = 0
    failed_rows: int = 0
    reconcile_delta: int | None = None
    error: str | None = None
    created_at: str
    finished_at: str | None = None


@router.get("/scrm/status", response_model=R[list[dict]], summary="[SCRM]查询4个数据源的最近同步状态")
def scrm_status(db: DBSession, _: CurrentUser):
    """返回 4 条 SCRM data_type 最近同步的摘要(类似 BMS get_sync_status)"""
    rows = get_scrm_sync_status(db)
    for r in rows:
        r.setdefault("name", SCRM_TYPE_NAME.get(r["data_type"], r["data_type"]))
    return R(data=rows)


@router.post("/scrm/sync", response_model=R[SubmitResponse], summary="[SCRM]手动提交同步任务")
async def scrm_sync_submit(payload: ScrmSyncRequest, current_user: CurrentUser):
    """提交 SCRM 同步到 SyncTaskManager(与 BMS 共享全局互斥锁 + 冷却)。

    立即返回 task_id,前端使用 GET /task/status?task_id=... 轮询进度。
    """
    # 1. 校验 sync_types
    sync_types = list(payload.sync_types) if payload.sync_types else list(SCRM_VALID_TYPES)
    for t in sync_types:
        if t not in SCRM_VALID_TYPES:
            raise HTTPException(status_code=400, detail=f"不支持的 SCRM 类型: {t},可选: {sorted(SCRM_VALID_TYPES)}")

    # 2. 计算 time_ranges(按 data_type 独立窗口): incremental → scrm_sync_service 内部自己按 last_success 算
    time_ranges_dt: dict[str, tuple[datetime, datetime]] = {}
    time_ranges_display: dict[str, tuple[str, str]] = {}

    if payload.time_range == "incremental":
        # 留空: sync_scrm_all 内部根据 _last_success_end_time 自动增量
        for dt in sync_types:
            time_ranges_display[dt] = ("按 last_success 增量", datetime.now(TZ_CN).strftime("%m-%d %H:%M:%S"))
    else:
        if payload.time_range == "custom":
            if not payload.custom_start or not payload.custom_end:
                raise HTTPException(status_code=400, detail="自定义时间范围需提供 custom_start/custom_end")
            start, end = payload.custom_start, payload.custom_end
            label_s = start.strftime("%m-%d %H:%M:%S(自定义)")
            label_e = end.strftime("%m-%d %H:%M:%S")
        elif payload.time_range == "today":
            now_cn = datetime.now(TZ_CN)
            start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now_cn
            label_s = start.strftime("%m-%d 00:00(今日)")
            label_e = end.strftime("%m-%d %H:%M:%S")
        elif payload.time_range == "yesterday":
            y = datetime.now(TZ_CN) - timedelta(days=1)
            start = y.replace(hour=0, minute=0, second=0, microsecond=0)
            end = y.replace(hour=23, minute=59, second=59, microsecond=0)
            label_s = start.strftime("%m-%d 00:00(昨日)")
            label_e = end.strftime("%m-%d 23:59:59")
        elif payload.time_range == "week":
            now_cn = datetime.now(TZ_CN)
            start = (now_cn - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now_cn
            label_s = start.strftime("%m-%d 00:00(近一周)")
            label_e = end.strftime("%m-%d %H:%M:%S")
        else:
            raise HTTPException(status_code=400, detail=f"不支持的 time_range: {payload.time_range}")
        for dt in sync_types:
            time_ranges_dt[dt] = (start, end)
            time_ranges_display[dt] = (label_s, label_e)

    # 3. runner 包装(调用 sync_scrm_all)
    async def runner(task_id: str, on_progress_cb) -> list[dict]:
        return await sync_scrm_all(
            trigger_type="manual",
            triggered_by=current_user.username,
            time_ranges=time_ranges_dt or None,
            sync_types=sync_types,
            on_progress=on_progress_cb,
        )

    # 4. 用户手动发起 SCRM 同步 → 视为用户确认恢复,清除当天熔断
    try:
        bms_scheduler.reset_halt()
    except Exception as _rh_e:
        logger.warning(f"清除熔断标记失败(不影响手动提交): {_rh_e}")

    # 5. 提交到 task_manager(与 BMS 共享全局互斥 + 冷却)
    try:
        task_id, msg = await sync_task_manager.submit(
            trigger_type="manual",
            triggered_by=current_user.username,
            data_types=sync_types,
            time_ranges=time_ranges_dt,
            time_ranges_display=time_ranges_display,
            runner_coro=runner,
            force=payload.force,
            ignore_cooldown=payload.ignore_cooldown or payload.time_range in ("custom", "yesterday", "week"),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return R(data=SubmitResponse(task_id=task_id, message=msg))


@router.get("/scrm/logs", response_model=R[list[ScrmLogOut]], summary="[SCRM]查询同步日志")
def scrm_sync_logs(
    db: DBSession,
    _: CurrentUser,
    data_type: str | None = None,
    limit: int = 50,
):
    stmt = select(ScrmSyncLog)
    if data_type:
        if data_type not in SCRM_VALID_TYPES:
            raise HTTPException(status_code=400, detail=f"不支持的 data_type: {data_type}")
        stmt = stmt.where(ScrmSyncLog.data_type == data_type)
    stmt = stmt.order_by(desc(ScrmSyncLog.created_at)).limit(limit)
    logs = list(db.scalars(stmt))
    return R(data=[
        ScrmLogOut(
            id=log.id,
            data_type=log.data_type,
            sync_scope=log.sync_scope,
            start_time=log.start_time.isoformat() if log.start_time else "",
            end_time=log.end_time.isoformat() if log.end_time else "",
            status=log.status,
            trigger_type=log.trigger_type,
            triggered_by=log.triggered_by,
            api_call_count=log.api_call_count,
            api_total_reported=log.api_total_reported,
            fetched_rows=log.fetched_rows,
            inserted_rows=log.inserted_rows,
            updated_rows=log.updated_rows,
            failed_rows=log.failed_rows,
            reconcile_delta=log.reconcile_delta,
            error=log.error,
            created_at=log.created_at.isoformat() if log.created_at else "",
            finished_at=log.finished_at.isoformat() if log.finished_at else None,
        )
        for log in logs
    ])


@router.get("/test", response_model=R[bool], summary="测试浏览器可用性")
async def test_browser(_: CurrentUser):
    """测试 Playwright 浏览器是否可用

    需登录鉴权: 该接口会拉起 Chromium 实例, 未认证开放可被无限并发触发导致 DoS/OOM。
    """
    async with BmsFetcher() as fetcher:
        result = await fetcher.test_connection()
    return R(data=result)


# ---------- 验证码交互登录接口(用于 requires_captcha=True 的数据源,如闪猫退款) ----------


class CaptchaLoginCheckResponse(BaseModel):
    data_type: str
    has_storage_state: bool
    state_mtime: str | None = Field(None, description="登录态保存时间(ISO)")
    state_path: str | None = None


class CaptchaLoginStartRequest(BaseModel):
    data_type: str = Field(..., description="需验证码登录的数据源,如: shanmao_refund")
    auto_ocr: bool = Field(
        True,
        description="是否启用自动 OCR 识别+重试登录(默认 true).关闭后仅截图返回给用户手动输入",
    )


class CaptchaLoginStartResponse(BaseModel):
    session_id: str = Field(..., description="会话ID,后续 submit 时使用")
    captcha_image: str = Field(..., description="验证码图片(data:image/png;base64,...)")
    login_url: str = Field(..., description="当前登录页URL(便于调试)")
    hint: str | None = Field(None, description="提示信息")
    captcha_type: str = Field(
        "text",
        description="验证码类型: text=普通字母数字(闪猫), math=数学计算式(SCRM,需提交计算结果)",
    )
    # 自动 OCR 识别字段(新):若 OCR 成功且自动登录通过,前端无需再弹窗让用户输入
    auto_login_attempted: bool = Field(False, description="后端是否尝试了自动 OCR 识别登录")
    auto_login_success: bool = Field(False, description="自动 OCR 识别+登录是否成功")
    ocr_recognized_text: str | None = Field(None, description="OCR 识别出的文本(math 类型=计算后的答案数字,例如 '0')")
    auto_login_message: str | None = Field(None, description="自动登录成功/失败的说明")
    storage_state_saved: bool = Field(False, description="若自动登录成功,storage_state 是否已写入磁盘")
    state_path: str | None = Field(None, description="若自动登录成功,对应 storage_state 路径")


class CaptchaLoginSubmitRequest(BaseModel):
    session_id: str = Field(..., description="start 接口返回的 session_id")
    captcha: str = Field(..., description="用户识别出的验证码文本")


class CaptchaLoginSubmitResponse(BaseModel):
    success: bool
    storage_state_saved: bool = False
    message: str
    state_path: str | None = None


class CaptchaLoginLogoutRequest(BaseModel):
    data_type: str = Field(..., description="数据源编码,如 shanmao_refund")


# ---- 验证码登录共享逻辑:填账号密码+验证码+点登录+保存 storage_state ----
async def _captcha_do_fill_and_login(
    sess: CaptchaSession,
    captcha: str,
) -> tuple[bool, bool, str, str | None]:
    """执行验证码登录的核心步骤(被自动 OCR 路径与人工 submit 路径复用)。

    Returns:
      (success: bool, storage_state_saved: bool, message: str, state_path: str|None)
    """
    page = sess.page
    log = __import__("logging").getLogger(__name__)
    success = False
    state_saved = False
    state_path_str: str | None = None
    message = ""

    try:
        captcha = (captcha or "").strip()
        if not captcha:
            raise ValueError("验证码不能为空")
        # 填账号
        user_sel = 'input[type="text"], input[placeholder*="账号" i], input[placeholder*="用户名" i], input[placeholder*="手机号" i]'
        try:
            await page.locator(user_sel).first.fill(sess.username, timeout=10000)
        except Exception:
            # 兜底: 找第一个可见文本 input
            await page.locator("input:visible").first.fill(sess.username, timeout=10000)
        # 填密码
        pwd_sel = 'input[type="password"], input[placeholder*="密码" i]'
        await page.locator(pwd_sel).first.fill(sess.password, timeout=10000)
        # 填验证码
        captcha_sel = (
            '.el-form-item:has-text("验证码") input:visible,'
            '.el-form-item:has-text("校验码") input:visible,'
            'input[placeholder*="验证码" i], input[placeholder*="校验码" i], input[placeholder*="captcha" i]'
        )
        try:
            await page.locator(captcha_sel).first.fill(captcha, timeout=8000)
        except Exception as e:
            log.warning(f"[CaptchaLogin] 按选择器填验证码失败,尝试找第三个可见 input: {e}")
            # 兜底: 可见 input 的第三个(账号/密码/验证码顺序)
            vis = page.locator("input:visible")
            count = await vis.count()
            if count >= 3:
                await vis.nth(2).fill(captcha, timeout=5000)
            else:
                raise

        await asyncio.sleep(0.8)

        # 点击登录按钮(优先匹配含"登录"/"sign in"文字的按钮)
        login_btn_sel = (
            'button:has-text("登录"), button:has-text("登 录"), button:has-text("Sign in"), button:has-text("LOGIN"),'
            'button.el-button--primary, button[type="submit"], .login-btn'
        )
        btn = page.locator(login_btn_sel).first
        await btn.click(timeout=8000)

        # 等待跳转: 成功会离开 /login 路由
        try:
            await page.wait_for_url(
                lambda u: "login" not in u.lower().split("#")[-1] if "#" in u else "login" not in u.lower(),
                timeout=15000,
            )
        except Exception as e:
            # 没跳: 大概率验证码错/账号错,尝试抓取页面上的错误提示
            err_text = ""
            try:
                err_loc = page.locator(
                    '.el-message, .el-form-item__error, .el-message-box__message, .ant-message-error'
                ).first
                if await err_loc.count() > 0 and await err_loc.is_visible():
                    err_text = await err_loc.inner_text(timeout=1500)
            except Exception:
                pass
            if not err_text:
                # 兜底取页面上可见错误
                try:
                    whole = await page.inner_text("body", timeout=1500)
                    for kw in ["验证码", "错误", "失败", "密码", "用户名"]:
                        if kw in whole:
                            idx = whole.find(kw)
                            err_text = whole[max(0, idx - 20): idx + 40]
                            break
                except Exception:
                    pass
            raise RuntimeError(
                f"登录失败: {err_text or '未跳转,可能验证码错误或账号密码错误'}"
            ) from e

        success = True
        log.info(f"[CaptchaLogin] 登录成功,正在保存 storage_state: {sess.state_path}")
        # 多等一会儿等资源加载(更完整的 cookie/localStorage)
        await asyncio.sleep(3)
        await sess.context.storage_state(path=str(sess.state_path))
        state_saved = True
        state_path_str = str(sess.state_path)
        message = "登录成功,登录态已保存,后续同步将自动复用此登录态"

    except Exception as e:
        message = str(e)
        log.error(f"[CaptchaLogin] 执行填单登录失败: {e}", exc_info=True)

    return success, state_saved, message, state_path_str


@router.get("/captcha-login/check", response_model=R[CaptchaLoginCheckResponse], summary="检查验证码登录态文件是否存在")
def captcha_login_check(data_type: str, _: CurrentUser, db: DBSession):
    """检查 storage_state 是否存在(首次进入同步设置页时调用)。兼容 BMS + SCRM。"""
    cfg = get_sync_source_config(db, data_type)
    if not cfg or not cfg.get("requires_captcha"):
        raise HTTPException(status_code=400, detail=f"数据源 {data_type} 当前配置不需要验证码登录")
    state_path = _storage_state_path(data_type)
    has_state = state_path.exists()
    mtime = None
    if has_state:
        ts = state_path.stat().st_mtime
        mtime = datetime.fromtimestamp(ts, TZ_CN).isoformat()
    return R(data=CaptchaLoginCheckResponse(
        data_type=data_type,
        has_storage_state=has_state,
        state_mtime=mtime,
        state_path=str(state_path) if has_state else None,
    ))


@router.post("/captcha-login/start", response_model=R[CaptchaLoginStartResponse], summary="开始验证码登录:启动浏览器并截图验证码")
async def captcha_login_start(payload: CaptchaLoginStartRequest, _: CurrentUser, db: DBSession):
    """
    第 1 步:启动 Playwright,导航到登录页,定位验证码图片并截图返回。
    前端拿到 captcha_image 后展示给用户,用户肉眼识别后调用 /submit 提交。

    兼容 BMS(text 验证码+闪猫自动重试) 与 SCRM(math 数学计算式验证码,OCR 后返回计算答案供用户确认)。
    """
    data_type = payload.data_type
    cfg = get_sync_source_config(db, data_type)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"未配置数据源 {data_type},请在同步设置中填写账号密码")
    if not cfg.get("requires_captcha"):
        raise HTTPException(
            status_code=400,
            detail=f"数据源 {data_type} 当前模式({cfg.get('fetch_mode')})不需要验证码登录。请先在同步设置中切换为 Playwright 模式。"
        )
    if not cfg.get("username") or not cfg.get("password"):
        raise HTTPException(status_code=400, detail=f"数据源 {data_type} 未配置账号密码")

    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Playwright 不可用: {e}")

    captcha_type: str = cfg.get("captcha_type") or "text"
    options: dict = cfg.get("options") or {}
    base_url = cfg["base_url"] or (BMS_DATA_SOURCES.get(data_type) or {}).get("base_url", "")
    seed = BMS_DATA_SOURCES.get(data_type) or SCRM_DATA_SOURCES.get(data_type) or {}

    # 登录地址推导: BMS = base/index.html#/login, SCRM = base + options.login_url_path(/login)
    if cfg.get("fetch_mode") == "openapi":
        raise HTTPException(status_code=400, detail="OpenAPI 模式不需要网页登录;请切换到 Playwright 模式")
    if data_type.startswith("scrm_"):
        login_url_path = options.get("login_url_path") or "/login"
        login_url = base_url.rstrip("/") + login_url_path
    else:
        login_url = f"{base_url}/index.html#/login"
    state_path = _storage_state_path(data_type)

    sess = await captcha_session_manager.create(
        data_type=data_type,
        base_url=base_url,
        # page_path 从种子配置读取(BMS/SCRM 的 DATA_SOURCES 均有该字段)
        page_path=seed.get("page_path") or "",
        username=cfg["username"],
        password=cfg["password"],
        state_path=state_path,
    )

    try:
        pw = await async_playwright().start()
        sess.playwright = pw
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                  "--window-size=1920,1080"],
        )
        sess.browser = browser
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        sess.context = context
        page = await context.new_page()
        sess.page = page

        # 导航到登录页
        logger = __import__("logging").getLogger(__name__)
        logger.info(f"[CaptchaLogin] 导航到登录页: {login_url}")
        await page.goto(login_url, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(2)

        # ── 定位验证码图片并截图 ──
        # 策略: 先找常见验证码元素(img 或 canvas,src 包含 captcha/code;或旁边有"验证码"标签)
        captcha_elem = None
        # 1) 找 img[src 包含 captcha|code|vcode]
        for sel in [
            'img[src*="captcha" i]',
            'img[src*="vcode" i]',
            'img[src*="verify" i]',
            'img[src*="code" i]',
            'canvas[class*="captcha" i]',
            'canvas[id*="captcha" i]',
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    captcha_elem = loc
                    break
            except Exception:
                continue
        # 2) 兜底: 找含"验证码"文字的表单项下的 img/canvas
        if not captcha_elem:
            try:
                form_item = page.locator(".el-form-item:has-text('验证码'), .el-form-item:has-text('校验码')").first
                if await form_item.count() > 0:
                    for sel in ["img", "canvas"]:
                        loc = form_item.locator(sel).first
                        if await loc.count() > 0 and await loc.is_visible():
                            captcha_elem = loc
                            break
            except Exception:
                pass
        # 3) 最后兜底: 取整个登录区域截图(让用户能看到验证码)
        captcha_png = None
        if captcha_elem:
            try:
                captcha_png = await captcha_elem.screenshot(type="png")
                logger.info(f"[CaptchaLogin] 已截取验证码图片: {len(captcha_png)} 字节")
            except Exception as e:
                logger.warning(f"[CaptchaLogin] 验证码元素截图失败: {e}")
        if not captcha_png:
            # 取登录表单所在区域/整个页面可视区截图
            logger.warning("[CaptchaLogin] 未定位到验证码元素,改为页面截图")
            try:
                form = page.locator(".login-container, .login-form, form, .el-form").first
                if await form.count() > 0 and await form.is_visible():
                    captcha_png = await form.screenshot(type="png")
                else:
                    captcha_png = await page.screenshot(type="png", full_page=False)
            except Exception as e:
                captcha_png = await page.screenshot(type="png", full_page=False)
                logger.warning(f"[CaptchaLogin] 页面截图: {e}")

        # ── 自动 OCR 识别尝试(优先):最多 N 次重试 ──
        auto_attempted = False
        auto_success = False
        ocr_text: str | None = None
        auto_msg: str | None = None
        autostate_saved = False
        autostate_path: str | None = None
        if captcha_type == "math":
            base_hint = (
                "请输入图片中数学计算式的计算结果(例如 7*0=? 的答案是 0, 9-9=? 的答案是 0)。"
                "OCR 已自动算出答案,请核对图片后直接点提交即可。"
                if captcha_elem
                else "未定位到验证码图片,请从截图中自行计算数学题的答案并输入"
            )
        else:
            base_hint = (
                "请输入图片中显示的验证码(不区分大小写可先尝试)"
                if captcha_elem
                else "未定位到验证码元素,已截整个登录区域,请从中识别验证码"
            )

        # 只在用户勾选「自动识别」(payload.auto_ocr=True) 且有验证码图片时,走自动重试
        if payload.auto_ocr and captcha_png:
            auto_attempted = True
            if captcha_type == "math":
                # SCRM 数学验证码: OCR 直接返回计算好的答案数字字符串
                ocr_result = try_ocr_math_captcha(captcha_png)
                ocr_text = ocr_result
                logger.info(
                    f"[CaptchaLogin-Math] 数学验证码 OCR 完成: 答案={ocr_result!r};"
                    f" 进入人工确认/提交流程(不尝试自动 fill,保持与 text 一致的交互路径)"
                )
                auto_msg = (
                    f"OCR 计算答案是 '{ocr_result}',请核对图片后直接点提交(若识别错可手动修改)"
                    if ocr_result
                    else "OCR 未能识别此算式,请肉眼计算后手动输入答案"
                )
                # math 类型不调用 _try_auto_login_via_ocr(那是 BMS 专用的):
                #   - 让用户看到答案 → 点 submit → 走通用 _captcha_do_fill_and_login 路径(也支持 math)
                #   - 这样保证与 text 类型交互一致,也方便人工修正
            else:
                # BMS text 类型: 原闪猫自动重试逻辑
                ocr_result = try_ocr_captcha(captcha_png)
                ocr_text = ocr_result  # 先把第一次识别结果返回给前端(便于排查)
                logger.info(
                    f"[CaptchaLogin-Text] 触发自动 OCR 登录模式(调用 _try_auto_login_via_ocr,最多 8 次重试);"
                    f" 首次粗略识别={ocr_result!r}"
                )
                ok, msg = await _try_auto_login_via_ocr(
                    page=page,
                    context=context,
                    data_type=data_type,
                    login_url=login_url,
                    username=sess.username,
                    password=sess.password,
                    state_path=sess.state_path,
                )
                auto_success = ok
                auto_msg = msg
                autostate_saved = bool(sess.state_path.exists()) if ok else False
                autostate_path = str(sess.state_path) if autostate_saved else None
                if ok:
                    logger.info(f"[CaptchaLogin-Text] 自动 OCR 登录成功:{msg}")
                    await captcha_session_manager.remove(sess.session_id)
                    return R(data=CaptchaLoginStartResponse(
                        session_id=sess.session_id,
                        captcha_image=bytes_to_data_uri(captcha_png),
                        login_url=login_url,
                        hint=base_hint + " (后端已自动识别并登录成功,无需输入)",
                        captcha_type=captcha_type,
                        auto_login_attempted=True,
                        auto_login_success=True,
                        ocr_recognized_text=ocr_text,
                        auto_login_message=auto_msg,
                        storage_state_saved=autostate_saved,
                        state_path=autostate_path,
                    ))
                else:
                    logger.warning(f"[CaptchaLogin-Text] 自动 OCR 登录 {_try_auto_login_via_ocr.__name__} 失败,转人工兜底: {msg}")
                    # 保留会话,但刷新一次登录页+重新截验证码图片(保证用户手动输入时用的是新验证码)
                    try:
                        await page.goto(login_url, wait_until="networkidle", timeout=60000)
                        await asyncio.sleep(2)
                        if captcha_elem:
                            try:
                                cap2 = await captcha_elem.screenshot(type="png")
                                if cap2 and len(cap2) > 100:
                                    captcha_png = cap2
                            except Exception:
                                pass
                    except Exception as re:
                        logger.warning(f"[CaptchaLogin-Text] 刷新登录页失败: {re}")

        # 无 OCR / math 类型 / OCR 登录失败 → 进入人工输入兜底流程(标准原流程)
        final_hint = base_hint
        if auto_attempted and not auto_success and auto_msg:
            final_hint = f"自动识别提示: {auto_msg} —— 请手动确认/输入答案后提交"
        return R(data=CaptchaLoginStartResponse(
            session_id=sess.session_id,
            captcha_image=bytes_to_data_uri(captcha_png),
            login_url=login_url,
            hint=final_hint,
            captcha_type=captcha_type,
            auto_login_attempted=auto_attempted,
            auto_login_success=auto_success,
            ocr_recognized_text=ocr_text,
            auto_login_message=auto_msg,
            storage_state_saved=autostate_saved,
            state_path=autostate_path,
        ))
    except Exception as e:
        await captcha_session_manager.remove(sess.session_id)
        raise HTTPException(status_code=500, detail=f"启动验证码登录失败: {e}")


@router.post("/captcha-login/submit", response_model=R[CaptchaLoginSubmitResponse], summary="提交验证码:填充并登录,成功后保存 storage_state")
async def captcha_login_submit(payload: CaptchaLoginSubmitRequest, _: CurrentUser):
    """
    第 2 步:用户提交识别的验证码 → 后端填账号密码+验证码 → 提交登录 → 成功后保存 storage_state。
    后续 BmsFetcher 抓数时会自动加载此 storage_state 跳过登录。
    """
    sess = captcha_session_manager.get(payload.session_id)
    if not sess:
        raise HTTPException(status_code=400, detail="会话不存在或已过期,请重新开始")
    if not sess.page:
        await captcha_session_manager.remove(payload.session_id)
        raise HTTPException(status_code=400, detail="浏览器会话已失效,请重新开始")

    captcha = (payload.captcha or "").strip()
    if not captcha:
        raise HTTPException(status_code=400, detail="验证码不能为空")

    log = __import__("logging").getLogger(__name__)
    try:
        success, state_saved, message, state_path_str = await _captcha_do_fill_and_login(sess, captcha)
    except Exception as e:
        log.error(f"[CaptchaLogin] submit 异常: {e}", exc_info=True)
        success, state_saved, message, state_path_str = False, False, str(e), None
    finally:
        # 无论成功失败都销毁会话(Playwright 资源)
        await captcha_session_manager.remove(payload.session_id)

    return R(data=CaptchaLoginSubmitResponse(
        success=success,
        storage_state_saved=state_saved,
        message=message,
        state_path=state_path_str,
    ))


@router.post("/captcha-login/logout", response_model=R[CaptchaLoginCheckResponse], summary="清除验证码登录态(强制下次重新登录)")
def captcha_login_logout(payload: CaptchaLoginLogoutRequest, _: CurrentUser, db: DBSession):
    cfg = get_sync_source_config(db, payload.data_type)
    if not cfg or not cfg.get("requires_captcha"):
        raise HTTPException(
            status_code=400,
            detail=f"数据源 {payload.data_type} 当前模式({cfg.get('fetch_mode') if cfg else ''})不需要验证码登录",
        )
    state_path = _storage_state_path(payload.data_type)
    if state_path.exists():
        try:
            state_path.unlink()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"删除登录态文件失败: {e}")
    return R(data=CaptchaLoginCheckResponse(
        data_type=payload.data_type,
        has_storage_state=False,
        state_mtime=None,
        state_path=None,
    ))
