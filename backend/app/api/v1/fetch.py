"""BMS 数据同步 API

2026-08-02 优化: 引入 SyncTaskManager 管理长任务
  - POST /task/submit   异步提交同步任务(立即返回 task_id)
  - GET  /task/status   查询任务进度(轮询用)
  - GET  /task/preflight 预检:是否可执行/增量时间范围/冷却剩余
  - POST /sync-all      (旧接口,兼容旧前端)内部改为走新 submit 模式
"""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.api.deps import CurrentUser, DBSession
from app.models.bms_sync_log import BmsSyncLog
from app.schemas.common import R
from app.services.bms_fetcher import BmsFetcher
from app.services.bms_scheduler import bms_scheduler
from app.services.bms_sync import get_sync_status, sync_all
from app.services.sync_task_manager import sync_task_manager

router = APIRouter(prefix="/fetch", tags=["数据中心-BMS同步"])

# 东八区
TZ_CN = timezone(timedelta(hours=8))


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
    last_sync: str | None = None
    last_status: str | None = None
    last_query_end: str | None = None  # 上次查询截止时间(数据已同步至此时间点)
    last_rows: int | None = None


class ScheduleStatus(BaseModel):
    enabled: bool
    schedule_hours: list[int]
    next_run: str | None = None
    current_progress: dict | None = None
    has_credentials: bool


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
    valid_types = {"refund", "ticket", "wallet", "device_combo", "cs_record"}
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
    dts = [s for s in data_types.split(",") if s] if data_types else [
        "refund", "ticket", "wallet", "device_combo", "cs_record",
    ]
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
    valid_types = {"refund", "ticket", "wallet", "device_combo", "cs_record"}
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

    # 3. 提交到 manager
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
    return R(data=TaskStatusResponse(
        task_id=task.task_id if task else None,
        busy=sync_task_manager.is_busy(),
        cooldown_remaining=sync_task_manager.remaining_cooldown_seconds(),
        task=task_public,
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


@router.post("/schedule/start", response_model=R[ScheduleStatus], summary="启动定时同步")
async def start_schedule(current_user: CurrentUser):
    """启动 BMS 定时同步(8/12/16/20/24 点各执行一次)

    前提: 默认同步的源(refund/ticket/wallet)需在 系统管理-同步设置 中配置账号密码
    """
    if bms_scheduler.is_running:
        return R(data=ScheduleStatus(**bms_scheduler.get_status()))

    # 检查凭证(从 sync_source_configs 判断默认源是否都配了凭证)
    status = bms_scheduler.get_status()
    if not status["has_credentials"]:
        raise HTTPException(
            status_code=400,
            detail="默认同步的源未全部配置凭证,请在 系统管理-同步设置 中配置账号密码",
        )

    # 必须在事件循环中调用,因为 bms_scheduler.start() 内部使用 asyncio.create_task
    bms_scheduler.start()
    return R(data=ScheduleStatus(**status))


@router.post("/schedule/stop", response_model=R[ScheduleStatus], summary="停止定时同步")
def stop_schedule(_: CurrentUser):
    """停止 BMS 定时同步"""
    bms_scheduler.stop()
    return R(data=ScheduleStatus(**bms_scheduler.get_status()))


@router.get("/test", response_model=R[bool], summary="测试浏览器可用性")
async def test_browser():
    """测试 Playwright 浏览器是否可用"""
    async with BmsFetcher() as fetcher:
        result = await fetcher.test_connection()
    return R(data=result)
